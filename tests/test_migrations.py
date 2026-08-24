from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool


def _unique_identities(engine, table_name: str) -> set[tuple[str, ...]]:
    inspector = inspect(engine)
    identities = {
        tuple(item.get("column_names") or [])
        for item in inspector.get_unique_constraints(table_name)
    }
    identities.update(
        tuple(item.get("column_names") or [])
        for item in inspector.get_indexes(table_name)
        if item.get("unique")
    )
    return identities


def _rebuild_without_constraints(engine, table_name: str) -> None:
    with engine.begin() as connection:
        quote = connection.dialect.identifier_preparer.quote
        columns = [
            item["name"]
            for item in inspect(connection).get_columns(table_name)
        ]
        selected = ", ".join(quote(name) for name in columns)
        replacement = quote(f"{table_name}_without_constraints")
        table = quote(table_name)
        connection.execute(text(
            f"CREATE TABLE {replacement} AS SELECT {selected} FROM {table}"
        ))
        connection.execute(text(f"DROP TABLE {table}"))
        connection.execute(text(
            f"ALTER TABLE {replacement} RENAME TO {table}"
        ))


def _alembic(
    tmp_path: Path,
    revision: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            revision.split()[0],
            *revision.split()[1:],
        ],
        check=check,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_drop_insight_compatibility_backfills_requested_scope(
    tmp_path: Path,
) -> None:
    database = tmp_path / "migration.db"
    _alembic(tmp_path, "upgrade 20260823_0022")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        poolclass=NullPool,
    )
    legacy_range = {
        "start": "2026-08-23T01:00:00Z",
        "end": "2026-08-23T01:05:00Z",
    }
    with engine.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO drop_insight_sessions (
                    id, query, target_json, time_range_json, mode,
                    budget_json, status, version,
                    clarification_questions_json, created_at, updated_at
                ) VALUES (
                    'diag-legacy', 'legacy diagnosis', '{}', :time_range,
                    'OBSERVE_ONLY', '{}', 'COLLECTING_EVIDENCE', 1, '[]',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            """),
            {"time_range": json.dumps(legacy_range)},
        )

    _alembic(tmp_path, "upgrade head")

    with engine.connect() as connection:
        row = connection.execute(text("""
            SELECT requested_time_range_json, effective_time_range_json
            FROM drop_insight_sessions
            WHERE id = 'diag-legacy'
        """)).mappings().one()
    assert json.loads(row["requested_time_range_json"]) == legacy_range
    assert row["effective_time_range_json"] is None


def test_drop_insight_compatibility_preserves_established_requested_scope(
    tmp_path: Path,
) -> None:
    database = tmp_path / "migration.db"
    _alembic(tmp_path, "upgrade head")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        poolclass=NullPool,
    )
    legacy_range = {
        "start": "2026-08-23T01:00:00Z",
        "end": "2026-08-23T01:05:00Z",
    }
    established_range = {
        "start": "2026-08-23T02:00:00Z",
        "end": "2026-08-23T02:05:00Z",
    }
    requested = established_range
    with engine.begin() as connection:
        connection.execute(
            text("""
                INSERT INTO drop_insight_sessions (
                    id, query, target_json, time_range_json,
                    requested_time_range_json, effective_time_range_json,
                    mode, budget_json, status, version,
                    clarification_questions_json, created_at, updated_at
                ) VALUES (
                    'diag-current', 'current diagnosis', '{}', :time_range,
                    :requested, NULL, 'OBSERVE_ONLY', '{}',
                    'COLLECTING_EVIDENCE', 1, '[]',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
            """),
            {
                "time_range": json.dumps(legacy_range),
                "requested": json.dumps(requested),
            },
        )

    _alembic(tmp_path, "downgrade 20260823_0022")
    with engine.begin() as connection:
        connection.execute(text("""
            ALTER TABLE drop_insight_sessions
            ADD COLUMN requested_time_range_json JSON
        """))
        connection.execute(text("""
            ALTER TABLE drop_insight_sessions
            ADD COLUMN effective_time_range_json JSON
        """))
        connection.execute(
            text("""
                UPDATE drop_insight_sessions
                SET requested_time_range_json = :requested
                WHERE id = 'diag-current'
            """),
            {"requested": json.dumps(requested)},
        )
    _alembic(tmp_path, "upgrade head")

    with engine.connect() as connection:
        row = connection.execute(text("""
            SELECT requested_time_range_json, effective_time_range_json
            FROM drop_insight_sessions
            WHERE id = 'diag-current'
        """)).mappings().one()
    assert json.loads(row["requested_time_range_json"]) == established_range
    assert row["effective_time_range_json"] is None


def test_drop_insight_compatibility_rejects_duplicate_reports(
    tmp_path: Path,
) -> None:
    database = tmp_path / "migration.db"
    _alembic(tmp_path, "upgrade 20260823_0022")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        poolclass=NullPool,
    )
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE drop_insight_reports_legacy "
            "AS SELECT * FROM drop_insight_reports"
        ))
        connection.execute(text("DROP TABLE drop_insight_reports"))
        connection.execute(text(
            "ALTER TABLE drop_insight_reports_legacy "
            "RENAME TO drop_insight_reports"
        ))
        connection.execute(text("""
            INSERT INTO drop_insight_sessions (
                id, query, target_json, time_range_json, mode,
                budget_json, status, version,
                clarification_questions_json, created_at, updated_at
            ) VALUES (
                'diag-duplicate', 'duplicate report diagnosis', '{}', '{}',
                'OBSERVE_ONLY', '{}', 'COLLECTING_EVIDENCE', 1, '[]',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("""
            INSERT INTO drop_insight_hypotheses (
                id, diagnosis_id, statement, expected_observations_json,
                falsification_criteria_json, status, source, round_index,
                generation_reason, created_at, updated_at
            ) VALUES (
                'hyp-duplicate', 'diag-duplicate', 'duplicate hypothesis',
                '[]', '[]', 'OPEN', 'DETERMINISTIC_RULE', 1, '',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """))
        for report_id in ("report-a", "report-b"):
            connection.execute(
                text("""
                    INSERT INTO drop_insight_reports (
                        id, diagnosis_id, hypothesis_id, conclusion,
                        confidence, evidence_refs_json,
                        counter_evidence_refs_json, assumptions_json,
                        limitations_json, next_actions_json, claims_json,
                        verification_json, created_at
                    ) VALUES (
                        :report_id, 'diag-duplicate', 'hyp-duplicate',
                        'duplicate', 100, '[]', '[]', '[]', '[]', '[]',
                        '[]', '{}', CURRENT_TIMESTAMP
                    )
                """),
                {"report_id": report_id},
            )

    result = _alembic(tmp_path, "upgrade head", check=False)

    assert result.returncode != 0
    assert "duplicate Drop Insight report identity prevents migration" in (
        result.stdout + result.stderr
    )


def test_agent_runtime_recovery_backfills_pending_turns(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    _alembic(tmp_path, "upgrade 20260824_0025")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        poolclass=NullPool,
    )
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE agent_runtime_turns"))
        connection.execute(text("""
            CREATE TABLE agent_runtime_turns (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                diagnosis_id VARCHAR(128) NOT NULL,
                turn_id VARCHAR(128) NOT NULL,
                runtime_session_id VARCHAR(128),
                runtime_generation INTEGER NOT NULL,
                user_message TEXT NOT NULL,
                requested_mode VARCHAR(40),
                side_effect_policy VARCHAR(24),
                actor_id VARCHAR(128),
                client_command_id VARCHAR(128) NOT NULL,
                status VARCHAR(32) DEFAULT 'SUBMITTING' NOT NULL,
                accepted_mode VARCHAR(32),
                detail TEXT,
                final_message_json JSON,
                sealed_at DATETIME,
                completed_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                FOREIGN KEY(diagnosis_id) REFERENCES diagnosis_sessions (id),
                CONSTRAINT uq_agent_runtime_turn_command
                    UNIQUE (diagnosis_id, client_command_id),
                CONSTRAINT uq_agent_runtime_turn_identity
                    UNIQUE (diagnosis_id, turn_id)
            )
        """))
        connection.execute(text("""
            CREATE INDEX ix_agent_runtime_turns_diagnosis_id
            ON agent_runtime_turns (diagnosis_id)
        """))
        connection.execute(text("""
            INSERT INTO diagnosis_sessions (
                id, creator_id, raw_query, status, policy_profile,
                model_version, planner_version, row_version, deadline_at,
                created_at, updated_at
            ) VALUES (
                'diag-recovery', 'alice', 'recover runtime turns',
                'ANALYZING', 'balanced', 'test-model', 'test-planner', 0,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """))
        rows = [
            ("turn-unbound-submitting", None, "SUBMITTING", "command-1"),
            ("turn-bound-submitting", "runtime-session-1", "SUBMITTING", "command-2"),
            ("turn-unbound-unknown", None, "ACCEPTANCE_UNKNOWN", "command-3"),
            ("turn-bound-unknown", "runtime-session-1", "ACCEPTANCE_UNKNOWN", "command-4"),
            ("turn-accepted", "runtime-session-1", "ACCEPTED", "command-5"),
            ("turn-completed", "runtime-session-1", "COMPLETED", "command-6"),
        ]
        for turn_id, runtime_session_id, status, command_id in rows:
            connection.execute(
                text("""
                    INSERT INTO agent_runtime_turns (
                        diagnosis_id, turn_id, runtime_session_id,
                        runtime_generation, user_message, requested_mode,
                        side_effect_policy, actor_id, client_command_id,
                        status, created_at, updated_at
                    ) VALUES (
                        'diag-recovery', :turn_id, :runtime_session_id, 1,
                        'continue diagnosis', 'COLLABORATE', 'READ_ONLY',
                        'alice', :command_id, :status,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                """),
                {
                    "turn_id": turn_id,
                    "runtime_session_id": runtime_session_id,
                    "command_id": command_id,
                    "status": status,
                },
            )

    _alembic(tmp_path, "upgrade 20260824_0026")

    with engine.connect() as connection:
        migrated = {
            row["turn_id"]: row
            for row in connection.execute(text("""
                SELECT turn_id, recovery_phase, recovery_owner,
                       recovery_lease_expires_at, recovery_fencing_token
                FROM agent_runtime_turns
                WHERE diagnosis_id = 'diag-recovery'
            """)).mappings()
        }
    assert migrated["turn-unbound-submitting"]["recovery_phase"] == "NEEDS_BINDING"
    assert migrated["turn-bound-submitting"]["recovery_phase"] == "SUBMIT_INTENT"
    assert migrated["turn-unbound-unknown"]["recovery_phase"] == "NEEDS_BINDING"
    assert migrated["turn-bound-unknown"]["recovery_phase"] == "SUBMIT_INTENT"
    assert migrated["turn-accepted"]["recovery_phase"] is None
    assert migrated["turn-completed"]["recovery_phase"] is None
    assert all(row["recovery_owner"] is None for row in migrated.values())
    assert all(row["recovery_lease_expires_at"] is None for row in migrated.values())
    assert all(row["recovery_fencing_token"] == 0 for row in migrated.values())

    runtime_columns = {
        item["name"] for item in inspect(engine).get_columns("agent_runtime_turns")
    }
    assert {
        "recovery_phase",
        "recovery_owner",
        "recovery_lease_expires_at",
        "recovery_fencing_token",
    } <= runtime_columns
    assert "ix_agent_runtime_turns_recovery_lease" in {
        item["name"] for item in inspect(engine).get_indexes("agent_runtime_turns")
    }
    assert "ck_agent_runtime_turn_recovery_phase" in {
        item["name"]
        for item in inspect(engine).get_check_constraints("agent_runtime_turns")
    }


def test_drop_insight_report_effect_authority_migration_lifecycle(
    tmp_path: Path,
) -> None:
    database = tmp_path / "migration.db"
    _alembic(tmp_path, "upgrade 20260824_0027")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        poolclass=NullPool,
    )
    authority_columns = {
        "effects_phase",
        "effects_owner",
        "effects_lease_expires_at",
        "effects_fencing_token",
    }

    preexisting_columns = {
        item["name"]
        for item in inspect(engine).get_columns("drop_insight_reports")
    }
    preexisting_indexes = {
        item["name"]
        for item in inspect(engine).get_indexes("drop_insight_reports")
    }
    preexisting_constraints = {
        item["name"]
        for item in inspect(engine).get_check_constraints(
            "drop_insight_reports"
        )
    }
    assert authority_columns <= preexisting_columns

    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO drop_insight_sessions (
                id, query, target_json, time_range_json,
                requested_time_range_json, mode, budget_json, status,
                version, clarification_questions_json, created_at, updated_at
            ) VALUES (
                'diag-authority-normalization', 'normalize report effects',
                '{}', '{}', '{}', 'OBSERVE_ONLY', '{}',
                'COLLECTING_EVIDENCE', 1, '[]',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text("PRAGMA ignore_check_constraints = ON"))
        rows = [
            (
                "report-invalid-status", "BROKEN", "BROKEN_PHASE",
                "owner-invalid", "2099-01-01 00:00:00", 4,
                "2026-08-24 00:00:00",
            ),
            (
                "report-pending", "PENDING", "EXECUTION_STARTED",
                "owner-pending", "2099-01-01 00:00:00", 5,
                "2026-08-24 00:00:00",
            ),
            (
                "report-malformed-applying", "APPLYING", "EXECUTION_STARTED",
                None, "2099-01-01 00:00:00", 6,
                "2026-08-24 00:00:00",
            ),
            (
                "report-valid-applying", "APPLYING", "EXECUTION_STARTED",
                "owner-valid", "2099-01-01 00:00:00", 7,
                "2026-08-24 00:00:00",
            ),
            (
                "report-applied", "APPLIED", None,
                "owner-applied", "2099-01-01 00:00:00", 8, None,
            ),
        ]
        for (
            report_id,
            status,
            phase,
            owner,
            lease,
            token,
            applied_at,
        ) in rows:
            connection.execute(
                text("""
                    INSERT INTO drop_insight_reports (
                        id, diagnosis_id, hypothesis_id, conclusion,
                        confidence, evidence_refs_json,
                        counter_evidence_refs_json, assumptions_json,
                        limitations_json, next_actions_json, claims_json,
                        verification_json, effects_status, effects_phase,
                        effects_owner, effects_lease_expires_at,
                        effects_fencing_token, effects_applied_at, created_at
                    ) VALUES (
                        :report_id, 'diag-authority-normalization', NULL,
                        'normalization fixture', 0, '[]', '[]', '[]', '[]',
                        '[]', '[]', '{}', :status, :phase, :owner, :lease,
                        :token, :applied_at, '2026-08-24 01:00:00'
                    )
                """),
                {
                    "report_id": report_id,
                    "status": status,
                    "phase": phase,
                    "owner": owner,
                    "lease": lease,
                    "token": token,
                    "applied_at": applied_at,
                },
            )
        connection.execute(text("PRAGMA ignore_check_constraints = OFF"))

    _alembic(tmp_path, "upgrade 20260824_0028")

    with engine.connect() as connection:
        normalized = {
            row["id"]: row
            for row in connection.execute(text("""
                SELECT id, effects_status, effects_phase, effects_owner,
                       effects_lease_expires_at, effects_fencing_token,
                       effects_applied_at
                FROM drop_insight_reports
                WHERE diagnosis_id = 'diag-authority-normalization'
            """)).mappings()
        }
    assert normalized["report-invalid-status"]["effects_status"] == "PENDING"
    assert normalized["report-invalid-status"]["effects_phase"] is None
    assert normalized["report-invalid-status"]["effects_owner"] is None
    assert normalized["report-invalid-status"]["effects_lease_expires_at"] is None
    assert normalized["report-invalid-status"]["effects_applied_at"] is None
    assert normalized["report-pending"]["effects_status"] == "PENDING"
    assert normalized["report-pending"]["effects_phase"] == "EXECUTION_STARTED"
    assert normalized["report-pending"]["effects_owner"] is None
    assert normalized["report-pending"]["effects_lease_expires_at"] is None
    assert normalized["report-pending"]["effects_applied_at"] is None
    assert normalized["report-malformed-applying"]["effects_status"] == "PENDING"
    assert normalized["report-malformed-applying"]["effects_owner"] is None
    assert normalized["report-malformed-applying"]["effects_lease_expires_at"] is None
    assert normalized["report-malformed-applying"]["effects_applied_at"] is None
    assert normalized["report-valid-applying"]["effects_status"] == "APPLYING"
    assert normalized["report-valid-applying"]["effects_owner"] == "owner-valid"
    assert normalized["report-valid-applying"]["effects_lease_expires_at"] is not None
    assert normalized["report-valid-applying"]["effects_fencing_token"] == 7
    assert normalized["report-valid-applying"]["effects_applied_at"] is None
    assert normalized["report-applied"]["effects_status"] == "APPLIED"
    assert normalized["report-applied"]["effects_phase"] == "EFFECTS_COMPLETED"
    assert normalized["report-applied"]["effects_owner"] is None
    assert normalized["report-applied"]["effects_lease_expires_at"] is None
    assert normalized["report-applied"]["effects_applied_at"] is not None

    assert authority_columns <= {
        item["name"]
        for item in inspect(engine).get_columns("drop_insight_reports")
    }
    assert "ix_drop_insight_reports_effects_lease" in {
        item["name"]
        for item in inspect(engine).get_indexes("drop_insight_reports")
    }
    assert {
        "ck_drop_insight_report_effects_status",
        "ck_drop_insight_report_effects_phase",
    } <= {
        item["name"]
        for item in inspect(engine).get_check_constraints(
            "drop_insight_reports"
        )
    }

    _alembic(tmp_path, "downgrade 20260824_0027")

    assert preexisting_columns <= {
        item["name"]
        for item in inspect(engine).get_columns("drop_insight_reports")
    }
    assert preexisting_indexes <= {
        item["name"]
        for item in inspect(engine).get_indexes("drop_insight_reports")
    }
    assert preexisting_constraints <= {
        item["name"]
        for item in inspect(engine).get_check_constraints(
            "drop_insight_reports"
        )
    }
    assert "migration_20260824_0028_ownership" not in {
        item for item in inspect(engine).get_table_names()
    }
    assert {"effects_status", "effects_applied_at"} <= {
        item["name"]
        for item in inspect(engine).get_columns("drop_insight_reports")
    }

    _alembic(tmp_path, "upgrade 20260824_0028")

    assert authority_columns <= {
        item["name"]
        for item in inspect(engine).get_columns("drop_insight_reports")
    }


def test_process_attestation_migration_lifecycle(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    _alembic(tmp_path, "upgrade 20260824_0028")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}", poolclass=NullPool
    )
    # Revision 0001 intentionally creates the current model metadata, so a
    # database stamped at 0028 already contains these future-owned objects.
    baseline_tables = set(inspect(engine).get_table_names())
    baseline_task_columns = {
        item["name"] for item in inspect(engine).get_columns("tasks")
    }
    baseline_task_indexes = {
        item["name"] for item in inspect(engine).get_indexes("tasks")
    }
    assert {"process_candidate_snapshots", "process_candidates"} <= baseline_tables
    assert {"process_snapshot_id", "process_binding_json"} <= baseline_task_columns

    _alembic(tmp_path, "upgrade 20260824_0029")
    inspector = inspect(engine)
    assert {"process_candidate_snapshots", "process_candidates"} <= set(
        inspector.get_table_names()
    )
    assert {"process_snapshot_id", "process_binding_json"} <= {
        item["name"] for item in inspector.get_columns("tasks")
    }
    assert "ix_tasks_process_snapshot_id" in {
        item["name"] for item in inspector.get_indexes("tasks")
    }
    assert (
        "agent_id", "id"
    ) in _unique_identities(engine, "process_candidate_snapshots")
    assert (
        "snapshot_id", "pid", "process_start_ticks", "pid_namespace_inode",
        "namespace_pid", "executable_identity",
    ) in _unique_identities(engine, "process_candidates")
    assert any(
        item.get("constrained_columns") == ["process_snapshot_id"]
        and item.get("referred_table") == "process_candidate_snapshots"
        and item.get("referred_columns") == ["id"]
        for item in inspector.get_foreign_keys("tasks")
    )

    _alembic(tmp_path, "downgrade 20260824_0028")
    inspector = inspect(engine)
    assert baseline_tables <= set(inspector.get_table_names())
    assert baseline_task_columns <= {
        item["name"] for item in inspector.get_columns("tasks")
    }
    assert baseline_task_indexes <= {
        item["name"] for item in inspector.get_indexes("tasks")
    }
    assert "migration_20260824_0029_ownership" not in set(
        inspector.get_table_names()
    )

    _alembic(tmp_path, "upgrade 20260824_0029")
    assert {"process_candidate_snapshots", "process_candidates"} <= set(
        inspect(engine).get_table_names()
    )


