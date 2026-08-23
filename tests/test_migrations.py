from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import NullPool


def _alembic(tmp_path: Path, revision: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", revision.split()[0], *revision.split()[1:]],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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
    assert {"claims_json", "verification_json"} <= report_columns
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
    assert {"deleted_at", "deleted_by", "delete_reason"} <= session_columns
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
