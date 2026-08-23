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
    DiagnosisEvidenceModel,
    DiagnosisEvidenceReviewModel,
    DiagnosisEvidenceSnapshotModel,
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
        for evidence_id in ("ev-1", "ev-2"):
            session.add(DiagnosisEvidenceModel(
                id=evidence_id,
                diagnosis_id="diag-snapshot",
                source_type="metric",
                source_system="test",
                evidence_role="incident",
                target_json={"pid": 123},
                event_time_range_json={},
                ingestion_time=timestamp,
                query_or_probe="bounded test probe",
                raw_artifact_ref=None,
                derived_artifact_ref="tasks/task-snapshot/sys_metrics.json",
                derivation_version="v1",
                observed_value_json={"value": evidence_id},
                baseline_value_json={},
                anomaly_score_json={},
                data_quality_json={"complete": True},
                integrity_hash=f"sha256:{'e' * 64}",
                claim_links_json=[],
                lifecycle_status="ACTIVE",
                trust_status="UNREVIEWED",
                review_revision=0,
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


def _add_artifact(
    *,
    integrity_status: str = "VERIFIED",
    sha256: str | None = "a" * 64,
    task_id: str = "task-snapshot",
    object_key: str | None = None,
    size_bytes: int = 128,
) -> int:
    timestamp = datetime(2026, 8, 21, 10, 2, tzinfo=timezone.utc)
    with new_session() as session:
        artifact = ArtifactModel(
            task_id=task_id,
            artifact_type="sys_metrics",
            object_key=object_key or f"tasks/{task_id}/sys_metrics.json",
            content_type="application/json",
            size_bytes=size_bytes,
            sha256=sha256,
            integrity_status=integrity_status,
            integrity_reason="verified in test",
            meta_json={},
            created_at=timestamp,
        )
        session.add(artifact)
        session.commit()
        return artifact.id


def _add_job(
    job_id: str,
    attempt_id: str | None,
    artifact_id: int,
    *,
    analyzer_type: str = "collector.sys_metrics",
    analyzer_version: str = "1.0.0",
) -> None:
    timestamp = datetime(2026, 8, 21, 10, 3, tzinfo=timezone.utc)
    with new_session() as session:
        session.add(AnalysisJobModel(
            id=job_id,
            task_id="task-snapshot",
            task_attempt_id=attempt_id,
            analyzer_type=analyzer_type,
            analyzer_version=analyzer_version,
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
    if artifact_id is None:
        artifact_id = _add_artifact()
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
        "artifact_ids": [artifact_id],
    }
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
    with new_session() as session:
        evidence_rows = session.query(DiagnosisEvidenceModel).order_by(
            DiagnosisEvidenceModel.id
        ).all()
        reviews = session.query(DiagnosisEvidenceReviewModel).order_by(
            DiagnosisEvidenceReviewModel.evidence_id
        ).all()
        persisted_snapshots = session.query(DiagnosisEvidenceSnapshotModel).all()
    assert [(row.id, row.trust_status, row.review_revision) for row in evidence_rows] == [
        ("ev-1", "TRUSTED", 1),
        ("ev-2", "TRUSTED", 1),
    ]
    assert [(row.evidence_id, row.revision) for row in reviews] == [
        ("ev-1", 1),
        ("ev-2", 1),
    ]
    assert len(persisted_snapshots) == 1


def test_snapshot_persists_database_derived_provenance_deterministically(
    store: DiagnosisStore,
) -> None:
    _add_attempt("attempt-one", 1)
    artifact_two = _add_artifact(
        object_key="tasks/task-snapshot/z.json",
        sha256="B" * 64,
        size_bytes=222,
    )
    artifact_one = _add_artifact(
        object_key="tasks/task-snapshot/a.json",
        sha256="a" * 64,
        size_bytes=111,
    )
    _add_job(
        "job-z",
        "attempt-one",
        artifact_two,
        analyzer_type="collector.z",
        analyzer_version="2.0.0",
    )
    _add_job(
        "job-a",
        "attempt-one",
        artifact_one,
        analyzer_type="collector.a",
        analyzer_version="1.0.0",
    )
    payload = _snapshot_payload(artifact_two)
    payload["artifact_ids"] = [artifact_two, artifact_one]
    payload["artifact_refs"] = ["caller/forged.json"]
    payload["artifact_provenance"] = [{"artifact_id": 999}]
    payload["analysis_provenance"] = [{"analysis_job_id": "forged"}]

    snapshot = store.add_evidence_snapshot(payload)

    assert snapshot["artifact_refs"] == [
        "tasks/task-snapshot/a.json",
        "tasks/task-snapshot/z.json",
    ]
    assert snapshot["artifact_provenance"] == [
        {
            "artifact_id": artifact_two,
            "task_id": "task-snapshot",
            "object_key": "tasks/task-snapshot/z.json",
            "sha256": "b" * 64,
            "size_bytes": 222,
            "integrity_status": "VERIFIED",
            "producing_task_attempt_id": "attempt-one",
        },
        {
            "artifact_id": artifact_one,
            "task_id": "task-snapshot",
            "object_key": "tasks/task-snapshot/a.json",
            "sha256": "a" * 64,
            "size_bytes": 111,
            "integrity_status": "VERIFIED",
            "producing_task_attempt_id": "attempt-one",
        },
    ]
    assert snapshot["analysis_provenance"] == [
        {
            "analysis_job_id": "job-a",
            "task_id": "task-snapshot",
            "task_attempt_id": "attempt-one",
            "analyzer_type": "collector.a",
            "analyzer_version": "1.0.0",
            "input_artifact_ids": [artifact_one],
            "output_artifact_ids": [artifact_one],
        },
        {
            "analysis_job_id": "job-z",
            "task_id": "task-snapshot",
            "task_attempt_id": "attempt-one",
            "analyzer_type": "collector.z",
            "analyzer_version": "2.0.0",
            "input_artifact_ids": [artifact_two],
            "output_artifact_ids": [artifact_two],
        },
    ]
    assert store.add_evidence_snapshot(payload) == snapshot


def test_database_provenance_change_conflicts_with_snapshot_identity(
    store: DiagnosisStore,
) -> None:
    _add_attempt("attempt-one", 1)
    artifact_id = _add_artifact()
    _add_job("job-one", "attempt-one", artifact_id)
    payload = _snapshot_payload(artifact_id)
    store.add_evidence_snapshot(payload)

    with new_session() as session:
        artifact = session.get(ArtifactModel, artifact_id)
        artifact.object_key = "tasks/task-snapshot/replaced.json"
        job = session.get(AnalysisJobModel, "job-one")
        job.analyzer_version = "2.0.0"
        session.commit()

    with pytest.raises(ValueError, match="evidence snapshot identity conflict"):
        store.add_evidence_snapshot(payload)


def test_task_snapshot_requires_artifact_ids(store: DiagnosisStore) -> None:
    _add_attempt("attempt-one", 1)
    payload = _snapshot_payload()
    payload["artifact_ids"] = []

    with pytest.raises(ValueError, match="requires verified artifacts"):
        store.add_evidence_snapshot(payload)

    _assert_no_snapshot_or_promotion()


@pytest.mark.parametrize(
    ("integrity_status", "sha256", "message"),
    [
        ("DECLARED", "a" * 64, "must be VERIFIED"),
        ("LEGACY_UNVERIFIED", "a" * 64, "must be VERIFIED"),
        ("VERIFIED", None, "valid SHA-256"),
        ("VERIFIED", "not-a-digest", "valid SHA-256"),
    ],
)
def test_task_snapshot_requires_verified_artifact_with_valid_sha(
    store: DiagnosisStore,
    integrity_status: str,
    sha256: str | None,
    message: str,
) -> None:
    _add_attempt("attempt-one", 1)
    artifact_id = _add_artifact(
        integrity_status=integrity_status,
        sha256=sha256,
    )

    with pytest.raises(ValueError, match=message):
        store.add_evidence_snapshot(_snapshot_payload(artifact_id))

    _assert_no_snapshot_or_promotion()


def test_task_snapshot_rejects_cross_task_artifact(store: DiagnosisStore) -> None:
    _add_attempt("attempt-one", 1)
    timestamp = datetime(2026, 8, 21, 10, 2, tzinfo=timezone.utc)
    with new_session() as session:
        session.add(TaskModel(
            id="task-other",
            name="other task",
            agent_id="agent-snapshot",
            target_pid=456,
            collector_type="sys_metrics",
            status="DONE",
            status_reason="completed",
            request_params={},
            created_at=timestamp,
            started_at=timestamp,
            finished_at=timestamp,
        ))
        session.commit()
    artifact_id = _add_artifact(task_id="task-other")

    with pytest.raises(ValueError, match="do not belong to task"):
        store.add_evidence_snapshot(_snapshot_payload(artifact_id))

    _assert_no_snapshot_or_promotion()


def test_task_snapshot_rejects_missing_or_foreign_evidence(
    store: DiagnosisStore,
) -> None:
    _add_attempt("attempt-one", 1)
    payload = _snapshot_payload()
    payload["evidence_refs"] = ["ev-1", "missing"]

    with pytest.raises(ValueError, match="does not belong to diagnosis"):
        store.add_evidence_snapshot(payload)
    _assert_no_snapshot_or_promotion()

    store.create_session({
        "diagnosis_id": "diag-other",
        "creator_id": "test",
        "raw_query": "other diagnosis",
        "status": "COLLECTING",
        "policy_profile": "test",
        "model_version": "test",
        "planner_version": "test",
        "deadline_at": datetime(2026, 8, 21, 11, 0, tzinfo=timezone.utc),
    })
    with new_session() as session:
        session.add(DiagnosisEvidenceModel(
            id="ev-other",
            diagnosis_id="diag-other",
            source_type="metric",
            source_system="test",
            evidence_role="incident",
            target_json={},
            event_time_range_json={},
            ingestion_time=datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc),
            query_or_probe="probe",
            derivation_version="v1",
            observed_value_json={},
            baseline_value_json={},
            anomaly_score_json={},
            data_quality_json={},
            integrity_hash=f"sha256:{'f' * 64}",
            claim_links_json=[],
            lifecycle_status="ACTIVE",
            trust_status="UNREVIEWED",
            review_revision=0,
        ))
        session.commit()
    payload["evidence_refs"] = ["ev-1", "ev-other"]
    with pytest.raises(ValueError, match="does not belong to diagnosis"):
        store.add_evidence_snapshot(payload)
    _assert_no_snapshot_or_promotion()


def test_promotion_failure_rolls_back_snapshot_and_all_reviews(
    store: DiagnosisStore,
) -> None:
    _add_attempt("attempt-one", 1)
    store.review_evidence(
        diagnosis_id="diag-snapshot",
        evidence_id="ev-2",
        lifecycle_status="ACTIVE",
        trust_status="LOW_TRUST",
        reviewer_id="security-reviewer",
    )

    with pytest.raises(ValueError, match="cannot be promoted from LOW_TRUST"):
        store.add_evidence_snapshot(_snapshot_payload())

    with new_session() as session:
        assert session.query(DiagnosisEvidenceSnapshotModel).count() == 0
        ev1 = session.get(DiagnosisEvidenceModel, "ev-1")
        ev2 = session.get(DiagnosisEvidenceModel, "ev-2")
        reviews = session.query(DiagnosisEvidenceReviewModel).all()
    assert (ev1.trust_status, ev1.review_revision) == ("UNREVIEWED", 0)
    assert (ev2.trust_status, ev2.review_revision) == ("LOW_TRUST", 1)
    assert [(row.evidence_id, row.revision) for row in reviews] == [("ev-2", 1)]


def _assert_no_snapshot_or_promotion() -> None:
    with new_session() as session:
        assert session.query(DiagnosisEvidenceSnapshotModel).count() == 0
        assert session.query(DiagnosisEvidenceReviewModel).count() == 0
        assert {
            row.trust_status for row in session.query(DiagnosisEvidenceModel).all()
        } == {"UNREVIEWED"}


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
    assert snapshot["artifact_provenance"] == []
    assert snapshot["analysis_provenance"] == []


def test_retry_is_idempotent_but_changed_payload_conflicts(
    store: DiagnosisStore,
) -> None:
    _add_attempt("attempt-one", 1)
    payload = _snapshot_payload()

    first = store.add_evidence_snapshot(payload)
    second = store.add_evidence_snapshot(payload)
    assert second == first
    with new_session() as session:
        assert session.query(DiagnosisEvidenceSnapshotModel).count() == 1
        assert session.query(DiagnosisEvidenceReviewModel).count() == 2
        assert {
            row.review_revision
            for row in session.query(DiagnosisEvidenceModel).all()
        } == {1}

    changed = dict(payload)
    changed["artifact_ids"] = list(payload["artifact_ids"])
    changed["quality"] = {"complete": False}
    with pytest.raises(ValueError, match="evidence snapshot identity conflict"):
        store.add_evidence_snapshot(changed)
