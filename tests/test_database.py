"""Database singleton initialization regression tests."""

import json
import re
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


def _install_managed_revision(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
        ))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": _MANAGED_SCHEMA_REVISION},
        )


def _remove_report_identity(connection) -> None:
    indexes = inspect(connection).get_indexes("drop_insight_reports")
    sql = connection.execute(text(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'drop_insight_reports'"
    )).scalar_one()
    sql = re.sub(
        r",\s*CONSTRAINT uq_drop_insight_report_identity "
        r"UNIQUE \(diagnosis_id, hypothesis_id\)",
        "",
        sql,
        count=1,
    )
    assert "uq_drop_insight_report_identity" not in sql
    connection.execute(text(
        sql.replace(
            "CREATE TABLE drop_insight_reports",
            "CREATE TABLE drop_insight_reports_legacy",
            1,
        )
    ))
    columns = [
        item["name"]
        for item in inspect(connection).get_columns("drop_insight_reports")
    ]
    selected = ", ".join(
        connection.dialect.identifier_preparer.quote(name)
        for name in columns
    )
    connection.execute(text(
        "INSERT INTO drop_insight_reports_legacy "
        f"({selected}) SELECT {selected} FROM drop_insight_reports"
    ))
    connection.execute(text("DROP TABLE drop_insight_reports"))
    connection.execute(text(
        "ALTER TABLE drop_insight_reports_legacy "
        "RENAME TO drop_insight_reports"
    ))
    quote = connection.dialect.identifier_preparer.quote
    for index in indexes:
        name = index.get("name")
        columns = index.get("column_names") or []
        if not name or not columns:
            continue
        unique = "UNIQUE " if index.get("unique") else ""
        selected = ", ".join(quote(column) for column in columns)
        connection.execute(text(
            f"CREATE {unique}INDEX {quote(name)} "
            f"ON drop_insight_reports ({selected})"
        ))


def _remove_report_effect_contract(connection) -> None:
    columns = [
        item["name"]
        for item in inspect(connection).get_columns("drop_insight_reports")
    ]
    selected = ", ".join(
        connection.dialect.identifier_preparer.quote(name)
        for name in columns
    )
    connection.execute(text(
        "CREATE TABLE drop_insight_reports_legacy "
        f"AS SELECT {selected} FROM drop_insight_reports"
    ))
    connection.execute(text("DROP TABLE drop_insight_reports"))
    connection.execute(text(
        "ALTER TABLE drop_insight_reports_legacy "
        "RENAME TO drop_insight_reports"
    ))
    connection.execute(text(
        "CREATE UNIQUE INDEX uq_drop_insight_report_identity "
        "ON drop_insight_reports (diagnosis_id, hypothesis_id)"
    ))


def _remove_report_effect_constraint(connection, constraint_name: str) -> None:
    expressions = {
        "ck_drop_insight_report_effects_status": (
            r",\s*CONSTRAINT ck_drop_insight_report_effects_status "
            r"CHECK \(effects_status IN \('PENDING', 'APPLYING', 'APPLIED'\)\)"
        ),
        "ck_drop_insight_report_effects_phase": (
            r",\s*CONSTRAINT ck_drop_insight_report_effects_phase "
            r"CHECK \(effects_phase IS NULL OR effects_phase IN "
            r"\('EXECUTION_STARTED', 'EFFECTS_COMPLETED'\)\)"
        ),
    }
    indexes = inspect(connection).get_indexes("drop_insight_reports")
    sql = connection.execute(text(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'drop_insight_reports'"
    )).scalar_one()
    sql = re.sub(expressions[constraint_name], "", sql, count=1)
    assert constraint_name not in sql
    connection.execute(text(
        sql.replace(
            "CREATE TABLE drop_insight_reports",
            "CREATE TABLE drop_insight_reports_legacy",
            1,
        )
    ))
    columns = [
        item["name"]
        for item in inspect(connection).get_columns("drop_insight_reports")
    ]
    quote = connection.dialect.identifier_preparer.quote
    selected = ", ".join(quote(name) for name in columns)
    connection.execute(text(
        "INSERT INTO drop_insight_reports_legacy "
        f"({selected}) SELECT {selected} FROM drop_insight_reports"
    ))
    connection.execute(text("DROP TABLE drop_insight_reports"))
    connection.execute(text(
        "ALTER TABLE drop_insight_reports_legacy "
        "RENAME TO drop_insight_reports"
    ))
    for index in indexes:
        name = index.get("name")
        index_columns = index.get("column_names") or []
        if not name or not index_columns:
            continue
        unique = "UNIQUE " if index.get("unique") else ""
        selected = ", ".join(quote(column) for column in index_columns)
        connection.execute(text(
            f"CREATE {unique}INDEX {quote(name)} "
            f"ON drop_insight_reports ({selected})"
        ))


