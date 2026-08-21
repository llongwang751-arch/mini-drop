from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.diagnosis.orchestrator import DiagnosisOrchestrator
from server.app.diagnosis.schemas import DiagnosisStatus
from server.app.diagnosis.store import DiagnosisStore, utcnow
from server.app.evaluation.artifacts import artifact_hash, canonical_artifact_json
from server.app.evaluation.schemas import FrozenDiagnosisArtifact
from server.app.models import (
    DiagnosisArtifactOutboxModel,
    DiagnosisSessionModel,
    FrozenDiagnosisArtifactModel,
)
from server.app.outbox_dispatcher import dispatch_artifact_once


@pytest.fixture(autouse=True)
def _db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    reset_engine()


def _insert_artifact(diagnosis_id: str = "diag-outbox") -> tuple[str, str, str]:
    now = utcnow()
    artifact_id = f"artifact:{diagnosis_id}"
    payload = FrozenDiagnosisArtifact.model_validate({
        "schema_version": "diagnosis-artifact-v1",
        "diagnosis_id": diagnosis_id,
        "case_id": "case-outbox",
        "terminal_status": "COMPLETED",
        "conclusion": {"verification": {"status": "passed"}},
        "model_version": "model-1",
        "planner_version": "planner-1",
    })
    canonical = canonical_artifact_json(payload)
    digest = artifact_hash(canonical)
    with new_session() as session:
        session.add(DiagnosisSessionModel(
            id=diagnosis_id,
            creator_id="test",
            raw_query="test",
            normalized_intent_json={},
            target_scope_json={},
            requested_time_range_json={},
            effective_time_range_json={},
            status="COMPLETED",
            policy_profile="test",
            risk_budget_json={},
            resource_budget_json={},
            budget_used_json={},
            hypothesis_graph_json={},
            child_task_ids_json=[],
            conclusion_versions_json=[],
            model_version="model-1",
            planner_version="planner-1",
            row_version=0,
            deadline_at=now + timedelta(hours=1),
            created_at=now,
            updated_at=now,
        ))
        session.flush()
        session.add(FrozenDiagnosisArtifactModel(
            id=artifact_id,
            diagnosis_id=diagnosis_id,
            schema_version=payload.schema_version,
            terminal_status=payload.terminal_status,
            canonical_json=canonical,
            artifact_hash=digest,
            created_at=now,
        ))
        session.flush()
        outbox_id = f"artifact-outbox:{artifact_id}"
        session.add(DiagnosisArtifactOutboxModel(
            id=outbox_id,
            diagnosis_id=diagnosis_id,
            artifact_id=artifact_id,
            artifact_hash=digest,
            status="PENDING",
            attempts=0,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        ))
        session.commit()
    return outbox_id, artifact_id, digest


def _freeze_detail(diagnosis_id: str) -> dict:
    return {
        "id": diagnosis_id,
        "case_id": "case-freeze",
        "status": "COMPLETED",
        "latest_conclusion": {
            "classification": "hotspot",
            "verification": {"status": "passed"},
        },
        "normalized_intent": {},
        "target_scope": {},
        "requested_time_range": {},
        "effective_time_range": {},
        "evidence": [],
        "evidence_snapshots": [],
        "probes": [],
        "risk_budget": {},
        "resource_budget": {},
        "budget_used": {},
        "model_version": "model-1",
        "planner_version": "planner-1",
    }


def _insert_freeze_session(
    diagnosis_id: str,
    *,
    status: str = "COMPLETED",
    verified: bool = True,
) -> None:
    now = utcnow()
    conclusions = [{
        "classification": "hotspot",
        "verification": {"status": "passed" if verified else "failed"},
    }]
    with new_session() as session:
        session.add(DiagnosisSessionModel(
            id=diagnosis_id,
            creator_id="test",
            raw_query="test",
            normalized_intent_json={},
            target_scope_json={},
            requested_time_range_json={},
            effective_time_range_json={},
            status=status,
            policy_profile="test",
            risk_budget_json={},
            resource_budget_json={},
            budget_used_json={},
            hypothesis_graph_json={},
            child_task_ids_json=[],
            conclusion_versions_json=conclusions,
            model_version="model-1",
            planner_version="planner-1",
            row_version=0,
            deadline_at=now + timedelta(hours=1),
            created_at=now,
            updated_at=now,
        ))
        session.commit()