def test_process_attestation_downgrade_preserves_preexisting_objects(
    tmp_path: Path,
) -> None:
    database = tmp_path / "migration.db"
    _alembic(tmp_path, "upgrade 20260824_0028")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}", poolclass=NullPool
    )
    with engine.begin() as connection:
        connection.execute(text(
            "DROP INDEX ix_process_snapshots_agent_received"
        ))
        connection.execute(text(
            "DROP INDEX ix_process_candidates_snapshot_pid"
        ))

    _alembic(tmp_path, "upgrade 20260824_0029")
    assert "ix_process_snapshots_agent_received" in {
        item["name"]
        for item in inspect(engine).get_indexes("process_candidate_snapshots")
    }
    assert "ix_process_candidates_snapshot_pid" in {
        item["name"] for item in inspect(engine).get_indexes("process_candidates")
    }

    _alembic(tmp_path, "downgrade 20260824_0028")
    inspector = inspect(engine)
    assert {"process_candidate_snapshots", "process_candidates"} <= set(
        inspector.get_table_names()
    )
    assert "ix_process_snapshots_agent_received" not in {
        item["name"]
        for item in inspector.get_indexes("process_candidate_snapshots")
    }
    assert "ix_process_candidates_snapshot_pid" not in {
        item["name"] for item in inspector.get_indexes("process_candidates")
    }
    assert {"process_snapshot_id", "process_binding_json"} <= {
        item["name"] for item in inspector.get_columns("tasks")
    }
    assert "migration_20260824_0029_ownership" not in set(
        inspector.get_table_names()
    )


