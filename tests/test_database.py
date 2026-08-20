"""Database singleton initialization regression tests."""

import threading

from sqlalchemy import inspect, text

from server.app.database import _get_engine, init_db, new_session, reset_engine


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

    init_db()
    inspector = inspect(engine)
    assert "diagnosis_step_id" in {item["name"] for item in inspector.get_columns("tasks")}
    assert {"row_version", "deadline_at"}.issubset(
        item["name"] for item in inspector.get_columns("diagnosis_sessions")
    )
    assert {"retry_count", "error_code", "error_message"}.issubset(
        item["name"] for item in inspector.get_columns("diagnosis_probe_executions")
    )
    assert "evidence_role" in {
        item["name"] for item in inspector.get_columns("diagnosis_evidence")
    }
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