def _freeze_once(monkeypatch, diagnosis_id: str = "diag-freeze") -> dict:
    _insert_freeze_session(diagnosis_id)
    store = DiagnosisStore()
    detail = _freeze_detail(diagnosis_id)
    monkeypatch.setattr(store, "get_detail", lambda _: detail)
    return store.freeze_diagnosis_artifact(diagnosis_id)


def test_artifact_dispatcher_publishes_to_event_bus():
    outbox_id, artifact_id, digest = _insert_artifact("diag-dispatch")
    from server.app.event_bus import BUS
    from server.app.outbox_dispatcher import artifact_event_bus_deliver

    assert dispatch_artifact_once(
        DiagnosisStore(), "artifact-worker", artifact_event_bus_deliver
    ) == 1

    with new_session() as session:
        row = session.get(DiagnosisArtifactOutboxModel, outbox_id)
        assert row.status == "PUBLISHED"
        assert row.published_at is not None
        assert row.worker_lease_owner is None
    event = BUS.get_history()[-1]
    assert event["event"] == "diagnosis_artifact_published"
    assert event["data"] == {
        "diagnosis_id": "diag-dispatch",
        "artifact_id": artifact_id,
        "artifact_hash": digest,
    }


def test_artifact_dispatcher_records_failure():
    outbox_id, _, _ = _insert_artifact("diag-dispatch-failure")

    def reject(_message):
        raise RuntimeError("downstream unavailable")

    assert dispatch_artifact_once(
        DiagnosisStore(), "artifact-worker", reject, max_attempts=2
    ) == 1

    with new_session() as session:
        row = session.get(DiagnosisArtifactOutboxModel, outbox_id)
        assert row.status == "FAILED"
        assert row.attempts == 1
        assert row.last_error == "downstream unavailable"
        assert row.worker_lease_owner is None


def test_claim_and_owner_validated_idempotent_ack():
    outbox_id, _, _ = _insert_artifact()
    store = DiagnosisStore()
    now = utcnow()

    claimed = store.claim_artifact_outbox("worker-a", now=now)
    assert [item["outbox_id"] for item in claimed] == [outbox_id]
    assert store.claim_artifact_outbox("worker-b", now=now) == []
    with pytest.raises(ValueError, match="lease owner mismatch"):
        store.mark_artifact_outbox_published(outbox_id, "worker-b", now=now)

    published = store.mark_artifact_outbox_published(outbox_id, "worker-a", now=now)
    assert published["status"] == "PUBLISHED"
    repeated = store.mark_artifact_outbox_published(outbox_id, "worker-a", now=now)
    assert repeated["status"] == "PUBLISHED"


def test_concurrent_file_sqlite_claim_has_single_winner(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'claim.db'}")
    reset_engine()
    init_db()
    outbox_id, _, _ = _insert_artifact("diag-concurrent-claim")
    barrier = Barrier(2)

    def claim(worker_id: str):
        barrier.wait()
        return DiagnosisStore().claim_artifact_outbox(worker_id, now=utcnow())

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(claim, worker_id)
            for worker_id in ("worker-a", "worker-b")
        ]
        results = [future.result() for future in futures]

    claimed = [item for result in results for item in result]
    assert [item["outbox_id"] for item in claimed] == [outbox_id]
    assert claimed[0]["worker_lease_owner"] in {"worker-a", "worker-b"}
    with new_session() as session:
        row = session.get(DiagnosisArtifactOutboxModel, outbox_id)
        assert row.status == "DISPATCHING"
        assert row.worker_lease_owner == claimed[0]["worker_lease_owner"]


def test_expired_lease_is_reclaimed_by_another_worker():

    outbox_id, _, _ = _insert_artifact()
    store = DiagnosisStore()
    start = utcnow()
    store.claim_artifact_outbox("worker-a", lease_seconds=10, now=start)

    claimed = store.claim_artifact_outbox(
        "worker-b", now=start + timedelta(seconds=11)
    )
    assert claimed[0]["outbox_id"] == outbox_id
    assert claimed[0]["worker_lease_owner"] == "worker-b"