def test_target_discovery_migration_lifecycle(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    _alembic(tmp_path, "upgrade 20260824_0030")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}", poolclass=NullPool
    )
    with engine.begin() as connection:
        connection.execute(text(
            "DROP TABLE drop_insight_target_bindings"
        ))
        connection.execute(text(
            "DROP TABLE drop_insight_target_discoveries"
        ))

    _alembic(tmp_path, "upgrade 20260824_0031")
    inspector = inspect(engine)
    assert {
        "drop_insight_target_discoveries",
        "drop_insight_target_bindings",
        "migration_20260824_0031_ownership",
    } <= set(inspector.get_table_names())
    assert "ix_drop_insight_target_discovery_scope" in {
        item["name"]
        for item in inspector.get_indexes(
            "drop_insight_target_discoveries"
        )
    }
    assert "ix_drop_insight_target_bindings_discovery" in {
        item["name"]
        for item in inspector.get_indexes(
            "drop_insight_target_bindings"
        )
    }

    _alembic(tmp_path, "downgrade 20260824_0030")
    assert not {
        "drop_insight_target_discoveries",
        "drop_insight_target_bindings",
        "migration_20260824_0031_ownership",
    } & set(inspect(engine).get_table_names())

    _alembic(tmp_path, "upgrade 20260824_0031")
    assert {
        "drop_insight_target_discoveries",
        "drop_insight_target_bindings",
    } <= set(inspect(engine).get_table_names())


