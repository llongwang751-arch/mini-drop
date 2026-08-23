"""Database singleton initialization regression tests."""

import threading

import pytest
from sqlalchemy import inspect, text

from server.app.database import (
    _MANAGED_SCHEMA_REVISION,
    _get_engine,
    init_db,
    new_session,
    reset_engine,
)


def test_fresh_session_initialization_does_not_deadlock(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fresh.db'}")
    reset_engine()
    result = []

    def create_session():
        session = new_session()
        session.close()
        result.append("created")

    thread = threading.Thread(target=create_session, daemon=True)
    thread.start()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert result == ["created"]
    reset_engine()


def test_init_db_adds_v2_columns_to_legacy_database(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'legacy.db'}")
    reset_engine()
    engine = _get_engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE tasks (id VARCHAR(128) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE diagnosis_sessions (id VARCHAR(128) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE diagnosis_probe_executions (id VARCHAR(128) PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE diagnosis_evidence (id VARCHAR(128) PRIMARY KEY)"))
        connection.execute(text(
            "CREATE TABLE diagnosis_evidence_snapshots "
            "(id VARCHAR(128) PRIMARY KEY)"
        ))

    init_db()
    inspector = inspect(engine)
    assert "diagnosis_step_id" in {item["name"] for item in inspector.get_columns("tasks")}
    assert {"row_version", "deadline_at"}.issubset(
        item["name"] for item in inspector.get_columns("diagnosis_sessions")
    )
    assert {"retry_count", "error_code", "error_message"}.issubset(
        item["name"] for item in inspector.get_columns("diagnosis_probe_executions")
    )
    evidence_columns = {
        item["name"]
        for item in inspector.get_columns("diagnosis_evidence")
    }
    assert {
        "evidence_role",
        "lifecycle_status",
        "trust_status",
        "superseded_by",
        "review_revision",
        "reviewed_at",
        "reviewer_id",
    } <= evidence_columns
    assert "diagnosis_evidence_reviews" in inspector.get_table_names()
    assert not any(
        item.get("constrained_columns") == ["superseded_by"]
        for item in inspector.get_foreign_keys("diagnosis_evidence")
    )
    snapshot_columns = {
        item["name"]
        for item in inspector.get_columns("diagnosis_evidence_snapshots")
    }
    assert {
        "artifact_provenance_json",
        "analysis_provenance_json",
    } <= snapshot_columns
    reset_engine()


def test_init_db_creates_snapshot_provenance_columns_for_fresh_database(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fresh-snapshot.db'}")
    reset_engine()

    init_db()

    snapshot_columns = {
        item["name"]
        for item in inspect(_get_engine()).get_columns(
            "diagnosis_evidence_snapshots"
        )
    }
    assert {
        "artifact_provenance_json",
        "analysis_provenance_json",
    } <= snapshot_columns
    reset_engine()


def test_managed_schema_rejects_missing_snapshot_provenance_columns(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'managed-missing-snapshot.db'}")
    monkeypatch.delenv("MINI_DROP_SCHEMA_MANAGED", raising=False)
    reset_engine()
    init_db()
    engine = _get_engine()
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        ))
        connection.execute(text(
            "ALTER TABLE diagnosis_evidence_snapshots "
            "DROP COLUMN artifact_provenance_json"
        ))
        connection.execute(text(
            "DELETE FROM alembic_version"
        ))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": _MANAGED_SCHEMA_REVISION},
        )

    monkeypatch.setenv("MINI_DROP_SCHEMA_MANAGED", "1")
    with pytest.raises(
        RuntimeError,
        match="diagnosis_evidence_snapshots missing columns: artifact_provenance_json",
    ):
        init_db()
    reset_engine()


def test_init_db_creates_complete_evidence_schema_for_fresh_database(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'fresh-evidence.db'}")
    reset_engine()

    init_db()

    inspector = inspect(_get_engine())
    assert {
        "diagnosis_evidence_reviews",
        "diagnosis_conclusion_invalidations",
        "diagnosis_revalidation_requests",
        "diagnosis_artifact_revocations",
        "diagnosis_artifact_revocation_outbox",
    } <= set(inspector.get_table_names())
    assert any(
        item.get("referred_table") == "diagnosis_evidence"
        and item.get("constrained_columns") == ["superseded_by"]
        for item in inspector.get_foreign_keys("diagnosis_evidence")
    )
    review_foreign_keys = inspector.get_foreign_keys("diagnosis_evidence_reviews")
    assert any(
        item.get("referred_table") == "diagnosis_sessions"
        and item.get("constrained_columns") == ["diagnosis_id"]
        for item in review_foreign_keys
    )
    assert any(
        item.get("referred_table") == "diagnosis_evidence"
        and item.get("constrained_columns") == ["evidence_id"]
        for item in review_foreign_keys
    )
    reset_engine()


def test_init_db_backfills_execution_dimensions_for_legacy_done_task(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'legacy-status.db'}")
    reset_engine()
    engine = _get_engine()
    # Build the current schema first, then emulate an installation that had
    # already received the new columns with their defaults but not the
    # compatibility backfill.
    init_db()
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO tasks (
                id, name, agent_id, target_pid, collector_type, sample_rate,
                duration_sec, status, status_reason, request_params,
                collection_status, analysis_status, created_at
            ) VALUES (
                'legacy-done', 'legacy', 'agent-a', 1, 'perf', 99, 10,
                'DONE', 'legacy completed', '{}', 'QUEUED', 'NOT_STARTED',
                CURRENT_TIMESTAMP
            )
        """))

    init_db()
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT collection_status, analysis_status FROM tasks "
            "WHERE id = 'legacy-done'"
        )).one()
    assert row.collection_status == "SUCCEEDED"
    assert row.analysis_status == "SUCCEEDED"
    reset_engine()


def test_managed_schema_rejects_missing_lifecycle_propagation_table(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'managed-missing-lifecycle.db'}")
    monkeypatch.delenv("MINI_DROP_SCHEMA_MANAGED", raising=False)
    reset_engine()
    init_db()
    engine = _get_engine()
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        ))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": _MANAGED_SCHEMA_REVISION},
        )
        connection.execute(text("DROP TABLE diagnosis_revalidation_requests"))

    monkeypatch.setenv("MINI_DROP_SCHEMA_MANAGED", "1")
    with pytest.raises(
        RuntimeError,
        match="missing tables: diagnosis_revalidation_requests",
    ):
        init_db()
    reset_engine()



def test_managed_schema_requires_current_alembic_head(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'managed.db'}")
    monkeypatch.delenv("MINI_DROP_SCHEMA_MANAGED", raising=False)
    reset_engine()
    init_db()
    engine = _get_engine()
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        ))
        connection.execute(text(
            "INSERT INTO alembic_version (version_num) VALUES ('outdated')"
        ))

    monkeypatch.setenv("MINI_DROP_SCHEMA_MANAGED", "1")
    with pytest.raises(RuntimeError, match="schema revision mismatch"):
        init_db()
    reset_engine()


def test_managed_schema_accepts_current_complete_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'managed-head.db'}")
    monkeypatch.delenv("MINI_DROP_SCHEMA_MANAGED", raising=False)
    reset_engine()
    init_db()
    engine = _get_engine()
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        ))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": _MANAGED_SCHEMA_REVISION},
        )

    monkeypatch.setenv("MINI_DROP_SCHEMA_MANAGED", "1")
    init_db()
    reset_engine()