def test_failure_retries_then_dead_letters_and_truncates_error():
    outbox_id, _, _ = _insert_artifact()
    store = DiagnosisStore()
    start = utcnow()
    store.claim_artifact_outbox("worker-a", now=start)
    assert store.fail_artifact_outbox(
        outbox_id, "worker-a", "x" * 3000, max_attempts=2, now=start
    ) == "FAILED"

    with new_session() as session:
        row = session.get(DiagnosisArtifactOutboxModel, outbox_id)
        assert row.attempts == 1
        assert len(row.last_error) == 2000
        assert row.next_attempt_at > start.replace(tzinfo=None)

    assert store.claim_artifact_outbox("worker-b", now=start) == []
    claimed = store.claim_artifact_outbox(
        "worker-b", now=start + timedelta(seconds=11)
    )
    assert claimed[0]["outbox_id"] == outbox_id
    assert store.fail_artifact_outbox(
        outbox_id,
        "worker-b",
        "still failing",
        max_attempts=2,
        now=start + timedelta(seconds=11),
    ) == "DEAD_LETTER"
    assert store.claim_artifact_outbox(
        "worker-c", now=start + timedelta(days=1)
    ) == []


def test_wrong_owner_and_expired_lease_cannot_fail_or_ack():
    outbox_id, _, _ = _insert_artifact()
    store = DiagnosisStore()
    start = utcnow()
    store.claim_artifact_outbox("worker-a", lease_seconds=10, now=start)

    with pytest.raises(ValueError, match="lease owner mismatch"):
        store.fail_artifact_outbox(
            outbox_id, "worker-b", "not mine", now=start
        )
    with pytest.raises(ValueError, match="lease expired"):
        store.mark_artifact_outbox_published(
            outbox_id, "worker-a", now=start + timedelta(seconds=11)
        )
    with pytest.raises(ValueError, match="lease expired"):
        store.fail_artifact_outbox(
            outbox_id,
            "worker-a",
            "too late",
            now=start + timedelta(seconds=11),
        )


def test_concurrent_same_payload_freeze_has_single_artifact_and_outbox(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'freeze.db'}")
    reset_engine()
    init_db()
    diagnosis_id = "diag-concurrent"
    _insert_freeze_session(diagnosis_id)
    detail = _freeze_detail(diagnosis_id)
    barrier = Barrier(2)

    def freeze_once(_):
        store = DiagnosisStore()
        store.get_detail = lambda _: detail
        barrier.wait()
        return store.freeze_diagnosis_artifact(diagnosis_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(freeze_once, range(2)))

    assert results[0]["artifact_id"] == results[1]["artifact_id"]
    assert results[0]["artifact_hash"] == results[1]["artifact_hash"]
    with new_session() as session:
        artifact = session.query(FrozenDiagnosisArtifactModel).one()
        outbox = session.query(DiagnosisArtifactOutboxModel).one()
        assert outbox.diagnosis_id == diagnosis_id
        assert outbox.artifact_id == artifact.id
        assert outbox.artifact_hash == artifact.artifact_hash


def test_concurrent_conflicting_freeze_keeps_immutable_winner(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'conflict.db'}")
    reset_engine()
    init_db()
    diagnosis_id = "diag-conflict"
    _insert_freeze_session(diagnosis_id)
    barrier = Barrier(2)

    def freeze_variant(classification: str):
        detail = _freeze_detail(diagnosis_id)
        detail["latest_conclusion"]["classification"] = classification
        store = DiagnosisStore()
        store.get_detail = lambda _: detail
        barrier.wait()
        try:
            return "success", store.freeze_diagnosis_artifact(diagnosis_id)
        except ValueError as exc:
            return "failure", str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(freeze_variant, classification)
            for classification in ("hotspot", "leak")
        ]
        results = [future.result() for future in futures]

    successes = [value for status, value in results if status == "success"]
    failures = [value for status, value in results if status == "failure"]
    assert len(successes) == 1
    assert len(failures) == 1
    assert "冻结产物完整性冲突" in failures[0]
    with new_session() as session:
        artifact = session.query(FrozenDiagnosisArtifactModel).one()
        outbox = session.query(DiagnosisArtifactOutboxModel).one()
        assert artifact.artifact_hash == successes[0]["artifact_hash"]
        assert outbox.artifact_id == artifact.id
        assert outbox.artifact_hash == artifact.artifact_hash