def _rebuild_effect_table(
    connection,
    table_name: str,
    *,
    include_effect_key: bool,
) -> None:
    quote = connection.dialect.identifier_preparer.quote
    columns = [
        item["name"]
        for item in inspect(connection).get_columns(table_name)
        if include_effect_key or item["name"] != "effect_key"
    ]
    selected = ", ".join(quote(name) for name in columns)
    connection.execute(text(
        f"CREATE TABLE {quote(table_name + '_legacy')} AS "
        f"SELECT {selected} FROM {quote(table_name)}"
    ))
    connection.execute(text(f"DROP TABLE {quote(table_name)}"))
    connection.execute(text(
        f"ALTER TABLE {quote(table_name + '_legacy')} "
        f"RENAME TO {quote(table_name)}"
    ))


def _remove_effect_identity(connection, table_name: str) -> None:
    _rebuild_effect_table(
        connection,
        table_name,
        include_effect_key=True,
    )


def _remove_effect_schema(connection, table_name: str) -> None:
    _rebuild_effect_table(
        connection,
        table_name,
        include_effect_key=False,
    )


def _remove_named_table_constraint(
    connection,
    table_name: str,
    constraint_name: str,
) -> None:
    indexes = inspect(connection).get_indexes(table_name)
    sql = connection.execute(text(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = :table_name"
    ), {"table_name": table_name}).scalar_one()
    sql = re.sub(
        rf",\s*CONSTRAINT {re.escape(constraint_name)} "
        r"(?:UNIQUE \([^)]*\)|FOREIGN KEY\([^)]*\) "
        r"REFERENCES [^( ]+ \([^)]*\))",
        "",
        sql,
        count=1,
    )
    assert constraint_name not in sql
    legacy_name = f"{table_name}_legacy"
    connection.execute(text(sql.replace(
        f"CREATE TABLE {table_name}",
        f"CREATE TABLE {legacy_name}",
        1,
    )))
    quote = connection.dialect.identifier_preparer.quote
    columns = [
        item["name"] for item in inspect(connection).get_columns(table_name)
    ]
    selected = ", ".join(quote(name) for name in columns)
    connection.execute(text(
        f"INSERT INTO {quote(legacy_name)} ({selected}) "
        f"SELECT {selected} FROM {quote(table_name)}"
    ))
    connection.execute(text(f"DROP TABLE {quote(table_name)}"))
    connection.execute(text(
        f"ALTER TABLE {quote(legacy_name)} RENAME TO {quote(table_name)}"
    ))
    for index in indexes:
        name = index.get("name")
        index_columns = index.get("column_names") or []
        if not name or not index_columns:
            continue
        unique = "UNIQUE " if index.get("unique") else ""
        selected = ", ".join(quote(column) for column in index_columns)
        connection.execute(text(
            f"CREATE {unique}INDEX {quote(name)} "
            f"ON {quote(table_name)} ({selected})"
        ))


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



