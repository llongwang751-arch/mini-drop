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
    assert "attempt_id" in snapshot_columns
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
    engine.dispose()
