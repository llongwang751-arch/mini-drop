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

_MANAGED_SCHEMA_REVISION = "20260824_0032"
_MANAGED_SCHEMA_TABLES = {
    "alembic_version",
    "tasks",
    "task_attempts",
    "artifacts",
    "analysis_jobs",
    "analysis_job_input_artifacts",
    "analysis_job_output_artifacts",
    "agents",
    "process_candidate_snapshots",
    "process_candidates",
    "diagnosis_sessions",
    "diagnosis_evidence",
    "diagnosis_evidence_reviews",
    "diagnosis_conclusion_invalidations",
    "diagnosis_revalidation_requests",
    "frozen_diagnosis_artifacts",
    "diagnosis_artifact_revocations",
    "diagnosis_artifact_revocation_outbox",
    "diagnosis_evidence_snapshots",
    "diagnosis_artifact_outbox",
    "diagnosis_artifact_evaluations",
    "agent_runtime_bindings",
    "agent_runtime_turns",
    "agent_runtime_events",
    "drop_insight_sessions",
    "drop_insight_target_discoveries",
    "drop_insight_target_bindings",
    "drop_insight_tool_calls",
    "drop_insight_reports",
}
_MANAGED_PROCESS_COLUMNS = {
    "tasks": {"process_snapshot_id", "process_binding_json"},
    "process_candidate_snapshots": {
        "id", "agent_id", "generation", "boot_id", "observed_at_unix_ms",
        "complete", "truncated", "error", "state", "authoritative",
        "received_at",
    },
    "process_candidates": {
        "id", "snapshot_id", "agent_id", "pid", "process_start_ticks",
        "pid_namespace_inode", "namespace_pid", "executable_identity",
        "comm", "cgroup", "service_hint", "instance_hint",
        "collector_capabilities",
    },
    "drop_insight_target_discoveries": {
        "id", "diagnosis_id", "diagnosis_version", "status",
        "service_filter", "environment_filter", "snapshot_state_json",
        "created_at", "expires_at", "invalidated_at",
    },
    "drop_insight_target_bindings": {
        "id", "discovery_id", "agent_id", "process_snapshot_id", "pid",
        "process_binding_json", "display_json",
    },
}
_MANAGED_PROCESS_INDEXES = {
    "tasks": {"ix_tasks_process_snapshot_id"},
    "process_candidate_snapshots": {"ix_process_snapshots_agent_received"},
    "process_candidates": {"ix_process_candidates_snapshot_pid"},
    "drop_insight_target_discoveries": {
        "ix_drop_insight_target_discovery_scope"
    },
    "drop_insight_target_bindings": {
        "ix_drop_insight_target_bindings_discovery"
    },
}
_MANAGED_PROCESS_CONSTRAINTS = {
    "process_candidate_snapshots": {
        "ck_process_snapshot_state", "ck_process_snapshot_generation",
    },
    "process_candidates": {
        "ck_process_candidate_pid", "ck_process_candidate_start_ticks",
        "ck_process_candidate_namespace_inode",
        "ck_process_candidate_namespace_pid",
    },
    "drop_insight_target_discoveries": {
        "ck_drop_insight_target_discovery_status"
    },
}
_MANAGED_PROCESS_IDENTITIES = {
    "process_candidate_snapshots": {("agent_id", "id")},
    "process_candidates": {
        (
            "snapshot_id", "pid", "process_start_ticks",
            "pid_namespace_inode", "namespace_pid", "executable_identity",
        )
    },
    "drop_insight_target_bindings": {("discovery_id", "id")},
}
_MANAGED_PROCESS_FOREIGN_KEYS = {
    "tasks": {("process_snapshot_id", "process_candidate_snapshots", "id")},
    "process_candidate_snapshots": {("agent_id", "agents", "id")},
    "process_candidates": {
        ("snapshot_id", "process_candidate_snapshots", "id"),
        ("agent_id", "agents", "id"),
    },
    "drop_insight_target_discoveries": {
        ("diagnosis_id", "drop_insight_sessions", "id")
    },
    "drop_insight_target_bindings": {
        ("discovery_id", "drop_insight_target_discoveries", "id"),
        ("agent_id", "agents", "id"),
        ("process_snapshot_id", "process_candidate_snapshots", "id"),
    },
}
_MANAGED_TASK_LINEAGE_COLUMNS = {
    "task_attempts": {"task_attempt_authority_sha256"},
    "analysis_jobs": {"task_attempt_id"},
    "artifacts": {"task_attempt_id", "analysis_job_id"},
    "analysis_job_input_artifacts": {
        "id", "analysis_job_id", "artifact_id", "task_id",
        "task_attempt_id", "created_at",
    },
    "analysis_job_output_artifacts": {
        "id", "analysis_job_id", "artifact_id", "task_id",
        "task_attempt_id", "created_at",
    },
}
_MANAGED_TASK_LINEAGE_INDEXES = {
    "analysis_jobs": {
        "ix_analysis_jobs_task_attempt_id": (("task_attempt_id",), False),
    },
    "artifacts": {
        "ix_artifacts_task_attempt_id": (("task_attempt_id",), False),
        "ix_artifacts_analysis_job_id": (("analysis_job_id",), False),
    },
    "analysis_job_input_artifacts": {
        "ix_analysis_job_input_artifacts_analysis_job_id": (
            ("analysis_job_id",), False
        ),
        "ix_analysis_job_input_artifacts_artifact_id": (
            ("artifact_id",), False
        ),
    },
    "analysis_job_output_artifacts": {
        "ix_analysis_job_output_artifacts_analysis_job_id": (
            ("analysis_job_id",), False
        ),
        "ix_analysis_job_output_artifacts_artifact_id": (
            ("artifact_id",), False
        ),
    },
}
_MANAGED_TASK_LINEAGE_IDENTITIES = {
    "task_attempts": {
        ("task_attempt_authority_sha256",),
        ("id", "task_id"),
    },
    "analysis_jobs": {("id", "task_id", "task_attempt_id")},
    "artifacts": {("id", "task_id", "task_attempt_id")},
    "analysis_job_input_artifacts": {
        ("analysis_job_id", "artifact_id")
    },
    "analysis_job_output_artifacts": {
        ("analysis_job_id", "artifact_id")
    },
}
_MANAGED_TASK_LINEAGE_FOREIGN_KEYS = {
    "analysis_jobs": {
        (
            ("task_attempt_id", "task_id"),
            "task_attempts",
            ("id", "task_id"),
        ),
    },
    "artifacts": {
        (
            ("task_attempt_id", "task_id"),
            "task_attempts",
            ("id", "task_id"),
        ),
        (
            ("analysis_job_id", "task_id", "task_attempt_id"),
            "analysis_jobs",
            ("id", "task_id", "task_attempt_id"),
        ),
    },
    "analysis_job_input_artifacts": {
        (
            ("analysis_job_id", "task_id", "task_attempt_id"),
            "analysis_jobs",
            ("id", "task_id", "task_attempt_id"),
        ),
        (
            ("artifact_id", "task_id", "task_attempt_id"),
            "artifacts",
            ("id", "task_id", "task_attempt_id"),
        ),
    },
    "analysis_job_output_artifacts": {
        (
            ("analysis_job_id", "task_id", "task_attempt_id"),
            "analysis_jobs",
            ("id", "task_id", "task_attempt_id"),
        ),
        (
            ("artifact_id", "task_id", "task_attempt_id"),
            "artifacts",
            ("id", "task_id", "task_attempt_id"),
        ),
    },
}
_MANAGED_ARTIFACT_OUTBOX_COLUMNS = {
    "attempts",
    "next_attempt_at",
    "worker_lease_owner",
    "worker_lease_expires_at",
    "last_error",
    "published_at",
}
_MANAGED_SNAPSHOT_PROVENANCE_COLUMNS = {
    "artifact_provenance_json",
    "analysis_provenance_json",
}
_MANAGED_REVOCATION_OUTBOX_INDEXES = {
    "ix_diagnosis_artifact_revocation_outbox_status",
    "ix_diagnosis_artifact_revocation_outbox_due",
    "ix_diagnosis_artifact_revocation_outbox_lease_recovery",
}
_MANAGED_RUNTIME_TURN_RECOVERY_COLUMNS = {
    "recovery_phase",
    "recovery_owner",
    "recovery_lease_expires_at",
    "recovery_fencing_token",
}
_MANAGED_RUNTIME_TURN_RECOVERY_INDEXES = {
    "ix_agent_runtime_turns_recovery_lease",
}
_MANAGED_RUNTIME_TURN_RECOVERY_CONSTRAINTS = {
    "ck_agent_runtime_turn_recovery_phase",
}
_MANAGED_DROP_INSIGHT_COLUMNS = {
    "drop_insight_sessions": {
        "requested_time_range_json",
        "effective_time_range_json",
    },
    "drop_insight_hypotheses": {
        "effect_key",
    },
    "drop_insight_events": {
        "effect_key",
    },
    "drop_insight_tool_calls": {
        "effect_key",
        "terminal_processing_status",
        "terminal_processed_at",
    },
    "drop_insight_reports": {
        "effects_status",
        "effects_phase",
        "effects_owner",
        "effects_lease_expires_at",
        "effects_fencing_token",
        "effects_applied_at",
    },
}
_MANAGED_REPORT_EFFECT_INDEXES = {
    "ix_drop_insight_reports_effects_lease",
}
_MANAGED_REPORT_EFFECT_CONSTRAINTS = {
    "ck_drop_insight_report_effects_status",
    "ck_drop_insight_report_effects_phase",
}
_MANAGED_DROP_INSIGHT_IDENTITIES = {
    "drop_insight_hypotheses": {
        ("diagnosis_id", "effect_key"),
    },
    "drop_insight_events": {
        ("diagnosis_id", "effect_key"),
    },
    "drop_insight_tool_calls": {
        ("diagnosis_id", "effect_key"),
    },
    "drop_insight_reports": {
        ("diagnosis_id", "hypothesis_id"),
    },
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
        for table_name, required_columns in _MANAGED_PROCESS_COLUMNS.items():
            actual_columns = {
                item["name"] for item in inspector.get_columns(table_name)
            }
            missing_process_columns = sorted(required_columns - actual_columns)
            if missing_process_columns:
                raise RuntimeError(
                    f"database schema is not migrated; {table_name} "
                    "missing columns: " + ", ".join(missing_process_columns)
                )
        for table_name, required_columns in (
            _MANAGED_TASK_LINEAGE_COLUMNS.items()
        ):
            actual_columns = {
                item["name"] for item in inspector.get_columns(table_name)
            }
            missing_lineage_columns = sorted(
                required_columns - actual_columns
            )
            if missing_lineage_columns:
                raise RuntimeError(
                    f"database schema is not migrated; {table_name} "
                    "missing columns: " + ", ".join(missing_lineage_columns)
                )
        for table_name, required_indexes in _MANAGED_PROCESS_INDEXES.items():
            actual_indexes = {
                item["name"] for item in inspector.get_indexes(table_name)
            }
            missing_process_indexes = sorted(required_indexes - actual_indexes)
            if missing_process_indexes:
                raise RuntimeError(
                    f"database schema is not migrated; {table_name} "
                    "missing indexes: " + ", ".join(missing_process_indexes)
                )
        for table_name, required_indexes in (
            _MANAGED_TASK_LINEAGE_INDEXES.items()
        ):
            actual_indexes = {
                str(item["name"]): (
                    tuple(item.get("column_names") or ()),
                    bool(item.get("unique")),
                )
                for item in inspector.get_indexes(table_name)
                if item.get("name")
            }
            invalid_indexes = sorted(
                name
                for name, signature in required_indexes.items()
                if actual_indexes.get(name) != signature
            )
            if invalid_indexes:
                raise RuntimeError(
                    f"database schema is not migrated; {table_name} "
                    "missing or incompatible indexes: "
                    + ", ".join(invalid_indexes)
                )
        for table_name, expected_identities in (
            _MANAGED_TASK_LINEAGE_IDENTITIES.items()
        ):
            identities = {
                tuple(item.get("column_names") or [])
                for item in inspector.get_unique_constraints(table_name)
            }
            identities.update(
                tuple(item.get("column_names") or [])
                for item in inspector.get_indexes(table_name)
                if item.get("unique")
            )
            missing_identities = expected_identities - identities
            if missing_identities:
                rendered = ", ".join(
                    "(" + ", ".join(item) + ")"
                    for item in sorted(missing_identities)
                )
                raise RuntimeError(
                    f"database schema is not migrated; {table_name} "
                    "missing unique identities: " + rendered
                )
        for table_name, expected_foreign_keys in (
            _MANAGED_TASK_LINEAGE_FOREIGN_KEYS.items()
        ):
            actual_foreign_keys = {
                (
                    tuple(item.get("constrained_columns") or []),
                    str(item.get("referred_table") or ""),
                    tuple(item.get("referred_columns") or []),
                )
                for item in inspector.get_foreign_keys(table_name)
            }
            missing_foreign_keys = expected_foreign_keys - actual_foreign_keys
            if missing_foreign_keys:
                rendered = ", ".join(
                    f"({', '.join(columns)})->{table}"
                    f"({', '.join(targets)})"
                    for columns, table, targets in sorted(missing_foreign_keys)
                )
                raise RuntimeError(
                    f"database schema is not migrated; {table_name} "
                    "missing composite foreign keys: " + rendered
                )
        for table_name, required_constraints in (
            _MANAGED_PROCESS_CONSTRAINTS.items()
        ):
            actual_constraints = {
                item["name"]
                for item in inspector.get_check_constraints(table_name)
            }
            missing_process_constraints = sorted(
                required_constraints - actual_constraints
            )
            if missing_process_constraints:
                raise RuntimeError(
                    f"database schema is not migrated; {table_name} "
                    "missing check constraints: "
                    + ", ".join(missing_process_constraints)
                )
        for table_name, expected_identities in (
            _MANAGED_PROCESS_IDENTITIES.items()
        ):
            identities = {
                tuple(item.get("column_names") or [])
                for item in inspector.get_unique_constraints(table_name)
            }
            identities.update(
                tuple(item.get("column_names") or [])
                for item in inspector.get_indexes(table_name)
                if item.get("unique")
            )
            missing_identities = expected_identities - identities
            if missing_identities:
                rendered = ", ".join(
                    "(" + ", ".join(item) + ")"
                    for item in sorted(missing_identities)
                )
                raise RuntimeError(
                    f"database schema is not migrated; {table_name} "
                    "missing unique identities: " + rendered
                )
        for table_name, expected_foreign_keys in (
            _MANAGED_PROCESS_FOREIGN_KEYS.items()
        ):
            actual_foreign_keys = {
                (
                    str((item.get("constrained_columns") or [""])[0]),
                    str(item.get("referred_table") or ""),
                    str((item.get("referred_columns") or [""])[0]),
                )
                for item in inspector.get_foreign_keys(table_name)
                if len(item.get("constrained_columns") or []) == 1
                and len(item.get("referred_columns") or []) == 1
            }
            missing_foreign_keys = expected_foreign_keys - actual_foreign_keys
            if missing_foreign_keys:
                rendered = ", ".join(
                    f"{column}->{table}.{target}"
                    for column, table, target in sorted(missing_foreign_keys)
                )
                raise RuntimeError(
                    f"database schema is not migrated; {table_name} "
                    "missing foreign keys: " + rendered
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
        snapshot_columns = {
            item["name"]
            for item in inspector.get_columns("diagnosis_evidence_snapshots")
        }
        missing_snapshot_columns = sorted(
            _MANAGED_SNAPSHOT_PROVENANCE_COLUMNS - snapshot_columns
        )
        if missing_snapshot_columns:
            raise RuntimeError(
                "database schema is not migrated; diagnosis_evidence_snapshots "
                "missing columns: " + ", ".join(missing_snapshot_columns)
            )
        revocation_outbox_indexes = {
            item["name"]
            for item in inspector.get_indexes(
                "diagnosis_artifact_revocation_outbox"
            )
        }
        missing_revocation_indexes = sorted(
            _MANAGED_REVOCATION_OUTBOX_INDEXES - revocation_outbox_indexes
        )
        if missing_revocation_indexes:
            raise RuntimeError(
                "database schema is not migrated; "
                "diagnosis_artifact_revocation_outbox missing indexes: "
                + ", ".join(missing_revocation_indexes)
            )
        runtime_turn_columns = {
            item["name"]
            for item in inspector.get_columns("agent_runtime_turns")
        }
        missing_runtime_turn_columns = sorted(
            _MANAGED_RUNTIME_TURN_RECOVERY_COLUMNS - runtime_turn_columns
        )
        if missing_runtime_turn_columns:
            raise RuntimeError(
                "database schema is not migrated; agent_runtime_turns "
                "missing columns: " + ", ".join(missing_runtime_turn_columns)
            )
        runtime_turn_indexes = {
            item["name"]
            for item in inspector.get_indexes("agent_runtime_turns")
        }
        missing_runtime_turn_indexes = sorted(
            _MANAGED_RUNTIME_TURN_RECOVERY_INDEXES - runtime_turn_indexes
        )
        if missing_runtime_turn_indexes:
            raise RuntimeError(
                "database schema is not migrated; agent_runtime_turns "
                "missing indexes: " + ", ".join(missing_runtime_turn_indexes)
            )
        runtime_turn_constraints = {
            item["name"]
            for item in inspector.get_check_constraints("agent_runtime_turns")
        }
        missing_runtime_turn_constraints = sorted(
            _MANAGED_RUNTIME_TURN_RECOVERY_CONSTRAINTS
            - runtime_turn_constraints
        )
        if missing_runtime_turn_constraints:
            raise RuntimeError(
                "database schema is not migrated; agent_runtime_turns "
                "missing check constraints: "
                + ", ".join(missing_runtime_turn_constraints)
            )
        for table_name, required_columns in _MANAGED_DROP_INSIGHT_COLUMNS.items():
            actual_columns = {
                item["name"] for item in inspector.get_columns(table_name)
            }
            missing_drop_insight_columns = sorted(
                required_columns - actual_columns
            )
            if missing_drop_insight_columns:
                raise RuntimeError(
                    f"database schema is not migrated; {table_name} "
                    "missing columns: "
                    + ", ".join(missing_drop_insight_columns)
                )
        report_effect_indexes = {
            item["name"]
            for item in inspector.get_indexes("drop_insight_reports")
        }
        missing_report_effect_indexes = sorted(
            _MANAGED_REPORT_EFFECT_INDEXES - report_effect_indexes
        )
        if missing_report_effect_indexes:
            raise RuntimeError(
                "database schema is not migrated; drop_insight_reports "
                "missing indexes: "
                + ", ".join(missing_report_effect_indexes)
            )
        report_effect_constraints = {
            item["name"]
            for item in inspector.get_check_constraints(
                "drop_insight_reports"
            )
        }
        missing_report_effect_constraints = sorted(
            _MANAGED_REPORT_EFFECT_CONSTRAINTS
            - report_effect_constraints
        )
        if missing_report_effect_constraints:
            raise RuntimeError(
                "database schema is not migrated; drop_insight_reports "
                "missing check constraints: "
                + ", ".join(missing_report_effect_constraints)
            )
        for table_name, expected_identities in (
            _MANAGED_DROP_INSIGHT_IDENTITIES.items()
        ):
            identities = {
                tuple(item.get("column_names") or [])
                for item in inspector.get_unique_constraints(table_name)
            }
            identities.update(
                tuple(item.get("column_names") or [])
                for item in inspector.get_indexes(table_name)
                if item.get("unique")
            )
            missing_identities = expected_identities - identities
            if missing_identities:
                rendered = ", ".join(
                    "(" + ", ".join(item) + ")"
                    for item in sorted(missing_identities)
                )
                raise RuntimeError(
                    f"database schema is not migrated; {table_name} "
                    "missing unique identities: " + rendered
                )
        return
    Base.metadata.create_all(bind=engine)
    _upgrade_legacy_schema(engine)
    _ensure_revocation_outbox_indexes(engine)
    _ensure_runtime_turn_recovery_index(engine)
    _ensure_report_effect_lease_index(engine)


def _ensure_report_effect_lease_index(engine: Engine) -> None:
    inspector = inspect(engine)
    if "drop_insight_reports" not in inspector.get_table_names():
        return
    existing = {
        item["name"]
        for item in inspector.get_indexes("drop_insight_reports")
    }
    if "ix_drop_insight_reports_effects_lease" not in existing:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE INDEX ix_drop_insight_reports_effects_lease "
                "ON drop_insight_reports "
                "(effects_status, effects_lease_expires_at)"
            ))