def test_init_db_upgrades_legacy_drop_insight_contract(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite:///{tmp_path / 'legacy-drop-insight.db'}",
    )
    reset_engine()
    init_db()
    engine = _get_engine()
    legacy_range = {
        "start": "2026-08-23T01:00:00Z",
        "end": "2026-08-23T01:05:00Z",
    }
    with engine.begin() as connection:
        _remove_report_effect_contract(connection)
        for table_name in (
            "drop_insight_hypotheses",
            "drop_insight_events",
            "drop_insight_tool_calls",
        ):
            _remove_effect_schema(connection, table_name)
        connection.execute(text(
            "ALTER TABLE drop_insight_sessions "
            "DROP COLUMN effective_time_range_json"
        ))
        connection.execute(text(
            "ALTER TABLE drop_insight_sessions "
            "DROP COLUMN requested_time_range_json"
        ))
        connection.execute(text(
            "ALTER TABLE drop_insight_tool_calls "
            "DROP COLUMN terminal_processed_at"
        ))
        connection.execute(text(
            "ALTER TABLE drop_insight_tool_calls "
            "DROP COLUMN terminal_processing_status"
        ))
        connection.execute(text(
            "ALTER TABLE drop_insight_reports "
            "DROP COLUMN effects_applied_at"
        ))
        connection.execute(text(
            "ALTER TABLE drop_insight_reports "
            "DROP COLUMN effects_status"
        ))
        connection.execute(
            text("""
                INSERT INTO drop_insight_sessions (
                    id, query, target_json, time_range_json, mode,
                    budget_json, status, version,
                    clarification_questions_json, created_at, updated_at
                ) VALUES (
                    'legacy-diag', 'legacy diagnosis', '{}', :time_range,
                    'OBSERVE_ONLY', '{}', 'COLLECTING_EVIDENCE', 1, '[]',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            """),
            {"time_range": json.dumps(legacy_range)},
        )
        connection.execute(text("""
            INSERT INTO drop_insight_tool_calls (
                id, diagnosis_id, tool_name, arguments_json,
                policy_decision, policy_checks_json, policy_reason,
                status, result_json, budget_reservation_json,
                budget_settlement_json, budget_reservation_status,
                requested_by, created_at
            ) VALUES (
                'legacy-call', 'legacy-diag', 'collect', '{}',
                'ALLOW', '[]', 'legacy', 'DONE', '{}', '{}', '{}',
                'NONE', 'legacy-user', CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("""
            INSERT INTO drop_insight_hypotheses (
                id, diagnosis_id, statement, expected_observations_json,
                falsification_criteria_json, status, source, round_index,
                generation_reason, created_at, updated_at
            ) VALUES (
                'legacy-hyp', 'legacy-diag', 'legacy hypothesis',
                '[]', '[]', 'OPEN', 'DETERMINISTIC_RULE', 1, '',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("""
            INSERT INTO drop_insight_reports (
                id, diagnosis_id, hypothesis_id, conclusion,
                confidence, evidence_refs_json,
                counter_evidence_refs_json, assumptions_json,
                limitations_json, next_actions_json, claims_json,
                verification_json, created_at
            ) VALUES (
                'legacy-report', 'legacy-diag', 'legacy-hyp', 'legacy',
                100, '[]', '[]', '[]', '[]', '[]', '[]', '{}',
                CURRENT_TIMESTAMP
            )
        """))

    init_db()

    inspector = inspect(engine)
    session_columns = {
        item["name"]
        for item in inspector.get_columns("drop_insight_sessions")
    }
    assert {
        "requested_time_range_json",
        "effective_time_range_json",
    } <= session_columns
    tool_call_columns = {
        item["name"]
        for item in inspector.get_columns("drop_insight_tool_calls")
    }
    assert {
        "effect_key",
        "terminal_processing_status",
        "terminal_processed_at",
    } <= tool_call_columns
    for table_name in {
        "drop_insight_hypotheses",
        "drop_insight_events",
        "drop_insight_tool_calls",
    }:
        assert "effect_key" in {
            item["name"] for item in inspector.get_columns(table_name)
        }
        identities = {
            tuple(item.get("column_names") or [])
            for item in inspector.get_unique_constraints(table_name)
        }
        identities.update(
            tuple(item.get("column_names") or [])
            for item in inspector.get_indexes(table_name)
            if item.get("unique")
        )
        assert ("diagnosis_id", "effect_key") in identities
    report_columns = {
        item["name"]
        for item in inspector.get_columns("drop_insight_reports")
    }
    assert {
        "effects_status",
        "effects_phase",
        "effects_owner",
        "effects_lease_expires_at",
        "effects_fencing_token",
        "effects_applied_at",
    } <= report_columns
    assert "ix_drop_insight_reports_effects_lease" in {
        item["name"]
        for item in inspector.get_indexes("drop_insight_reports")
    }
    assert any(
        item.get("name") == "uq_drop_insight_report_identity"
        and item.get("unique")
        and item.get("column_names")
        == ["diagnosis_id", "hypothesis_id"]
        for item in inspector.get_indexes("drop_insight_reports")
    )
    with engine.connect() as connection:
        row = connection.execute(text("""
            SELECT requested_time_range_json, effective_time_range_json
            FROM drop_insight_sessions
            WHERE id = 'legacy-diag'
        """)).mappings().one()
        terminal_status = connection.execute(text("""
            SELECT terminal_processing_status
            FROM drop_insight_tool_calls
            WHERE id = 'legacy-call'
        """)).scalar_one()
        report_effects = connection.execute(text("""
            SELECT effects_status, effects_phase, effects_owner,
                   effects_lease_expires_at, effects_fencing_token,
                   effects_applied_at
            FROM drop_insight_reports
            WHERE id = 'legacy-report'
        """)).mappings().one()
    assert json.loads(row["requested_time_range_json"]) == legacy_range
    assert row["effective_time_range_json"] is None
    assert terminal_status == "NONE"
    assert report_effects["effects_status"] == "PENDING"
    assert report_effects["effects_phase"] is None
    assert report_effects["effects_owner"] is None
    assert report_effects["effects_lease_expires_at"] is None
    assert report_effects["effects_fencing_token"] == 0
    assert report_effects["effects_applied_at"] is None
    reset_engine()


def test_init_db_preserves_established_requested_scope(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite:///{tmp_path / 'requested-scope.db'}",
    )
    reset_engine()
    init_db()
    engine = _get_engine()
    legacy_range = {
        "start": "2026-08-23T01:00:00Z",
        "end": "2026-08-23T01:05:00Z",
    }
    established_range = {
        "start": "2026-08-23T02:00:00Z",
        "end": "2026-08-23T02:05:00Z",
    }
    with engine.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO drop_insight_sessions (
                    id, query, target_json, time_range_json,
                    requested_time_range_json, mode, budget_json, status,
                    version, clarification_questions_json,
                    created_at, updated_at
                ) VALUES (
                    'current-diag', 'current diagnosis', '{}', :time_range,
                    :requested, 'OBSERVE_ONLY', '{}',
                    'COLLECTING_EVIDENCE', 1, '[]',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            """),
            {
                "time_range": json.dumps(legacy_range),
                "requested": json.dumps(established_range),
            },
        )

    init_db()

    with engine.connect() as connection:
        requested = connection.execute(text("""
            SELECT requested_time_range_json
            FROM drop_insight_sessions
            WHERE id = 'current-diag'
        """)).scalar_one()
    assert json.loads(requested) == established_range
    reset_engine()


def test_init_db_rejects_duplicate_drop_insight_reports(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite:///{tmp_path / 'duplicate-reports.db'}",
    )
    reset_engine()
    init_db()
    engine = _get_engine()
    with engine.begin() as connection:
        _remove_report_identity(connection)
        connection.execute(text("""
            INSERT INTO drop_insight_sessions (
                id, query, target_json, time_range_json,
                requested_time_range_json, effective_time_range_json,
                mode, budget_json, status, version,
                clarification_questions_json, created_at, updated_at
            ) VALUES (
                'duplicate-diag', 'duplicate diagnosis', '{}', '{}',
                '{}', '{}', 'OBSERVE_ONLY', '{}',
                'COLLECTING_EVIDENCE', 1, '[]',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("""
            INSERT INTO drop_insight_hypotheses (
                id, diagnosis_id, statement, expected_observations_json,
                falsification_criteria_json, status, source, round_index,
                generation_reason, created_at, updated_at
            ) VALUES (
                'duplicate-hyp', 'duplicate-diag', 'duplicate hypothesis',
                '[]', '[]', 'OPEN', 'DETERMINISTIC_RULE', 1, '',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """))
        for report_id in ("report-one", "report-two"):
            connection.execute(
                text("""
                    INSERT INTO drop_insight_reports (
                        id, diagnosis_id, hypothesis_id, conclusion,
                        confidence, evidence_refs_json,
                        counter_evidence_refs_json, assumptions_json,
                        limitations_json, next_actions_json, claims_json,
                        verification_json, effects_status,
                        effects_fencing_token, created_at
                    ) VALUES (
                        :report_id, 'duplicate-diag', 'duplicate-hyp',
                        'duplicate', 100, '[]', '[]', '[]', '[]', '[]',
                        '[]', '{}', 'PENDING', 0, CURRENT_TIMESTAMP
                    )
                """),
                {"report_id": report_id},
            )

    with pytest.raises(
        RuntimeError,
        match="duplicate Drop Insight report identity prevents schema upgrade",
    ):
        init_db()
    reset_engine()


def test_managed_schema_rejects_missing_drop_insight_column(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite:///{tmp_path / 'managed-missing-drop-insight.db'}",
    )
    monkeypatch.delenv("MINI_DROP_SCHEMA_MANAGED", raising=False)
    reset_engine()
    init_db()
    engine = _get_engine()
    _install_managed_revision(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE drop_insight_tool_calls "
            "DROP COLUMN terminal_processed_at"
        ))

    monkeypatch.setenv("MINI_DROP_SCHEMA_MANAGED", "1")
    with pytest.raises(
        RuntimeError,
        match="drop_insight_tool_calls missing columns: terminal_processed_at",
    ):
        init_db()
    reset_engine()


def test_managed_schema_rejects_missing_report_effect_column(
    monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite:///{tmp_path / 'managed-missing-report-effect.db'}",
    )
    monkeypatch.delenv("MINI_DROP_SCHEMA_MANAGED", raising=False)
    reset_engine()
    init_db()
    engine = _get_engine()
    _install_managed_revision(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "ALTER TABLE drop_insight_reports "
            "DROP COLUMN effects_applied_at"
        ))

    monkeypatch.setenv("MINI_DROP_SCHEMA_MANAGED", "1")
    with pytest.raises(
        RuntimeError,
        match="drop_insight_reports missing columns: effects_applied_at",
    ):
        init_db()
    reset_engine()


@pytest.mark.parametrize(
    ("object_name", "expected"),
    [
        (
            "ix_drop_insight_reports_effects_lease",
            "drop_insight_reports missing indexes: "
            "ix_drop_insight_reports_effects_lease",
        ),
        (
            "ck_drop_insight_report_effects_status",
            "drop_insight_reports missing check constraints: "
            "ck_drop_insight_report_effects_status",
        ),
        (
            "ck_drop_insight_report_effects_phase",
            "drop_insight_reports missing check constraints: "
            "ck_drop_insight_report_effects_phase",
        ),
    ],
)
def test_managed_schema_rejects_missing_report_effect_authority_object(
    monkeypatch,
    tmp_path,
    object_name,
    expected,
):
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite:///{tmp_path / (object_name + '.db')}",
    )
    monkeypatch.delenv("MINI_DROP_SCHEMA_MANAGED", raising=False)
    reset_engine()
    init_db()
    engine = _get_engine()
    _install_managed_revision(engine)
    with engine.begin() as connection:
        if object_name.startswith("ix_"):
            connection.execute(text(f"DROP INDEX {object_name}"))
        else:
            _remove_report_effect_constraint(connection, object_name)

    monkeypatch.setenv("MINI_DROP_SCHEMA_MANAGED", "1")
    with pytest.raises(RuntimeError, match=expected):
        init_db()
    reset_engine()


