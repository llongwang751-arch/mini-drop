"""数据库引擎与会话管理。

通过 DATABASE_URL 环境变量切换后端：
  PostgreSQL: DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db
  SQLite:     DATABASE_URL=sqlite:///mini_drop.db（默认，测试/演示适用）

引擎和 Session factory 通过 _get_engine() / _get_sessionmaker() 延迟创建，
测试代码可以在 import 本模块之前设置 DATABASE_URL 环境变量。
"""

from __future__ import annotations

import os
import threading

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from server.app.models import Base

_engine: Engine | None = None
_sessionmaker: sessionmaker | None = None
# _get_sessionmaker() may initialize the engine while holding this lock, so it
# must be re-entrant in a fresh process where neither singleton exists yet.
_lock = threading.RLock()

_MANAGED_SCHEMA_REVISION = "20260822_0018"
_MANAGED_SCHEMA_TABLES = {
    "alembic_version",
    "tasks",
    "agents",
    "diagnosis_sessions",
    "frozen_diagnosis_artifacts",
    "diagnosis_artifact_outbox",
    "diagnosis_artifact_evaluations",
}
_MANAGED_ARTIFACT_OUTBOX_COLUMNS = {
    "attempts",
    "next_attempt_at",
    "worker_lease_owner",
    "worker_lease_expires_at",
    "last_error",
    "published_at",
}


def _build_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if url:
        return url
    db_file = os.getenv("SQLITE_PATH", "mini_drop.db")
    return f"sqlite:///{db_file}"


def _get_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is not None:
            return _engine
        url = _build_url()
        connect_args: dict = {}
        engine_kwargs: dict = {}
        if "sqlite" in url:
            connect_args["check_same_thread"] = False
            if url in {"sqlite:///:memory:", "sqlite://"}:
                engine_kwargs["poolclass"] = StaticPool
        _engine = create_engine(
            url,
            echo=False,
            pool_pre_ping=True,
            connect_args=connect_args,
            **engine_kwargs,
        )
        return _engine


def _get_sessionmaker() -> sessionmaker:
    global _sessionmaker
    if _sessionmaker is not None:
        return _sessionmaker
    with _lock:
        if _sessionmaker is not None:
            return _sessionmaker
        _sessionmaker = sessionmaker(
            bind=_get_engine(), autoflush=False, autocommit=False,
            expire_on_commit=False,
        )
        return _sessionmaker