def test_freeze_recreates_missing_outbox(monkeypatch):
    frozen = _freeze_once(monkeypatch)
    outbox_id = f"artifact-outbox:{frozen['artifact_id']}"
    with new_session() as session:
        session.delete(session.get(DiagnosisArtifactOutboxModel, outbox_id))
        session.commit()

    store = DiagnosisStore()
    monkeypatch.setattr(
        store,
        "get_detail",
        lambda _: _freeze_detail("diag-freeze"),
    )
    repeated = store.freeze_diagnosis_artifact("diag-freeze")

    assert repeated["artifact_hash"] == frozen["artifact_hash"]
    with new_session() as session:
        assert session.query(FrozenDiagnosisArtifactModel).count() == 1
        outbox = session.query(DiagnosisArtifactOutboxModel).one()
        assert outbox.status == "PENDING"
        assert outbox.artifact_id == frozen["artifact_id"]
        assert outbox.artifact_hash == frozen["artifact_hash"]


def test_freeze_retry_rejects_tampered_outbox(monkeypatch):
    frozen = _freeze_once(monkeypatch)
    with new_session() as session:
        outbox = session.query(DiagnosisArtifactOutboxModel).one()
        outbox.artifact_hash = "sha256:" + "0" * 64
        session.commit()

    store = DiagnosisStore()
    monkeypatch.setattr(
        store,
        "get_detail",
        lambda _: _freeze_detail("diag-freeze"),
    )
    with pytest.raises(ValueError, match="通知完整性冲突"):
        store.freeze_diagnosis_artifact("diag-freeze")

    with new_session() as session:
        assert session.query(FrozenDiagnosisArtifactModel).count() == 1
        assert session.query(DiagnosisArtifactOutboxModel).count() == 1


def test_claim_quarantines_tampered_canonical_artifact_without_starving_healthy():
    poison_id, _, _ = _insert_artifact("diag-poison-canonical")
    with new_session() as session:
        artifact = session.query(FrozenDiagnosisArtifactModel).filter(
            FrozenDiagnosisArtifactModel.diagnosis_id == "diag-poison-canonical"
        ).one()
        artifact.canonical_json = artifact.canonical_json.replace(
            '"model-1"', '"model-2"', 1
        )
        session.commit()
    healthy_id, _, _ = _insert_artifact("diag-healthy-canonical")

    store = DiagnosisStore()
    claimed = store.claim_artifact_outbox("worker-a", now=utcnow())

    assert [item["outbox_id"] for item in claimed] == [healthy_id]
    assert store.claim_artifact_outbox("worker-b", now=utcnow()) == []
    with new_session() as session:
        poison = session.get(DiagnosisArtifactOutboxModel, poison_id)
        assert poison.status == "DEAD_LETTER"
        assert poison.last_error == "INTEGRITY_ARTIFACT_HASH_INVALID"
        assert poison.attempts == 0
        assert poison.worker_lease_owner is None
        assert poison.worker_lease_expires_at is None


def test_claim_quarantines_lineage_mismatch_without_starving_healthy():
    poison_id, _, _ = _insert_artifact("diag-poison-lineage")
    with new_session() as session:
        row = session.get(DiagnosisArtifactOutboxModel, poison_id)
        row.artifact_hash = "sha256:" + "0" * 64
        session.commit()
    healthy_id, _, _ = _insert_artifact("diag-healthy-lineage")

    claimed = DiagnosisStore().claim_artifact_outbox(
        "worker-a", now=utcnow()
    )

    assert [item["outbox_id"] for item in claimed] == [healthy_id]
    with new_session() as session:
        poison = session.get(DiagnosisArtifactOutboxModel, poison_id)
        assert poison.status == "DEAD_LETTER"
        assert poison.last_error == "INTEGRITY_ARTIFACT_LINEAGE_MISMATCH"
        assert poison.attempts == 0
        assert poison.worker_lease_owner is None
        assert poison.worker_lease_expires_at is None