def test_managed_schema_rejects_missing_report_identity(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite:///{tmp_path / 'managed-missing-report-identity.db'}",
    )
    monkeypatch.delenv("MINI_DROP_SCHEMA_MANAGED", raising=False)
    reset_engine()
    init_db()
    engine = _get_engine()
    _install_managed_revision(engine)
    with engine.begin() as connection:
        _remove_report_identity(connection)

    monkeypatch.setenv("MINI_DROP_SCHEMA_MANAGED", "1")
    with pytest.raises(
        RuntimeError,
        match="drop_insight_reports missing unique identities",
    ):
        init_db()
    reset_engine()


def test_managed_schema_rejects_missing_effect_identity(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite:///{tmp_path / 'managed-missing-effect-identity.db'}",
    )
    monkeypatch.delenv("MINI_DROP_SCHEMA_MANAGED", raising=False)
    reset_engine()
    init_db()
    engine = _get_engine()
    _install_managed_revision(engine)
    with engine.begin() as connection:
        _remove_effect_identity(connection, "drop_insight_events")

    monkeypatch.setenv("MINI_DROP_SCHEMA_MANAGED", "1")
    with pytest.raises(
        RuntimeError,
        match=(
            "drop_insight_events missing unique identities: "
            r"\(diagnosis_id, effect_key\)"
        ),
    ):
        init_db()
    reset_engine()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            "column",
            "analysis_job_input_artifacts missing columns: created_at",
        ),
        (
            "index",
            "analysis_jobs missing or incompatible indexes: "
            "ix_analysis_jobs_task_attempt_id",
        ),
        (
            "identity",
            "artifacts missing unique identities: "
            r"\(id, task_id, task_attempt_id\)",
        ),
        (
            "foreign-key",
            "analysis_jobs missing composite foreign keys: "
            r"\(task_attempt_id, task_id\)->task_attempts\(id, task_id\)",
        ),
    ],
)
def test_managed_schema_rejects_incomplete_task_attempt_lineage(
    monkeypatch,
    tmp_path,
    mutation,
    expected,
):
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite:///{tmp_path / ('managed-lineage-' + mutation + '.db')}",
    )
    monkeypatch.delenv("MINI_DROP_SCHEMA_MANAGED", raising=False)
    reset_engine()
    init_db()
    engine = _get_engine()
    _install_managed_revision(engine)
    with engine.begin() as connection:
        if mutation == "column":
            connection.execute(text(
                "ALTER TABLE analysis_job_input_artifacts "
                "DROP COLUMN created_at"
            ))
        elif mutation == "index":
            connection.execute(text(
                "DROP INDEX ix_analysis_jobs_task_attempt_id"
            ))
            connection.execute(text(
                "CREATE INDEX ix_analysis_jobs_task_attempt_id "
                "ON analysis_jobs (task_id)"
            ))
        elif mutation == "identity":
            _remove_named_table_constraint(
                connection,
                "artifacts",
                "uq_artifact_attempt_identity",
            )
        else:
            _remove_named_table_constraint(
                connection,
                "analysis_jobs",
                "fk_analysis_jobs_task_attempt_identity",
            )

    monkeypatch.setenv("MINI_DROP_SCHEMA_MANAGED", "1")
    with pytest.raises(RuntimeError, match=expected):
        init_db()
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
    assert row.collection_status == "COLLECTED"
    assert row.analysis_status == "SUCCESS"
    reset_engine()