def _foreign_key_identities(
    engine,
    table_name: str,
) -> set[tuple[tuple[str, ...], str, tuple[str, ...]]]:
    return {
        (
            tuple(item.get("constrained_columns") or []),
            str(item.get("referred_table") or ""),
            tuple(item.get("referred_columns") or []),
        )
        for item in inspect(engine).get_foreign_keys(table_name)
    }


def test_task_attempt_lineage_migration_lifecycle(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    _alembic(tmp_path, "upgrade 20260824_0031")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}", poolclass=NullPool
    )
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE analysis_job_output_artifacts"))
        connection.execute(text("DROP TABLE analysis_job_input_artifacts"))
        connection.execute(text("DROP TABLE artifacts"))
        connection.execute(text("DROP TABLE analysis_jobs"))
    engine.dispose()

    # Rebuild the two parent tables from the current metadata, then remove only
    # the lineage columns so 0032 owns and can reverse those additions.
    from server.app.models import AnalysisJobModel, ArtifactModel

    engine = create_engine(
        f"sqlite:///{database.as_posix()}", poolclass=NullPool
    )
    AnalysisJobModel.__table__.create(engine)
    ArtifactModel.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_analysis_jobs_task_attempt_id"))
        connection.execute(text("DROP INDEX ix_artifacts_analysis_job_id"))
        connection.execute(text("DROP INDEX ix_artifacts_task_attempt_id"))
    _rebuild_without_constraints(engine, "analysis_jobs")
    _rebuild_without_constraints(engine, "artifacts")
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE analysis_jobs DROP COLUMN task_attempt_id"))
        connection.execute(text("ALTER TABLE artifacts DROP COLUMN analysis_job_id"))
        connection.execute(text("ALTER TABLE artifacts DROP COLUMN task_attempt_id"))
    engine.dispose()

    _alembic(tmp_path, "upgrade 20260824_0032")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}", poolclass=NullPool
    )
    inspector = inspect(engine)
    assert {
        "analysis_job_input_artifacts",
        "analysis_job_output_artifacts",
        "migration_20260824_0032_ownership",
    } <= set(inspector.get_table_names())
    assert ("id", "task_id") in _unique_identities(engine, "task_attempts")
    assert ("id", "task_id", "task_attempt_id") in _unique_identities(
        engine, "analysis_jobs"
    )
    assert ("id", "task_id", "task_attempt_id") in _unique_identities(
        engine, "artifacts"
    )
    assert (
        ("task_attempt_id", "task_id"),
        "task_attempts",
        ("id", "task_id"),
    ) in _foreign_key_identities(engine, "analysis_jobs")
    assert (
        ("analysis_job_id", "task_id", "task_attempt_id"),
        "analysis_jobs",
        ("id", "task_id", "task_attempt_id"),
    ) in _foreign_key_identities(engine, "artifacts")
    assert (
        ("artifact_id", "task_id", "task_attempt_id"),
        "artifacts",
        ("id", "task_id", "task_attempt_id"),
    ) in _foreign_key_identities(engine, "analysis_job_input_artifacts")
    engine.dispose()

    _alembic(tmp_path, "downgrade 20260824_0031")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}", poolclass=NullPool
    )
    inspector = inspect(engine)
    assert not {
        "analysis_job_input_artifacts",
        "analysis_job_output_artifacts",
        "migration_20260824_0032_ownership",
    } & set(inspector.get_table_names())
    assert "task_attempt_id" not in {
        item["name"] for item in inspector.get_columns("analysis_jobs")
    }
    assert not {"task_attempt_id", "analysis_job_id"} & {
        item["name"] for item in inspector.get_columns("artifacts")
    }
    engine.dispose()

    _alembic(tmp_path, "upgrade 20260824_0032")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}", poolclass=NullPool
    )
    assert {
        "analysis_job_input_artifacts",
        "analysis_job_output_artifacts",
    } <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_analysis_artifact_repair_recovers_stamped_schema_drift(
    tmp_path: Path,
) -> None:
    database = tmp_path / "migration.db"
    _alembic(tmp_path, "upgrade 20260824_0032")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}", poolclass=NullPool
    )
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE analysis_job_output_artifacts"))
        connection.execute(text("DROP TABLE analysis_job_input_artifacts"))
    engine.dispose()

    _alembic(tmp_path, "upgrade head")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}", poolclass=NullPool
    )
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {
        "analysis_job_input_artifacts",
        "analysis_job_output_artifacts",
        "migration_20260825_0033_ownership",
    } <= tables
    assert {
        "ix_analysis_job_input_artifacts_analysis_job_id",
        "ix_analysis_job_input_artifacts_artifact_id",
    } <= {
        item["name"]
        for item in inspector.get_indexes("analysis_job_input_artifacts")
    }
    engine.dispose()

    _alembic(tmp_path, "downgrade 20260824_0032")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}", poolclass=NullPool
    )
    assert not {
        "analysis_job_input_artifacts",
        "analysis_job_output_artifacts",
        "migration_20260825_0033_ownership",
    } & set(inspect(engine).get_table_names())
    engine.dispose()