def test_terminal_artifact_reconciliation_freezes_once_and_is_idempotent():
    diagnosis_id = "diag-reconcile"
    _insert_freeze_session(diagnosis_id)
    orchestrator = DiagnosisOrchestrator(None, DiagnosisStore())

    assert orchestrator.reconcile_terminal_artifacts() == {
        "scanned": 1, "frozen": 1, "skipped": 0, "failed": 0,
    }
    assert orchestrator.reconcile_terminal_artifacts() == {
        "scanned": 0, "frozen": 0, "skipped": 0, "failed": 0,
    }
    with new_session() as session:
        artifact = session.query(FrozenDiagnosisArtifactModel).one()
        outbox = session.query(DiagnosisArtifactOutboxModel).one()
        assert artifact.diagnosis_id == diagnosis_id
        assert outbox.artifact_id == artifact.id
        assert outbox.artifact_hash == artifact.artifact_hash


def test_terminal_artifact_reconciliation_excludes_nonterminal_and_skips_unverified():
    _insert_freeze_session("diag-active", status="ANALYZING")
    _insert_freeze_session("diag-unverified", verified=False)

    outcome = DiagnosisOrchestrator(
        None, DiagnosisStore()
    ).reconcile_terminal_artifacts()

    assert outcome == {
        "scanned": 1, "frozen": 0, "skipped": 1, "failed": 0,
    }
    with new_session() as session:
        assert session.query(FrozenDiagnosisArtifactModel).count() == 0
        active = session.get(DiagnosisSessionModel, "diag-active")
        assert active.lease_owner is None


def test_terminal_artifact_reconciliation_failure_does_not_starve_later_candidate(
    monkeypatch,
):
    _insert_freeze_session("diag-broken")
    _insert_freeze_session("diag-healthy")
    store = DiagnosisStore()
    original_freeze = store.freeze_diagnosis_artifact

    def freeze(diagnosis_id: str):
        if diagnosis_id == "diag-broken":
            raise RuntimeError("cannot freeze")
        return original_freeze(diagnosis_id)

    monkeypatch.setattr(store, "freeze_diagnosis_artifact", freeze)
    original_record = store.record_event

    def record(diagnosis_id: str, event_type: str, payload=None):
        if diagnosis_id == "diag-broken":
            raise RuntimeError("cannot audit")
        return original_record(diagnosis_id, event_type, payload)

    monkeypatch.setattr(store, "record_event", record)
    outcome = DiagnosisOrchestrator(None, store).reconcile_terminal_artifacts()

    assert outcome == {
        "scanned": 2, "frozen": 1, "skipped": 0, "failed": 1,
    }
    with new_session() as session:
        artifact = session.query(FrozenDiagnosisArtifactModel).one()
        assert artifact.diagnosis_id == "diag-healthy"


def test_terminal_transition_survives_freeze_and_audit_failure(monkeypatch):
    diagnosis_id = "diag-live-freeze-failure"
    _insert_freeze_session(diagnosis_id, status="CONCLUDING")
    store = DiagnosisStore()
    monkeypatch.setattr(
        store,
        "freeze_diagnosis_artifact",
        lambda _: (_ for _ in ()).throw(RuntimeError("freeze unavailable")),
    )
    monkeypatch.setattr(
        store,
        "record_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit unavailable")),
    )

    DiagnosisOrchestrator(None, store)._transition(
        diagnosis_id,
        DiagnosisStatus.COMPLETED,
        "diagnosis_completed",
    )

    assert store.get_session(diagnosis_id)["status"] == "COMPLETED"
    with new_session() as session:
        assert session.query(FrozenDiagnosisArtifactModel).count() == 0


def test_concurrent_terminal_reconciliation_keeps_one_immutable_winner(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'reconcile.db'}")
    reset_engine()
    init_db()
    diagnosis_id = "diag-concurrent-reconcile"
    _insert_freeze_session(diagnosis_id)
    barrier = Barrier(2)

    def reconcile(_):
        orchestrator = DiagnosisOrchestrator(None, DiagnosisStore())
        barrier.wait()
        return orchestrator.reconcile_terminal_artifacts()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(reconcile, range(2)))

    assert sum(item["failed"] for item in outcomes) == 0
    with new_session() as session:
        assert session.query(FrozenDiagnosisArtifactModel).count() == 1
        assert session.query(DiagnosisArtifactOutboxModel).count() == 1