def test_managed_schema_rejects_missing_process_table(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite:///{tmp_path / 'managed-process-table.db'}"
    )
    monkeypatch.delenv("MINI_DROP_SCHEMA_MANAGED", raising=False)
    reset_engine()
    init_db()
    engine = _get_engine()
    _install_managed_revision(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE process_candidates"))

    monkeypatch.setenv("MINI_DROP_SCHEMA_MANAGED", "1")
    with pytest.raises(RuntimeError, match="missing tables: process_candidates"):
        init_db()
    reset_engine()


def test_managed_schema_rejects_missing_process_index(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite:///{tmp_path / 'managed-process-index.db'}"
    )
    monkeypatch.delenv("MINI_DROP_SCHEMA_MANAGED", raising=False)
    reset_engine()
    init_db()
    engine = _get_engine()
    _install_managed_revision(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_process_candidates_snapshot_pid"))

    monkeypatch.setenv("MINI_DROP_SCHEMA_MANAGED", "1")
    with pytest.raises(
        RuntimeError,
        match=(
            "process_candidates missing indexes: "
            "ix_process_candidates_snapshot_pid"
        ),
    ):
        init_db()
    reset_engine()


def test_managed_schema_rejects_missing_process_foreign_key(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite:///{tmp_path / 'managed-process-fk.db'}"
    )
    monkeypatch.delenv("MINI_DROP_SCHEMA_MANAGED", raising=False)
    reset_engine()
    init_db()
    engine = _get_engine()
    _install_managed_revision(engine)
    with engine.begin() as connection:
        indexes = inspect(connection).get_indexes("tasks")
        columns = [item["name"] for item in inspect(connection).get_columns("tasks")]
        selected = ", ".join(columns)
        create_sql = connection.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'"
        )).scalar_one()
        create_sql = re.sub(
            r",\s*FOREIGN KEY\(process_snapshot_id\) "
            r"REFERENCES process_candidate_snapshots \(id\)",
            "",
            create_sql,
            count=1,
        )
        assert "FOREIGN KEY(process_snapshot_id)" not in create_sql
        connection.execute(text(create_sql.replace(
            "CREATE TABLE tasks", "CREATE TABLE tasks_without_process_fk", 1
        )))
        connection.execute(text(
            f"INSERT INTO tasks_without_process_fk ({selected}) "
            f"SELECT {selected} FROM tasks"
        ))
        connection.execute(text("DROP TABLE tasks"))
        connection.execute(text(
            "ALTER TABLE tasks_without_process_fk RENAME TO tasks"
        ))
        quote = connection.dialect.identifier_preparer.quote
        for index in indexes:
            name = index.get("name")
            index_columns = index.get("column_names") or []
            if name and index_columns:
                unique = "UNIQUE " if index.get("unique") else ""
                rendered = ", ".join(quote(item) for item in index_columns)
                connection.execute(text(
                    f"CREATE {unique}INDEX {quote(name)} ON tasks ({rendered})"
                ))

    monkeypatch.setenv("MINI_DROP_SCHEMA_MANAGED", "1")
    with pytest.raises(
        RuntimeError,
        match=(
            "tasks missing foreign keys: process_snapshot_id->"
            "process_candidate_snapshots.id"
        ),
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
