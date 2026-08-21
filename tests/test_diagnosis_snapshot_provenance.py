"""Diagnosis evidence snapshot attempt-lineage tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.diagnosis.store import DiagnosisStore
from server.app.models import (
    AgentModel,
    AnalysisJobModel,
    ArtifactModel,
    Base,
    TaskAttemptModel,
    TaskModel,
)


@pytest.fixture(autouse=True)
def _database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    from server.app.database import _get_engine

    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture
def store() -> DiagnosisStore:
    result = DiagnosisStore()
    timestamp = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
    result.create_session({
        "diagnosis_id": "diag-snapshot",
        "creator_id": "test",
        "raw_query": "snapshot lineage test",
        "normalized_intent": {},
        "target_scope": {},
        "requested_time_range": {},
        "effective_time_range": {},
        "status": "COLLECTING",
        "policy_profile": "production_safe",
        "risk_budget": {},
        "resource_budget": {},
        "budget_used": {},
        "hypothesis_graph": {},
        "child_task_ids": [],
        "conclusion_versions": [],
        "model_version": "test",
        "planner_version": "test",
        "deadline_at": timestamp + timedelta(hours=1),
    })
    with new_session() as session:
        session.add(AgentModel(
            id="agent-snapshot",
            hostname="worker",
            ip_addr="127.0.0.1",
            version="1.0",
            os_info="linux",
            capabilities=["sys_metrics"],
            status="ONLINE",
            last_heartbeat_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
        ))
        session.add(TaskModel(
            id="task-snapshot",
            name="snapshot task",
            agent_id="agent-snapshot",
            target_pid=123,
            collector_type="sys_metrics",
            status="DONE",
            status_reason="completed",
            request_params={},
            created_at=timestamp,
            started_at=timestamp + timedelta(seconds=1),
            finished_at=timestamp + timedelta(seconds=10),
        ))
        session.commit()
    return result


def _add_attempt(
    attempt_id: str,
    attempt_no: int,
    *,
    status: str = "SUCCEEDED",
) -> None:
    timestamp = datetime(2026, 8, 21, 10, attempt_no, tzinfo=timezone.utc)
    with new_session() as session:
        session.add(TaskAttemptModel(
            id=attempt_id,
            task_id="task-snapshot",
            attempt_no=attempt_no,
            agent_id="agent-snapshot",
            status=status,
            reason="completed",
            metadata_json={},
            created_at=timestamp,
            started_at=timestamp + timedelta(seconds=1),
            finished_at=timestamp + timedelta(seconds=9),
        ))
        session.commit()


def _add_artifact() -> int:
    timestamp = datetime(2026, 8, 21, 10, 2, tzinfo=timezone.utc)
    with new_session() as session:
        artifact = ArtifactModel(
            task_id="task-snapshot",
            artifact_type="sys_metrics",
            object_key="tasks/task-snapshot/sys_metrics.json",
            content_type="application/json",
            size_bytes=128,
            sha256="a" * 64,
            integrity_status="VERIFIED",
            integrity_reason="verified in test",
            meta_json={},
            created_at=timestamp,
        )
        session.add(artifact)
        session.commit()
        return artifact.id


def _add_job(job_id: str, attempt_id: str | None, artifact_id: int) -> None:
    timestamp = datetime(2026, 8, 21, 10, 3, tzinfo=timezone.utc)
    with new_session() as session:
        session.add(AnalysisJobModel(
            id=job_id,
            task_id="task-snapshot",
            task_attempt_id=attempt_id,
            analyzer_type="collector.sys_metrics",
            analyzer_version="1.0.0",
            input_checksum="b" * 64,
            input_artifact_ids_json=[artifact_id],
            idempotency_key=f"snapshot:{job_id}",
            status="SUCCEEDED",
            status_reason="completed",
            retry_count=0,
            max_retries=3,
            next_run_at=timestamp,
            output_artifact_ids_json=[artifact_id],
            created_at=timestamp,
            updated_at=timestamp,
            started_at=timestamp,
            finished_at=timestamp,
        ))
        session.commit()


def _snapshot_payload(artifact_id: int | None = None) -> dict:
    payload = {
        "diagnosis_id": "diag-snapshot",
        "round_index": 2,
        "evidence_role": "incident",
        "time_range": {"sampling_period_seconds": 5},
        "target": {"pid": 123},
        "workload_identity": {},
        "deployment_version": None,
        "host_fingerprint": {"agent_id": "agent-snapshot"},
        "collector": "sys_metrics",
        "collector_version": "1.0",
        "task_id": "task-snapshot",
        "evidence_refs": ["ev-2", "ev-1"],
        "artifact_refs": ["tasks/task-snapshot/sys_metrics.json"],
        "baseline_ref": None,
        "quality": {"complete": True},
    }
    if artifact_id is not None:
        payload["artifact_ids"] = [artifact_id]
    return payload


def test_unique_successful_attempt_is_bound_with_terminal_window(
    store: DiagnosisStore,
) -> None:
    _add_attempt("attempt-one", 1)

    snapshot = store.add_evidence_snapshot(_snapshot_payload())

    identity = "diag-snapshot:task-snapshot:attempt-one:2:incident"
    assert snapshot["snapshot_id"] == (
        "snap_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
    )
    assert snapshot["attempt_id"] == "attempt-one"
    assert snapshot["task_attempt_id"] == "attempt-one"
    assert snapshot["captured_at"] == datetime(2026, 8, 21, 10, 1, 9)
    assert snapshot["time_range"]["start"].startswith("2026-08-21T10:01:01")
    assert snapshot["time_range"]["end"].startswith("2026-08-21T10:01:09")
    assert snapshot["integrity_hash"].startswith("sha256:")


def test_analysis_job_selects_producing_attempt_among_retries(
    store: DiagnosisStore,
) -> None:
    _add_attempt("attempt-one", 1)
    _add_attempt("attempt-two", 2)
    artifact_id = _add_artifact()
    _add_job("job-two", "attempt-two", artifact_id)

    snapshot = store.add_evidence_snapshot(_snapshot_payload(artifact_id))

    assert snapshot["attempt_id"] == "attempt-two"


def test_multiple_successful_attempts_without_job_lineage_are_rejected(
    store: DiagnosisStore,
) -> None:
    _add_attempt("attempt-one", 1)
    _add_attempt("attempt-two", 2)

    with pytest.raises(ValueError, match="one unambiguous successful attempt"):
        store.add_evidence_snapshot(_snapshot_payload())


def test_relevant_job_without_attempt_lineage_is_rejected(
    store: DiagnosisStore,
) -> None:
    _add_attempt("attempt-one", 1)
    artifact_id = _add_artifact()
    _add_job("job-missing", None, artifact_id)

    with pytest.raises(ValueError, match="analysis job attempt lineage is missing"):
        store.add_evidence_snapshot(_snapshot_payload(artifact_id))


def test_conflicting_analysis_job_attempt_bindings_are_rejected(
    store: DiagnosisStore,
) -> None:
    _add_attempt("attempt-one", 1)
    _add_attempt("attempt-two", 2)
    artifact_id = _add_artifact()
    _add_job("job-one", "attempt-one", artifact_id)
    _add_job("job-two", "attempt-two", artifact_id)

    with pytest.raises(ValueError, match="conflicting analysis job attempt lineage"):
        store.add_evidence_snapshot(_snapshot_payload(artifact_id))


def test_identity_changes_with_round_and_role(store: DiagnosisStore) -> None:
    _add_attempt("attempt-one", 1)
    incident = store.add_evidence_snapshot(_snapshot_payload())

    next_round = _snapshot_payload()
    next_round["round_index"] = 3
    verification = _snapshot_payload()
    verification["evidence_role"] = "verification"

    assert store.add_evidence_snapshot(next_round)["snapshot_id"] != incident["snapshot_id"]
    assert store.add_evidence_snapshot(verification)["snapshot_id"] != incident["snapshot_id"]


def test_caller_attempt_id_cannot_override_resolved_lineage(
    store: DiagnosisStore,
) -> None:
    _add_attempt("attempt-one", 1)
    payload = _snapshot_payload()
    payload["attempt_id"] = "caller-claimed-attempt"

    assert store.add_evidence_snapshot(payload)["attempt_id"] == "attempt-one"


def test_non_task_snapshot_preserves_unknown_attempt(store: DiagnosisStore) -> None:
    snapshot = store.add_evidence_snapshot({
        "snapshot_id": "snap-historical",
        "diagnosis_id": "diag-snapshot",
        "captured_at": datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
        "collector": "historical-import",
        "evidence_refs": [],
        "artifact_refs": [],
        "created_at": datetime(2026, 8, 20, 9, 1, tzinfo=timezone.utc),
    })

    assert snapshot["attempt_id"] is None
    assert snapshot["task_attempt_id"] is None


def test_retry_is_idempotent_but_changed_payload_conflicts(
    store: DiagnosisStore,
) -> None:
    _add_attempt("attempt-one", 1)
    payload = _snapshot_payload()

    first = store.add_evidence_snapshot(payload)
    second = store.add_evidence_snapshot(payload)
    assert second == first

    changed = _snapshot_payload()
    changed["quality"] = {"complete": False}
    with pytest.raises(ValueError, match="evidence snapshot identity conflict"):
        store.add_evidence_snapshot(changed)