def test_task_attempt_lineage_migration_rejects_duplicate_authority(
    tmp_path: Path,
) -> None:
    database = tmp_path / "migration.db"
    _alembic(tmp_path, "upgrade 20260824_0031")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}", poolclass=NullPool
    )
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE task_attempts_without_authority_unique (
                id VARCHAR(128) NOT NULL,
                task_id VARCHAR(128) NOT NULL,
                attempt_no INTEGER NOT NULL,
                task_attempt_authority_sha256 VARCHAR(64),
                agent_id VARCHAR(128) NOT NULL,
                status VARCHAR(16) NOT NULL,
                reason TEXT,
                lease_expires_at DATETIME,
                metadata_json JSON,
                created_at DATETIME NOT NULL,
                started_at DATETIME,
                finished_at DATETIME,
                PRIMARY KEY (id)
            )
        """))
        connection.execute(text("""
            INSERT INTO task_attempts_without_authority_unique
            SELECT * FROM task_attempts
        """))
        connection.execute(text("DROP TABLE task_attempts"))
        connection.execute(text(
            "ALTER TABLE task_attempts_without_authority_unique "
            "RENAME TO task_attempts"
        ))
        connection.execute(text("""
            INSERT INTO task_attempts (
                id, task_id, attempt_no, task_attempt_authority_sha256,
                agent_id, status, reason, metadata_json, created_at
            ) VALUES
                ('attempt-duplicate-1', 'missing-task', 1, :digest,
                 'missing-agent', 'RUNNING', '', '{}', CURRENT_TIMESTAMP),
                ('attempt-duplicate-2', 'missing-task', 2, :digest,
                 'missing-agent', 'RUNNING', '', '{}', CURRENT_TIMESTAMP)
        """), {"digest": "a" * 64})
    engine.dispose()

    result = _alembic(
        tmp_path, "upgrade 20260824_0032", check=False
    )
    assert result.returncode != 0
    assert "duplicate task attempt authority digest" in (
        result.stdout + result.stderr
    )


def test_target_discovery_downgrade_preserves_preexisting_objects(
    tmp_path: Path,
) -> None:
    database = tmp_path / "migration.db"
    _alembic(tmp_path, "upgrade 20260824_0030")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}", poolclass=NullPool
    )
    with engine.begin() as connection:
        connection.execute(text(
            "DROP INDEX ix_drop_insight_target_discovery_scope"
        ))
        connection.execute(text(
            "DROP INDEX ix_drop_insight_target_bindings_discovery"
        ))
        connection.execute(text(
            "CREATE INDEX ix_target_discovery_unrelated "
            "ON drop_insight_target_discoveries (status)"
        ))
        connection.execute(text(
            "CREATE INDEX ix_target_binding_unrelated "
            "ON drop_insight_target_bindings (agent_id)"
        ))

    _alembic(tmp_path, "upgrade 20260824_0031")
    inspector = inspect(engine)
    assert {
        "ix_drop_insight_target_discovery_scope",
        "ix_target_discovery_unrelated",
    } <= {
        item["name"]
        for item in inspector.get_indexes(
            "drop_insight_target_discoveries"
        )
    }
    assert {
        "ix_drop_insight_target_bindings_discovery",
        "ix_target_binding_unrelated",
    } <= {
        item["name"]
        for item in inspector.get_indexes(
            "drop_insight_target_bindings"
        )
    }

    _alembic(tmp_path, "downgrade 20260824_0030")
    inspector = inspect(engine)
    assert {
        "drop_insight_target_discoveries",
        "drop_insight_target_bindings",
    } <= set(inspector.get_table_names())
    assert "ix_drop_insight_target_discovery_scope" not in {
        item["name"]
        for item in inspector.get_indexes(
            "drop_insight_target_discoveries"
        )
    }
    assert "ix_drop_insight_target_bindings_discovery" not in {
        item["name"]
        for item in inspector.get_indexes(
            "drop_insight_target_bindings"
        )
    }
    assert "ix_target_discovery_unrelated" in {
        item["name"]
        for item in inspector.get_indexes(
            "drop_insight_target_discoveries"
        )
    }
    assert "ix_target_binding_unrelated" in {
        item["name"]
        for item in inspector.get_indexes(
            "drop_insight_target_bindings"
        )
    }
    assert "migration_20260824_0031_ownership" not in set(
        inspector.get_table_names()
    )


def test_drop_insight_effect_identity_migration_lifecycle(
    tmp_path: Path,
) -> None:
    database = tmp_path / "migration.db"
    _alembic(tmp_path, "upgrade 20260824_0026")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        poolclass=NullPool,
    )
    effect_tables = {
        "drop_insight_hypotheses",
        "drop_insight_events",
        "drop_insight_tool_calls",
    }
    for table_name in effect_tables:
        assert "effect_key" in {
            item["name"] for item in inspect(engine).get_columns(table_name)
        }
        assert (
            "diagnosis_id",
            "effect_key",
        ) in _unique_identities(engine, table_name)

    _alembic(tmp_path, "upgrade 20260824_0027")

    for table_name in effect_tables:
        assert "effect_key" in {
            item["name"] for item in inspect(engine).get_columns(table_name)
        }
        assert (
            "diagnosis_id",
            "effect_key",
        ) in _unique_identities(engine, table_name)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO drop_insight_sessions (
                id, query, target_json, time_range_json,
                requested_time_range_json, mode, budget_json, status,
                version, clarification_questions_json,
                created_at, updated_at
            ) VALUES (
                'diag-effect-null', 'effect identity null semantics', '{}',
                '{}', '{}', 'OBSERVE_ONLY', '{}',
                'COLLECTING_EVIDENCE', 1, '[]',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """))
        for hypothesis_id in ("hyp-null-a", "hyp-null-b"):
            connection.execute(
                text("""
                    INSERT INTO drop_insight_hypotheses (
                        id, diagnosis_id, statement,
                        expected_observations_json,
                        falsification_criteria_json, status, source,
                        round_index, generation_reason, effect_key,
                        created_at, updated_at
                    ) VALUES (
                        :hypothesis_id, 'diag-effect-null', 'null identity',
                        '[]', '[]', 'OPEN', 'DETERMINISTIC_RULE', 1, '', NULL,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                """),
                {"hypothesis_id": hypothesis_id},
            )

    _alembic(tmp_path, "downgrade 20260824_0026")

    for table_name in effect_tables:
        assert "effect_key" not in {
            item["name"] for item in inspect(engine).get_columns(table_name)
        }
        assert (
            "diagnosis_id",
            "effect_key",
        ) not in _unique_identities(engine, table_name)
    assert {"effects_status", "effects_applied_at"} <= {
        item["name"]
        for item in inspect(engine).get_columns("drop_insight_reports")
    }
    assert {
        "recovery_phase",
        "recovery_owner",
        "recovery_lease_expires_at",
        "recovery_fencing_token",
    } <= {
        item["name"]
        for item in inspect(engine).get_columns("agent_runtime_turns")
    }

    _alembic(tmp_path, "upgrade 20260824_0027")

    for table_name in effect_tables:
        assert "effect_key" in {
            item["name"] for item in inspect(engine).get_columns(table_name)
        }
        assert (
            "diagnosis_id",
            "effect_key",
        ) in _unique_identities(engine, table_name)