def init_db() -> None:
    """创建所有表（幂等）。应用启动时调用一次。"""
    engine = _get_engine()
    if os.getenv("MINI_DROP_SCHEMA_MANAGED", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        missing = sorted(_MANAGED_SCHEMA_TABLES - tables)
        if missing:
            raise RuntimeError(
                "database schema is not migrated; missing tables: " + ", ".join(missing)
            )
        with engine.connect() as connection:
            revisions = {
                str(row[0])
                for row in connection.execute(text("SELECT version_num FROM alembic_version"))
            }
        if revisions != {_MANAGED_SCHEMA_REVISION}:
            rendered = ", ".join(sorted(revisions)) or "<empty>"
            raise RuntimeError(
                "database schema revision mismatch; expected "
                f"{_MANAGED_SCHEMA_REVISION}, found {rendered}"
            )
        artifact_outbox_columns = {
            item["name"]
            for item in inspector.get_columns("diagnosis_artifact_outbox")
        }
        missing_columns = sorted(
            _MANAGED_ARTIFACT_OUTBOX_COLUMNS - artifact_outbox_columns
        )
        if missing_columns:
            raise RuntimeError(
                "database schema is not migrated; diagnosis_artifact_outbox "
                "missing columns: " + ", ".join(missing_columns)
            )
        return
    Base.metadata.create_all(bind=engine)
    _upgrade_legacy_schema(engine)


_ADDITIVE_MIGRATIONS = {
    "tasks": {
        "diagnosis_step_id": "VARCHAR(128)",
        "collection_status": "VARCHAR(16) NOT NULL DEFAULT 'QUEUED'",
        "analysis_status": "VARCHAR(16) NOT NULL DEFAULT 'NOT_STARTED'",
        "deleted_at": "TIMESTAMP",
        "deleted_by": "VARCHAR(128)",
        "delete_reason": "TEXT",
    },
    "diagnosis_sessions": {
        "row_version": "INTEGER NOT NULL DEFAULT 0",
        # Existing rows may not have a meaningful deadline. Keeping the added
        # column nullable is safer than inventing a historical deadline.
        "deadline_at": "TIMESTAMP",
    },
    "diagnosis_probe_executions": {
        "retry_count": "INTEGER NOT NULL DEFAULT 0",
        "error_code": "VARCHAR(128)",
        "error_message": "TEXT",
        "evidence_purpose": "VARCHAR(16) NOT NULL DEFAULT 'VERIFY'",
        "round_index": "INTEGER NOT NULL DEFAULT 1",
    },
    "diagnosis_evidence": {
        "evidence_role": "VARCHAR(32) NOT NULL DEFAULT 'incident'",
    },
    "diagnosis_evidence_snapshots": {
        # Unknown historical provenance remains NULL; never infer the latest attempt.
        "attempt_id": "VARCHAR(128)",
    },
    "drop_insight_sessions": {
        "deleted_at": "TIMESTAMP",
        "deleted_by": "VARCHAR(128)",
        "delete_reason": "TEXT",
    },
    "drop_insight_hypotheses": {
        "source": "VARCHAR(32) NOT NULL DEFAULT 'DETERMINISTIC_RULE'",
        "round_index": "INTEGER NOT NULL DEFAULT 1",
        "parent_hypothesis_id": "VARCHAR(128)",
        "generation_reason": "TEXT NOT NULL DEFAULT ''",
    },
}


def _upgrade_legacy_schema(engine: Engine) -> None:
    """Apply small, additive upgrades needed by pre-v2 SQLite/Postgres installs."""

    with engine.begin() as connection:
        # The API, gRPC control plane and diagnosis worker are independent
        # processes. They may start at the same time on a clean deployment, so
        # schema upgrades must be serialized across processes rather than only
        # guarded by the in-process ``_lock`` above.
        if engine.dialect.name == "postgresql":
            connection.execute(text(
                "SELECT pg_advisory_xact_lock(hashtext('mini_drop_schema_migration'))"
            ))

        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        for table, columns in _ADDITIVE_MIGRATIONS.items():
            if table not in tables:
                continue
            existing = {item["name"] for item in inspector.get_columns(table)}
            for column, declaration in columns.items():
                if column not in existing:
                    if engine.dialect.name == "postgresql":
                        statement = (
                            f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS '
                            f'"{column}" {declaration}'
                        )
                    else:
                        statement = f'ALTER TABLE "{table}" ADD COLUMN "{column}" {declaration}'
                    connection.execute(text(statement))
                    # Keep this inspector snapshot accurate for duplicate
                    # entries in future migration maps.
                    existing.add(column)
        if "tasks" in tables:
            # Columns added to a legacy installation receive their SQL
            # defaults, which would otherwise make historical DONE tasks look
            # like they are still queued. Only repair the untouched default
            # pair, so statuses maintained by the new state machine are never
            # overwritten.
            task_columns = {
                item["name"] for item in inspect(connection).get_columns("tasks")
            }
            if {"status", "collection_status", "analysis_status"}.issubset(task_columns):
                connection.execute(text("""
                    UPDATE tasks
                    SET collection_status = CASE status
                            WHEN 'RUNNING' THEN 'COLLECTING'
                            WHEN 'UPLOADING' THEN 'UPLOADING'
                            WHEN 'ANALYZING' THEN 'SUCCEEDED'
                            WHEN 'DONE' THEN 'SUCCEEDED'
                            WHEN 'FAILED' THEN 'FAILED'
                            WHEN 'CANCELLED' THEN 'CANCELLED'
                            ELSE collection_status
                        END,
                        analysis_status = CASE status
                            WHEN 'ANALYZING' THEN 'QUEUED'
                            WHEN 'DONE' THEN 'SUCCEEDED'
                            WHEN 'FAILED' THEN 'SKIPPED'
                            WHEN 'CANCELLED' THEN 'CANCELLED'
                            ELSE analysis_status
                        END
                    WHERE collection_status = 'QUEUED'
                      AND analysis_status = 'NOT_STARTED'
                      AND status <> 'PENDING'
                """))
            connection.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_tasks_diagnosis_step_id "
                "ON tasks (diagnosis_step_id)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_tasks_deleted_at ON tasks (deleted_at)"
            ))
        if "diagnosis_evidence_snapshots" in tables:
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_diagnosis_evidence_snapshots_attempt_id "
                "ON diagnosis_evidence_snapshots (attempt_id)"
            ))
            if engine.dialect.name == "postgresql":
                invalid = connection.execute(text("""
                    SELECT snapshot.id
                    FROM diagnosis_evidence_snapshots AS snapshot
                    LEFT JOIN task_attempts AS attempt ON attempt.id = snapshot.attempt_id
                    WHERE snapshot.attempt_id IS NOT NULL
                      AND (attempt.id IS NULL OR snapshot.task_id IS NULL
                           OR attempt.task_id <> snapshot.task_id)
                    LIMIT 1
                """)).scalar()
                if invalid is not None:
                    raise RuntimeError(
                        "invalid diagnosis evidence snapshot attempt lineage: "
                        f"{invalid}"
                    )
                foreign_keys = inspect(connection).get_foreign_keys(
                    "diagnosis_evidence_snapshots"
                )
                has_attempt_fk = any(
                    item.get("referred_table") == "task_attempts"
                    and item.get("constrained_columns") == ["attempt_id"]
                    for item in foreign_keys
                )
                if not has_attempt_fk:
                    connection.execute(text(
                        "ALTER TABLE diagnosis_evidence_snapshots "
                        "ADD CONSTRAINT fk_diagnosis_evidence_snapshots_attempt_id "
                        "FOREIGN KEY (attempt_id) REFERENCES task_attempts (id)"
                    ))


def new_session() -> Session:
    """返回一个新的数据库会话。调用方负责 close。"""
    return _get_sessionmaker()()


def reset_engine() -> None:
    """重置引擎和 session factory（测试用，强制下次调用时重建）。"""
    global _engine, _sessionmaker
    with _lock:
        _engine = None
        _sessionmaker = None