def _ensure_runtime_turn_recovery_index(engine: Engine) -> None:
    inspector = inspect(engine)
    if "agent_runtime_turns" not in inspector.get_table_names():
        return
    existing = {
        item["name"] for item in inspector.get_indexes("agent_runtime_turns")
    }
    if "ix_agent_runtime_turns_recovery_lease" not in existing:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE INDEX ix_agent_runtime_turns_recovery_lease "
                "ON agent_runtime_turns (status, recovery_lease_expires_at)"
            ))


def _ensure_revocation_outbox_indexes(engine: Engine) -> None:
    """Keep fresh and legacy SQLite schemas aligned with the managed head."""
    inspector = inspect(engine)
    if "diagnosis_artifact_revocation_outbox" not in inspector.get_table_names():
        return
    existing = {
        item["name"]
        for item in inspector.get_indexes("diagnosis_artifact_revocation_outbox")
    }
    statements = {
        "ix_diagnosis_artifact_revocation_outbox_due": (
            "CREATE INDEX IF NOT EXISTS "
            "ix_diagnosis_artifact_revocation_outbox_due "
            "ON diagnosis_artifact_revocation_outbox "
            "(status, next_attempt_at)"
        ),
        "ix_diagnosis_artifact_revocation_outbox_lease_recovery": (
            "CREATE INDEX IF NOT EXISTS "
            "ix_diagnosis_artifact_revocation_outbox_lease_recovery "
            "ON diagnosis_artifact_revocation_outbox "
            "(status, worker_lease_expires_at)"
        ),
    }
    missing = [name for name in statements if name not in existing]
    if missing:
        with engine.begin() as connection:
            for name in missing:
                connection.execute(text(statements[name]))