def test_drop_insight_effect_identity_migration_rejects_duplicates(
    tmp_path: Path,
) -> None:
    database = tmp_path / "migration.db"
    _alembic(tmp_path, "upgrade 20260824_0026")
    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        poolclass=NullPool,
    )
    _rebuild_without_constraints(engine, "drop_insight_hypotheses")
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO drop_insight_sessions (
                id, query, target_json, time_range_json,
                requested_time_range_json, mode, budget_json, status,
                version, clarification_questions_json,
                created_at, updated_at
            ) VALUES (
                'diag-effect-duplicate', 'duplicate effect identity', '{}',
                '{}', '{}', 'OBSERVE_ONLY', '{}',
                'COLLECTING_EVIDENCE', 1, '[]',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """))
        for hypothesis_id in ("hyp-effect-a", "hyp-effect-b"):
            connection.execute(
                text("""
                    INSERT INTO drop_insight_hypotheses (
                        id, diagnosis_id, statement,
                        expected_observations_json,
                        falsification_criteria_json, status, source,
                        round_index, generation_reason, effect_key,
                        created_at, updated_at
                    ) VALUES (
                        :hypothesis_id, 'diag-effect-duplicate',
                        'duplicate identity', '[]', '[]', 'OPEN',
                        'DETERMINISTIC_RULE', 1, '', 'report:r1:hypothesis',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                """),
                {"hypothesis_id": hypothesis_id},
            )

    result = _alembic(
        tmp_path,
        "upgrade 20260824_0027",
        check=False,
    )

    assert result.returncode != 0
    assert "duplicate Drop Insight effect identity prevents migration" in (
        result.stdout + result.stderr
    )
    assert "table=drop_insight_hypotheses" in (
        result.stdout + result.stderr
    )


def test_migrations_upgrade_rollback_and_reapply(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    _alembic(tmp_path, "upgrade head")
    # NullPool prevents SQLite handles from surviving Alembic subprocesses on
    # Windows, where pytest cannot clean a still-open temporary database.
    engine = create_engine(f"sqlite:///{database.as_posix()}", poolclass=NullPool)
    tables = set(inspect(engine).get_table_names())
    assert {"alembic_version", "tasks", "diagnosis_sessions"} <= tables
    assert "platform_schema_metadata" in tables
    agent_columns = {item["name"]: item for item in inspect(engine).get_columns("agents")}
    assert agent_columns["os_info"]["type"].__class__.__name__.upper() == "TEXT"
    artifact_columns = {item["name"] for item in inspect(engine).get_columns("artifacts")}
    assert {"sha256", "manifest_json", "integrity_status", "integrity_reason"} <= artifact_columns
    report_columns = {
        item["name"] for item in inspect(engine).get_columns("drop_insight_reports")
    }
    assert {
        "claims_json",
        "verification_json",
        "effects_status",
        "effects_applied_at",
    } <= report_columns
    task_columns = {item["name"] for item in inspect(engine).get_columns("tasks")}
    assert {"idempotency_key", "creator_id"} <= task_columns
    assert "outbox_messages" in tables
    assert {"schedules", "schedule_records"} <= tables
    assert {"composite_tasks", "composite_task_items"} <= tables
    assert "fix_verifications" in tables
    assert {
        "frozen_diagnosis_artifacts",
        "diagnosis_artifact_outbox",
        "diagnosis_artifact_evaluations",
    } <= tables
    assert {
        "agent_runtime_bindings",
        "agent_runtime_turns",
        "agent_runtime_events",
    } <= tables
    assert {
        "diagnosis_conclusion_invalidations",
        "diagnosis_revalidation_requests",
        "diagnosis_artifact_revocations",
        "diagnosis_artifact_revocation_outbox",
    } <= tables
    lifecycle_unique_constraints = {
        "diagnosis_conclusion_invalidations":
            "uq_diagnosis_conclusion_invalidation_identity",
        "diagnosis_revalidation_requests":
            "uq_diagnosis_revalidation_request_identity",
        "diagnosis_artifact_revocations":
            "uq_diagnosis_artifact_revocation_identity",
    }
    for table_name, constraint_name in lifecycle_unique_constraints.items():
        assert constraint_name in {
            item["name"]
            for item in inspect(engine).get_unique_constraints(table_name)
        }
    assert any(
        set(item.get("column_names") or []) == {"revocation_id"}
        for item in inspect(engine).get_unique_constraints(
            "diagnosis_artifact_revocation_outbox"
        )
    )
    revocation_outbox_indexes = {
        item["name"]
        for item in inspect(engine).get_indexes(
            "diagnosis_artifact_revocation_outbox"
        )
    }
    assert {
        "ix_diagnosis_artifact_revocation_outbox_status",
        "ix_diagnosis_artifact_revocation_outbox_due",
        "ix_diagnosis_artifact_revocation_outbox_lease_recovery",
    } <= revocation_outbox_indexes
    lifecycle_foreign_keys = {
        "diagnosis_conclusion_invalidations": {
            (("diagnosis_id",), "diagnosis_sessions"),
            (("evidence_id",), "diagnosis_evidence"),
        },
        "diagnosis_revalidation_requests": {
            (("diagnosis_id",), "diagnosis_sessions"),
            (("evidence_id",), "diagnosis_evidence"),
        },
        "diagnosis_artifact_revocations": {
            (("diagnosis_id",), "diagnosis_sessions"),
            (("artifact_id",), "frozen_diagnosis_artifacts"),
            (("evidence_id",), "diagnosis_evidence"),
        },
        "diagnosis_artifact_revocation_outbox": {
            (("revocation_id",), "diagnosis_artifact_revocations"),
        },
    }
    for table_name, expected in lifecycle_foreign_keys.items():
        actual = {
            (tuple(item.get("constrained_columns") or []), item.get("referred_table"))
            for item in inspect(engine).get_foreign_keys(table_name)
        }
        assert expected <= actual
    assert "diagnosis_evidence_reviews" in tables
    evidence_columns = {
        item["name"] for item in inspect(engine).get_columns("diagnosis_evidence")
    }
    assert {
        "lifecycle_status",
        "trust_status",
        "superseded_by",
        "review_revision",
        "reviewed_at",
        "reviewer_id",
    } <= evidence_columns
    evidence_review_uniques = {
        item["name"] for item in inspect(engine).get_unique_constraints(
            "diagnosis_evidence_reviews"
        )
    }
    assert "uq_diagnosis_evidence_review_revision" in evidence_review_uniques
    for constrained_columns, referred_table in (
        (["diagnosis_id"], "diagnosis_sessions"),
        (["evidence_id"], "diagnosis_evidence"),
        (["superseded_by"], "diagnosis_evidence"),
    ):
        assert any(
            item.get("referred_table") == referred_table
            and item.get("constrained_columns") == constrained_columns
            for item in inspect(engine).get_foreign_keys(
                "diagnosis_evidence_reviews"
            )
        )
    runtime_turn_indexes = {
        item["name"] for item in inspect(engine).get_indexes("agent_runtime_turns")
    }
    runtime_event_indexes = {
        item["name"] for item in inspect(engine).get_indexes("agent_runtime_events")
    }
    assert "ix_agent_runtime_turns_diagnosis_id" in runtime_turn_indexes
    assert "ix_agent_runtime_events_diagnosis_id" in runtime_event_indexes
    runtime_turn_uniques = {
        item["name"] for item in inspect(engine).get_unique_constraints(
            "agent_runtime_turns"
        )
    }
    runtime_event_uniques = {
        item["name"] for item in inspect(engine).get_unique_constraints(
            "agent_runtime_events"
        )
    }
    assert {
        "uq_agent_runtime_turn_command",
        "uq_agent_runtime_turn_identity",
    } <= runtime_turn_uniques
    assert {
        "uq_agent_runtime_event_sequence",
        "uq_agent_runtime_event_identity",
    } <= runtime_event_uniques
    for table in (
        "agent_runtime_bindings",
        "agent_runtime_turns",
        "agent_runtime_events",
    ):
        assert any(
            item.get("referred_table") == "diagnosis_sessions"
            and item.get("constrained_columns") == ["diagnosis_id"]
            for item in inspect(engine).get_foreign_keys(table)
        )
    assert any(
        item.get("referred_table") == "agent_runtime_turns"
        and item.get("constrained_columns") == ["diagnosis_id", "turn_id"]
        and item.get("referred_columns") == ["diagnosis_id", "turn_id"]
        for item in inspect(engine).get_foreign_keys("agent_runtime_events")
    )
    artifact_outbox_columns = {
        item["name"]
        for item in inspect(engine).get_columns("diagnosis_artifact_outbox")
    }
    assert {
        "attempts",
        "next_attempt_at",
        "worker_lease_owner",
        "worker_lease_expires_at",
        "last_error",
        "published_at",
    } <= artifact_outbox_columns
    artifact_outbox_indexes = {
        item["name"]
        for item in inspect(engine).get_indexes("diagnosis_artifact_outbox")
    }
    assert {
        "ix_diagnosis_artifact_outbox_status",
        "ix_diagnosis_artifact_outbox_due",
        "ix_diagnosis_artifact_outbox_lease_expiry",
    } <= artifact_outbox_indexes
    diagnosis_session_columns = {
        item["name"] for item in inspect(engine).get_columns("diagnosis_sessions")
    }
    assert "case_id" in diagnosis_session_columns
    diagnosis_session_indexes = {
        item["name"] for item in inspect(engine).get_indexes("diagnosis_sessions")
    }
    assert "ix_diagnosis_sessions_case_id" in diagnosis_session_indexes
    session_columns = {
        item["name"] for item in inspect(engine).get_columns("drop_insight_sessions")
    }
    assert {
        "deleted_at",
        "deleted_by",
        "delete_reason",
        "requested_time_range_json",
        "effective_time_range_json",
    } <= session_columns
    tool_call_columns = {
        item["name"]
        for item in inspect(engine).get_columns("drop_insight_tool_calls")
    }
    assert {
        "effect_key",
        "terminal_processing_status",
        "terminal_processed_at",
    } <= tool_call_columns
    effect_identity_tables = {
        "drop_insight_hypotheses",
        "drop_insight_events",
        "drop_insight_tool_calls",
    }
    for table_name in effect_identity_tables:
        assert "effect_key" in {
            item["name"] for item in inspect(engine).get_columns(table_name)
        }
        assert (
            "diagnosis_id",
            "effect_key",
        ) in _unique_identities(engine, table_name)
    report_identities = {
        (item.get("name"), tuple(item.get("column_names") or []))
        for item in inspect(engine).get_unique_constraints(
            "drop_insight_reports"
        )
    }
    report_identities.update(
        (item.get("name"), tuple(item.get("column_names") or []))
        for item in inspect(engine).get_indexes("drop_insight_reports")
        if item.get("unique")
    )
    assert (
        "uq_drop_insight_report_identity",
        ("diagnosis_id", "hypothesis_id"),
    ) in report_identities
    snapshot_columns = {
        item["name"] for item in inspect(engine).get_columns(
            "diagnosis_evidence_snapshots"
        )
    }
    assert {
        "attempt_id",
        "artifact_provenance_json",
        "analysis_provenance_json",
    } <= snapshot_columns
    snapshot_indexes = {
        item["name"] for item in inspect(engine).get_indexes(
            "diagnosis_evidence_snapshots"
        )
    }
    assert "ix_diagnosis_evidence_snapshots_attempt_id" in snapshot_indexes
    snapshot_foreign_keys = inspect(engine).get_foreign_keys(
        "diagnosis_evidence_snapshots"
    )
    assert any(
        item.get("referred_table") == "task_attempts"
        and item.get("constrained_columns") == ["attempt_id"]
        for item in snapshot_foreign_keys
    )

    # 20260824_0027 -> 20260824_0026 removes only semantic effect identities.
    _alembic(tmp_path, "downgrade 20260824_0026")
    for table_name in effect_identity_tables:
        assert "effect_key" not in {
            item["name"] for item in inspect(engine).get_columns(table_name)
        }
        assert (
            "diagnosis_id",
            "effect_key",
        ) not in _unique_identities(engine, table_name)
    assert {"effects_status", "effects_applied_at"} <= {
        item["name"]
        for item in inspect(engine).get_columns("drop_insight_reports")
    }
    assert {
        "recovery_phase",
        "recovery_owner",
        "recovery_lease_expires_at",
        "recovery_fencing_token",
    } <= {
        item["name"]
        for item in inspect(engine).get_columns("agent_runtime_turns")
    }
    _alembic(tmp_path, "upgrade head")
    for table_name in effect_identity_tables:
        assert "effect_key" in {
            item["name"] for item in inspect(engine).get_columns(table_name)
        }
        assert (
            "diagnosis_id",
            "effect_key",
        ) in _unique_identities(engine, table_name)

    # 20260824_0025 -> 20260824_0023 removes only report-effect state.
    _alembic(tmp_path, "downgrade 20260824_0023")
    after_report_effects_downgrade = set(inspect(engine).get_table_names())
    report_columns = {
        item["name"]
        for item in inspect(engine).get_columns("drop_insight_reports")
    }
    assert not {"effects_status", "effects_applied_at"} & report_columns
    report_identities = {
        tuple(item.get("column_names") or [])
        for item in inspect(engine).get_unique_constraints(
            "drop_insight_reports"
        )
    }
    report_identities.update(
        tuple(item.get("column_names") or [])
        for item in inspect(engine).get_indexes("drop_insight_reports")
        if item.get("unique")
    )
    assert ("diagnosis_id", "hypothesis_id") in report_identities
    assert {
        "terminal_processing_status",
        "terminal_processed_at",
    } <= {
        item["name"]
        for item in inspect(engine).get_columns("drop_insight_tool_calls")
    }
    assert {
        "requested_time_range_json",
        "effective_time_range_json",
    } <= {
        item["name"]
        for item in inspect(engine).get_columns("drop_insight_sessions")
    }
    _alembic(tmp_path, "upgrade head")
    assert {"effects_status", "effects_applied_at"} <= {
        item["name"]
        for item in inspect(engine).get_columns("drop_insight_reports")
    }

    # 20260824_0023 -> 20260823_0022 removes only live-scope and
    # terminal-processing compatibility schema.
    _alembic(tmp_path, "downgrade 20260823_0022")
    session_columns = {
        item["name"]
        for item in inspect(engine).get_columns("drop_insight_sessions")
    }
    assert not {
        "requested_time_range_json",
        "effective_time_range_json",
    } & session_columns
    tool_call_columns = {
        item["name"]
        for item in inspect(engine).get_columns("drop_insight_tool_calls")
    }
    assert not {
        "terminal_processing_status",
        "terminal_processed_at",
    } & tool_call_columns
    report_identities = {
        item.get("name")
        for item in inspect(engine).get_unique_constraints(
            "drop_insight_reports"
        )
    } | {
        item.get("name")
        for item in inspect(engine).get_indexes("drop_insight_reports")
        if item.get("unique")
    }
    assert "uq_drop_insight_report_identity" not in report_identities
    assert {
        "diagnosis_conclusion_invalidations",
        "diagnosis_revalidation_requests",
        "diagnosis_artifact_revocations",
        "diagnosis_artifact_revocation_outbox",
    } <= set(inspect(engine).get_table_names())
    _alembic(tmp_path, "upgrade head")

    # 20260823_0022 -> 20260823_0021 removes only lifecycle propagation tables.
    _alembic(tmp_path, "downgrade 20260823_0021")
    tables = set(inspect(engine).get_table_names())
    assert not {
        "diagnosis_conclusion_invalidations",
        "diagnosis_revalidation_requests",
        "diagnosis_artifact_revocations",
        "diagnosis_artifact_revocation_outbox",
    } & tables
    snapshot_columns = {
        item["name"]
        for item in inspect(engine).get_columns("diagnosis_evidence_snapshots")
    }
    assert {
        "attempt_id",
        "artifact_provenance_json",
        "analysis_provenance_json",
    } <= snapshot_columns
    _alembic(tmp_path, "upgrade head")

    # 20260823_0021 -> 20260823_0020 removes only Snapshot provenance.
    _alembic(tmp_path, "downgrade 20260823_0020")
    snapshot_columns = {
        item["name"] for item in inspect(engine).get_columns(
            "diagnosis_evidence_snapshots"
        )
    }
    assert "attempt_id" in snapshot_columns
    assert not {
        "artifact_provenance_json",
        "analysis_provenance_json",
    } & snapshot_columns
    assert "ix_diagnosis_evidence_snapshots_attempt_id" in {
        item["name"] for item in inspect(engine).get_indexes(
            "diagnosis_evidence_snapshots"
        )
    }
    assert any(
        item.get("referred_table") == "task_attempts"
        and item.get("constrained_columns") == ["attempt_id"]
        for item in inspect(engine).get_foreign_keys(
            "diagnosis_evidence_snapshots"
        )
    )
    _alembic(tmp_path, "upgrade head")
    snapshot_columns = {
        item["name"] for item in inspect(engine).get_columns(
            "diagnosis_evidence_snapshots"
        )
    }
    assert {
        "attempt_id",
        "artifact_provenance_json",
        "analysis_provenance_json",
    } <= snapshot_columns

    # 20260823_0019 -> 20260822_0018 removes only Runtime persistence.
    _alembic(tmp_path, "downgrade 20260822_0018")
    tables = set(inspect(engine).get_table_names())
    assert not {
        "agent_runtime_bindings",
        "agent_runtime_turns",
        "agent_runtime_events",
    } & tables
    assert {
        "diagnosis_sessions",
        "frozen_diagnosis_artifacts",
        "diagnosis_artifact_outbox",
        "diagnosis_artifact_evaluations",
    } <= tables
    _alembic(tmp_path, "upgrade head")
    assert {
        "agent_runtime_bindings",
        "agent_runtime_turns",
        "agent_runtime_events",
    } <= set(inspect(engine).get_table_names())

    # 20260821_0017 -> 20260821_0016 removes only the public case identity.
    _alembic(tmp_path, "downgrade 20260821_0016")
    diagnosis_session_columns = {
        item["name"] for item in inspect(engine).get_columns("diagnosis_sessions")
    }
    assert "case_id" not in diagnosis_session_columns
    assert "ix_diagnosis_sessions_case_id" not in {
        item["name"] for item in inspect(engine).get_indexes("diagnosis_sessions")
    }
    _alembic(tmp_path, "upgrade 20260821_0017")

    # 20260821_0013 -> 20260813_0012 removes the attempt foreign key/index/column.
    _alembic(tmp_path, "downgrade 20260813_0012")
    snapshot_columns = {
        item["name"] for item in inspect(engine).get_columns(
            "diagnosis_evidence_snapshots"
        )
    }
    assert "attempt_id" not in snapshot_columns
    snapshot_indexes = {
        item["name"] for item in inspect(engine).get_indexes(
            "diagnosis_evidence_snapshots"
        )
    }
    assert "ix_diagnosis_evidence_snapshots_attempt_id" not in snapshot_indexes
    assert not any(
        item.get("referred_table") == "task_attempts"
        and item.get("constrained_columns") == ["attempt_id"]
        for item in inspect(engine).get_foreign_keys(
            "diagnosis_evidence_snapshots"
        )
    )

    # 20260813_0012 -> 20260807_0011 drops feedback rounds only.
    _alembic(tmp_path, "downgrade -1")
    hypothesis_columns = {
        item["name"] for item in inspect(engine).get_columns("drop_insight_hypotheses")
    }
    assert "round_index" not in hypothesis_columns
    assert "drop_insight_feedback" not in set(inspect(engine).get_table_names())
    session_columns = {
        item["name"] for item in inspect(engine).get_columns("drop_insight_sessions")
    }
    assert "deleted_at" in session_columns

    # 20260807_0011 -> 20260806_0010 drops the soft-delete columns only.
    _alembic(tmp_path, "downgrade -1")
    session_columns = {
        item["name"] for item in inspect(engine).get_columns("drop_insight_sessions")
    }
    assert "deleted_at" not in session_columns
    assert "fix_verifications" in set(inspect(engine).get_table_names())

    # 20260806_0010 -> 20260806_0009 drops the fix-verification table only.
    _alembic(tmp_path, "downgrade -1")
    assert "fix_verifications" not in set(inspect(engine).get_table_names())
    assert {"composite_tasks", "composite_task_items"} <= set(inspect(engine).get_table_names())

    # 20260806_0009 -> 20260806_0008 drops the composite tables only.
    _alembic(tmp_path, "downgrade -1")
    assert "composite_tasks" not in set(inspect(engine).get_table_names())
    assert "composite_task_items" not in set(inspect(engine).get_table_names())
    assert {"schedules", "schedule_records"} <= set(inspect(engine).get_table_names())

    # 20260806_0008 -> 20260806_0007 drops the schedule tables only.
    _alembic(tmp_path, "downgrade -1")
    assert "schedules" not in set(inspect(engine).get_table_names())
    assert "schedule_records" not in set(inspect(engine).get_table_names())
    assert "outbox_messages" in set(inspect(engine).get_table_names())

    # 20260806_0007 -> 20260806_0006 drops the outbox table only.
    _alembic(tmp_path, "downgrade -1")
    assert "outbox_messages" not in set(inspect(engine).get_table_names())
    task_columns = {item["name"] for item in inspect(engine).get_columns("tasks")}
    assert "idempotency_key" in task_columns

    # 20260806_0006 -> 20260805_0005 drops the task idempotency columns only.
    _alembic(tmp_path, "downgrade -1")
    task_columns = {item["name"] for item in inspect(engine).get_columns("tasks")}
    assert "idempotency_key" not in task_columns
    assert "creator_id" not in task_columns
    report_columns = {
        item["name"] for item in inspect(engine).get_columns("drop_insight_reports")
    }
    assert "claims_json" in report_columns
    artifact_columns = {item["name"] for item in inspect(engine).get_columns("artifacts")}
    assert "sha256" in artifact_columns

    # 20260805_0005 -> 20260805_0004 drops the budget-reservation columns.
    _alembic(tmp_path, "downgrade -1")
    report_columns = {
        item["name"] for item in inspect(engine).get_columns("drop_insight_reports")
    }
    assert "claims_json" in report_columns
    artifact_columns = {item["name"] for item in inspect(engine).get_columns("artifacts")}
    assert "sha256" in artifact_columns

    # 20260805_0004 -> 20260802_0003 drops the claim-verification columns.
    _alembic(tmp_path, "downgrade -1")
    report_columns = {
        item["name"] for item in inspect(engine).get_columns("drop_insight_reports")
    }
    assert "claims_json" not in report_columns
    assert "verification_json" not in report_columns
    artifact_columns = {item["name"] for item in inspect(engine).get_columns("artifacts")}
    assert "sha256" in artifact_columns

    # 20260802_0003 -> 20260801_0002 drops the artifact SHA-256 columns.
    _alembic(tmp_path, "downgrade -1")
    artifact_columns = {item["name"] for item in inspect(engine).get_columns("artifacts")}
    assert "sha256" not in artifact_columns
    assert "platform_schema_metadata" in set(inspect(engine).get_table_names())

    # 20260801_0002 -> 20260801_0001 drops the schema-metadata table.
    _alembic(tmp_path, "downgrade -1")
    assert "platform_schema_metadata" not in set(inspect(engine).get_table_names())

    _alembic(tmp_path, "upgrade head")
    assert "platform_schema_metadata" in set(inspect(engine).get_table_names())
    artifact_columns = {item["name"] for item in inspect(engine).get_columns("artifacts")}
    assert "sha256" in artifact_columns
    task_columns = {item["name"] for item in inspect(engine).get_columns("tasks")}
    assert {"idempotency_key", "creator_id"} <= task_columns
    assert "outbox_messages" in set(inspect(engine).get_table_names())
    assert {"schedules", "schedule_records"} <= set(inspect(engine).get_table_names())
    assert {"composite_tasks", "composite_task_items"} <= set(inspect(engine).get_table_names())
    assert "fix_verifications" in set(inspect(engine).get_table_names())
    artifact_outbox_columns = {
        item["name"]
        for item in inspect(engine).get_columns("diagnosis_artifact_outbox")
    }
    assert {
        "attempts",
        "next_attempt_at",
        "worker_lease_owner",
        "worker_lease_expires_at",
        "last_error",
        "published_at",
    } <= artifact_outbox_columns
    assert {
        "agent_runtime_bindings",
        "agent_runtime_turns",
        "agent_runtime_events",
    } <= set(inspect(engine).get_table_names())
    engine.dispose()