_ADDITIVE_MIGRATIONS = {
    "tasks": {
        "diagnosis_step_id": "VARCHAR(128)",
        "collection_status": "VARCHAR(16) NOT NULL DEFAULT 'QUEUED'",
        "analysis_status": "VARCHAR(16) NOT NULL DEFAULT 'NOT_STARTED'",
        "deleted_at": "TIMESTAMP",
        "deleted_by": "VARCHAR(128)",
        "delete_reason": "TEXT",
        "process_snapshot_id": "VARCHAR(128)",
        "process_binding_json": "JSON",
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
        "lifecycle_status": "VARCHAR(32) NOT NULL DEFAULT 'ACTIVE'",
        "trust_status": "VARCHAR(32) NOT NULL DEFAULT 'UNREVIEWED'",
        "superseded_by": "VARCHAR(128)",
        "review_revision": "INTEGER NOT NULL DEFAULT 0",
        "reviewed_at": "TIMESTAMP",
        "reviewer_id": "VARCHAR(128)",
    },
    "diagnosis_evidence_snapshots": {
        # Unknown historical provenance remains NULL; never infer the latest attempt.
        "attempt_id": "VARCHAR(128)",
        "artifact_provenance_json": "JSON",
        "analysis_provenance_json": "JSON",
    },
    "agent_runtime_turns": {
        "recovery_phase": "VARCHAR(32)",
        "recovery_owner": "VARCHAR(128)",
        "recovery_lease_expires_at": "TIMESTAMP",
        "recovery_fencing_token": "INTEGER NOT NULL DEFAULT 0",
    },
    "drop_insight_sessions": {
        "requested_time_range_json": "JSON",
        "effective_time_range_json": "JSON",
        "deleted_at": "TIMESTAMP",
        "deleted_by": "VARCHAR(128)",
        "delete_reason": "TEXT",
    },
    "drop_insight_events": {
        "effect_key": "VARCHAR(160)",
    },
    "drop_insight_tool_calls": {
        "effect_key": "VARCHAR(160)",
        "terminal_processing_status": (
            "VARCHAR(32) NOT NULL DEFAULT 'NONE'"
        ),
        "terminal_processed_at": "TIMESTAMP",
    },
    "drop_insight_reports": {
        "effects_status": (
            "VARCHAR(32) NOT NULL DEFAULT 'PENDING'"
        ),
        "effects_phase": "VARCHAR(32)",
        "effects_owner": "VARCHAR(128)",
        "effects_lease_expires_at": "TIMESTAMP",
        "effects_fencing_token": "INTEGER NOT NULL DEFAULT 0",
        "effects_applied_at": "TIMESTAMP",
    },
    "drop_insight_hypotheses": {
        "source": "VARCHAR(32) NOT NULL DEFAULT 'DETERMINISTIC_RULE'",
        "round_index": "INTEGER NOT NULL DEFAULT 1",
        "parent_hypothesis_id": "VARCHAR(128)",
        "generation_reason": "TEXT NOT NULL DEFAULT ''",
        "effect_key": "VARCHAR(160)",
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
        if "agent_runtime_turns" in tables:
            runtime_turn_columns = {
                item["name"]
                for item in inspect(connection).get_columns(
                    "agent_runtime_turns"
                )
            }
            if _MANAGED_RUNTIME_TURN_RECOVERY_COLUMNS.issubset(
                runtime_turn_columns
            ):
                connection.execute(text("""
                    UPDATE agent_runtime_turns
                    SET recovery_phase = CASE
                            WHEN runtime_session_id IS NULL
                                THEN 'NEEDS_BINDING'
                            ELSE 'SUBMIT_INTENT'
                        END,
                        recovery_owner = NULL,
                        recovery_lease_expires_at = NULL,
                        recovery_fencing_token =
                            COALESCE(recovery_fencing_token, 0)
                    WHERE status IN ('SUBMITTING', 'ACCEPTANCE_UNKNOWN')
                      AND recovery_phase IS NULL
                """))
                connection.execute(text("""
                    UPDATE agent_runtime_turns
                    SET recovery_phase = NULL,
                        recovery_owner = NULL,
                        recovery_lease_expires_at = NULL,
                        recovery_fencing_token =
                            COALESCE(recovery_fencing_token, 0)
                    WHERE status NOT IN ('SUBMITTING', 'ACCEPTANCE_UNKNOWN')
                """))
        if "drop_insight_sessions" in tables:
            session_columns = {
                item["name"]
                for item in inspect(connection).get_columns(
                    "drop_insight_sessions"
                )
            }
            if {
                "time_range_json",
                "requested_time_range_json",
            }.issubset(session_columns):
                connection.execute(text("""
                    UPDATE drop_insight_sessions
                    SET requested_time_range_json = time_range_json
                    WHERE (
                        requested_time_range_json IS NULL
                        OR TRIM(CAST(requested_time_range_json AS TEXT))
                            IN ('', '{}', 'null')
                    )
                      AND time_range_json IS NOT NULL
                      AND TRIM(CAST(time_range_json AS TEXT))
                            NOT IN ('', '{}', 'null')
                """))
        if "drop_insight_tool_calls" in tables:
            tool_call_columns = {
                item["name"]
                for item in inspect(connection).get_columns(
                    "drop_insight_tool_calls"
                )
            }
            if "terminal_processing_status" in tool_call_columns:
                connection.execute(text("""
                    UPDATE drop_insight_tool_calls
                    SET terminal_processing_status = 'NONE'
                    WHERE terminal_processing_status IS NULL
                """))
        for table_name, identity_name in {
            "drop_insight_hypotheses": (
                "uq_drop_insight_hypothesis_effect"
            ),
            "drop_insight_events": (
                "uq_drop_insight_event_effect"
            ),
            "drop_insight_tool_calls": (
                "uq_drop_insight_tool_call_effect"
            ),
        }.items():
            if table_name not in tables:
                continue
            effect_columns = {
                item["name"]
                for item in inspect(connection).get_columns(table_name)
            }
            if "effect_key" not in effect_columns:
                continue
            duplicate = connection.execute(text(f"""
                SELECT diagnosis_id, effect_key, COUNT(*) AS copies
                FROM {table_name}
                WHERE effect_key IS NOT NULL
                GROUP BY diagnosis_id, effect_key
                HAVING COUNT(*) > 1
                LIMIT 1
            """)).mappings().first()
            if duplicate is not None:
                raise RuntimeError(
                    "duplicate Drop Insight effect identity prevents schema "
                    "upgrade: "
                    f"table={table_name}, "
                    f"diagnosis_id={duplicate['diagnosis_id']}, "
                    f"effect_key={duplicate['effect_key']}, "
                    f"count={duplicate['copies']}"
                )
            effect_inspector = inspect(connection)
            effect_identities = {
                tuple(item.get("column_names") or [])
                for item in effect_inspector.get_unique_constraints(
                    table_name
                )
            }
            effect_identities.update(
                tuple(item.get("column_names") or [])
                for item in effect_inspector.get_indexes(table_name)
                if item.get("unique")
            )
            if ("diagnosis_id", "effect_key") not in effect_identities:
                connection.execute(text(
                    f"CREATE UNIQUE INDEX {identity_name} "
                    f"ON {table_name} (diagnosis_id, effect_key)"
                ))
        if "drop_insight_reports" in tables:
            report_columns = {
                item["name"]
                for item in inspect(connection).get_columns(
                    "drop_insight_reports"
                )
            }
            if _MANAGED_DROP_INSIGHT_COLUMNS[
                "drop_insight_reports"
            ].issubset(report_columns):
                connection.execute(text("""
                    UPDATE drop_insight_reports
                    SET effects_status = 'PENDING',
                        effects_owner = NULL,
                        effects_lease_expires_at = NULL,
                        effects_fencing_token =
                            COALESCE(effects_fencing_token, 0),
                        effects_applied_at = NULL
                    WHERE effects_status IS NULL
                       OR effects_status NOT IN (
                            'PENDING', 'APPLYING', 'APPLIED'
                       )
                """))
                connection.execute(text("""
                    UPDATE drop_insight_reports
                    SET effects_owner = NULL,
                        effects_lease_expires_at = NULL,
                        effects_fencing_token =
                            COALESCE(effects_fencing_token, 0),
                        effects_applied_at = NULL
                    WHERE effects_status = 'PENDING'
                """))
                connection.execute(text("""
                    UPDATE drop_insight_reports
                    SET effects_status = 'PENDING',
                        effects_owner = NULL,
                        effects_lease_expires_at = NULL,
                        effects_fencing_token =
                            COALESCE(effects_fencing_token, 0),
                        effects_applied_at = NULL
                    WHERE effects_status = 'APPLYING'
                      AND (
                        effects_owner IS NULL
                        OR effects_lease_expires_at IS NULL
                      )
                """))
                connection.execute(text("""
                    UPDATE drop_insight_reports
                    SET effects_phase = 'EFFECTS_COMPLETED',
                        effects_owner = NULL,
                        effects_lease_expires_at = NULL,
                        effects_fencing_token =
                            COALESCE(effects_fencing_token, 0),
                        effects_applied_at =
                            COALESCE(effects_applied_at, created_at)
                    WHERE effects_status = 'APPLIED'
                """))
                connection.execute(text("""
                    UPDATE drop_insight_reports
                    SET effects_phase = NULL
                    WHERE effects_phase IS NOT NULL
                      AND effects_phase NOT IN (
                        'EXECUTION_STARTED', 'EFFECTS_COMPLETED'
                      )
                """))
            duplicate = connection.execute(text("""
                SELECT diagnosis_id, hypothesis_id, COUNT(*) AS copies
                FROM drop_insight_reports
                GROUP BY diagnosis_id, hypothesis_id
                HAVING COUNT(*) > 1
                LIMIT 1
            """)).mappings().first()
            if duplicate is not None:
                raise RuntimeError(
                    "duplicate Drop Insight report identity prevents schema "
                    "upgrade: "
                    f"diagnosis_id={duplicate['diagnosis_id']}, "
                    f"hypothesis_id={duplicate['hypothesis_id']}, "
                    f"count={duplicate['copies']}"
                )
            report_inspector = inspect(connection)
            report_identities = [
                tuple(item.get("column_names") or [])
                for item in report_inspector.get_unique_constraints(
                    "drop_insight_reports"
                )
            ]
            report_identities.extend(
                tuple(item.get("column_names") or [])
                for item in report_inspector.get_indexes(
                    "drop_insight_reports"
                )
                if item.get("unique")
            )
            if ("diagnosis_id", "hypothesis_id") not in report_identities:
                connection.execute(text(
                    "CREATE UNIQUE INDEX "
                    "uq_drop_insight_report_identity "
                    "ON drop_insight_reports "
                    "(diagnosis_id, hypothesis_id)"
                ))
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
        previous_engine = _engine
        _engine = None
        _sessionmaker = None
    if previous_engine is not None:
        previous_engine.dispose()
