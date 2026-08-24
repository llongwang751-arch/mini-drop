from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

import server.app.outbox_dispatcher as outbox_dispatcher

from server.app.database import init_db, new_session, reset_engine
from server.app.diagnosis.orchestrator import DiagnosisOrchestrator
from server.app.diagnosis.schemas import DiagnosisStatus
from server.app.diagnosis.store import DiagnosisStore, utcnow
from server.app.evaluation.artifacts import artifact_hash, canonical_artifact_json
from server.app.evaluation.schemas import FrozenDiagnosisArtifact
from server.app.models import (
    DiagnosisArtifactOutboxModel,
    DiagnosisArtifactRevocationModel,
    DiagnosisArtifactRevocationOutboxModel,
    DiagnosisEvidenceModel,
    DiagnosisEvidenceReviewModel,
    DiagnosisSessionModel,
    FrozenDiagnosisArtifactModel,
)
from server.app.outbox_dispatcher import (
    dispatch_artifact_once,
    dispatch_artifact_revocation_once,
    revocation_event_bus_deliver,
)


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


def _insert_publishable_artifact(
    diagnosis_id: str,
    *,
    outbox_status: str = "PENDING",
) -> tuple[str, str, str, str]:
    now = utcnow()
    artifact_id = f"artifact:{diagnosis_id}"
    evidence_id = f"evidence:{diagnosis_id}"
    conclusion_hash = "sha256:" + "c" * 64
    payload = FrozenDiagnosisArtifact.model_validate({
        "schema_version": "diagnosis-artifact-v1",
        "diagnosis_id": diagnosis_id,
        "case_id": "case-publishable",
        "terminal_status": "COMPLETED",
        "conclusion": {
            "integrity_hash": conclusion_hash,
            "evidence_refs": [evidence_id],
            "verification": {"status": "passed"},
        },
        "evidence": [{
            "evidence_id": evidence_id,
            "diagnosis_id": diagnosis_id,
            "source_type": "metric",
            "source_system": "test",
            "evidence_role": "incident",
            "target": {},
            "event_time_range": {},
            "ingestion_time": now,
            "query_or_probe": "test probe",
            "derivation_version": "v1",
            "observed_value": {"value": 1},
            "baseline_value": {},
            "anomaly_score": {},
            "data_quality": {"complete": True},
            "integrity_hash": "sha256:" + "e" * 64,
            "claim_links": [],
            "lifecycle_status": "ACTIVE",
            "trust_status": "TRUSTED",
            "review_revision": 0,
        }],
        "model_version": "model-1",
        "planner_version": "planner-1",
    })
    canonical = canonical_artifact_json(payload)
    digest = artifact_hash(canonical)
    outbox_id = f"artifact-outbox:{artifact_id}"
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
            conclusion_versions_json=[payload.conclusion],
            model_version="model-1",
            planner_version="planner-1",
            row_version=0,
            deadline_at=now + timedelta(hours=1),
            created_at=now,
            updated_at=now,
        ))
        session.flush()
        session.add(DiagnosisEvidenceModel(
            id=evidence_id,
            diagnosis_id=diagnosis_id,
            source_type="metric",
            source_system="test",
            evidence_role="incident",
            target_json={},
            event_time_range_json={},
            ingestion_time=now,
            query_or_probe="test probe",
            derivation_version="v1",
            observed_value_json={"value": 1},
            baseline_value_json={},
            anomaly_score_json={},
            data_quality_json={"complete": True},
            integrity_hash="sha256:" + "e" * 64,
            claim_links_json=[],
            lifecycle_status="ACTIVE",
            trust_status="TRUSTED",
            review_revision=0,
        ))
        session.add(FrozenDiagnosisArtifactModel(
            id=artifact_id,
            diagnosis_id=diagnosis_id,
            schema_version=payload.schema_version,
            terminal_status=payload.terminal_status,
            canonical_json=canonical,
            artifact_hash=digest,
            created_at=now,
        ))
        lease_owner = "existing-worker" if outbox_status == "DISPATCHING" else None
        lease_expires_at = (
            now + timedelta(minutes=5)
            if outbox_status == "DISPATCHING"
            else None
        )
        published_at = now if outbox_status == "PUBLISHED" else None
        session.add(DiagnosisArtifactOutboxModel(
            id=outbox_id,
            diagnosis_id=diagnosis_id,
            artifact_id=artifact_id,
            artifact_hash=digest,
            status=outbox_status,
            attempts=1 if outbox_status == "FAILED" else 0,
            next_attempt_at=now,
            worker_lease_owner=lease_owner,
            worker_lease_expires_at=lease_expires_at,
            published_at=published_at,
            created_at=now,
            updated_at=now,
        ))
        session.commit()
    return outbox_id, artifact_id, digest, evidence_id


def _insert_revocation(
    diagnosis_id: str = "diag-revocation",
    *,
    status: str = "PENDING",
    review_revision: int = 1,
) -> tuple[str, str, str, str, str]:
    now = utcnow()
    evidence_id = f"evidence:{diagnosis_id}"
    conclusion_hash = "sha256:" + "c" * 64
    payload = FrozenDiagnosisArtifact.model_validate({
        "schema_version": "diagnosis-artifact-v1",
        "diagnosis_id": diagnosis_id,
        "case_id": "case-revocation",
        "terminal_status": "COMPLETED",
        "conclusion": {
            "integrity_hash": conclusion_hash,
            "evidence_refs": [evidence_id],
            "verification": {"status": "passed"},
        },
        "model_version": "model-1",
        "planner_version": "planner-1",
    })
    canonical = canonical_artifact_json(payload)
    digest = artifact_hash(canonical)
    artifact_id = f"artifact:{diagnosis_id}"
    revocation_id = f"artifact-revocation:{diagnosis_id}"
    outbox_id = f"artifact-revocation-outbox:{diagnosis_id}"
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
            conclusion_versions_json=[payload.conclusion],
            model_version="model-1",
            planner_version="planner-1",
            row_version=0,
            deadline_at=now + timedelta(hours=1),
            created_at=now,
            updated_at=now,
        ))
        session.flush()
        session.add(DiagnosisEvidenceModel(
            id=evidence_id,
            diagnosis_id=diagnosis_id,
            source_type="metric",
            source_system="test",
            evidence_role="incident",
            target_json={},
            event_time_range_json={},
            ingestion_time=now,
            query_or_probe="test probe",
            derivation_version="v1",
            observed_value_json={"value": 1},
            baseline_value_json={},
            anomaly_score_json={},
            data_quality_json={"complete": True},
            integrity_hash="sha256:" + "e" * 64,
            claim_links_json=[],
            lifecycle_status="INVALID",
            trust_status="LOW_TRUST",
            review_revision=review_revision,
            reviewed_at=now,
            reviewer_id="reviewer",
        ))
        session.flush()
        for revision in range(1, review_revision + 1):
            session.add(DiagnosisEvidenceReviewModel(
                id=f"evidence-review:{evidence_id}:{revision}",
                diagnosis_id=diagnosis_id,
                evidence_id=evidence_id,
                revision=revision,
                lifecycle_status="INVALID",
                trust_status="LOW_TRUST",
                superseded_by=None,
                reviewer_id="reviewer",
                reason=f"review {revision}",
                reviewed_at=now,
            ))
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
        session.add(DiagnosisArtifactRevocationModel(
            id=revocation_id,
            diagnosis_id=diagnosis_id,
            artifact_id=artifact_id,
            artifact_hash=digest,
            conclusion_hash=conclusion_hash,
            evidence_id=evidence_id,
            review_revision=1,
            reason="evidence invalidated",
            created_at=now,
        ))
        session.flush()
        session.add(DiagnosisArtifactRevocationOutboxModel(
            id=outbox_id,
            revocation_id=revocation_id,
            status=status,
            attempts=0,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        ))
        session.commit()
    return outbox_id, revocation_id, artifact_id, digest, evidence_id


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
        "integrity_hash": "sha256:" + "a" * 64,
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


def test_revocation_outbox_lists_complete_immutable_lineage():
    outbox_id, revocation_id, artifact_id, digest, evidence_id = (
        _insert_revocation("diag-revocation-list")
    )

    messages = DiagnosisStore().list_pending_artifact_revocation_outbox()

    assert len(messages) == 1
    message = messages[0]
    assert message["outbox_id"] == outbox_id
    assert message["revocation_id"] == revocation_id
    assert message["diagnosis_id"] == "diag-revocation-list"
    assert message["artifact_id"] == artifact_id
    assert message["artifact_hash"] == digest
    assert message["conclusion_hash"] == "sha256:" + "c" * 64
    assert message["evidence_id"] == evidence_id
    assert message["review_revision"] == 1
    assert message["reason"] == "evidence invalidated"
    assert message["status"] == "PENDING"
    assert message["attempts"] == 0


def test_revocation_outbox_missing_fact_fails_closed():
    outbox_id, _, _, _, _ = _insert_revocation("diag-revocation-missing-fact")
    with new_session() as session:
        row = session.get(DiagnosisArtifactRevocationOutboxModel, outbox_id)
        session.delete(session.get(DiagnosisArtifactRevocationModel, row.revocation_id))
        session.commit()

    with pytest.raises(ValueError, match="revocation not found"):
        DiagnosisStore().list_pending_artifact_revocation_outbox()


def test_revocation_claim_quarantines_poison_without_starving_healthy():
    poison_id, revocation_id, _, _, _ = _insert_revocation(
        "diag-revocation-poison"
    )
    with new_session() as session:
        revocation = session.get(DiagnosisArtifactRevocationModel, revocation_id)
        revocation.artifact_hash = "sha256:" + "0" * 64
        session.commit()
    healthy_id, _, _, _, _ = _insert_revocation("diag-revocation-healthy")

    claimed = DiagnosisStore().claim_artifact_revocation_outbox(
        "worker-a", limit=1, now=utcnow()
    )

    assert [item["outbox_id"] for item in claimed] == [healthy_id]
    with new_session() as session:
        poison = session.get(DiagnosisArtifactRevocationOutboxModel, poison_id)
        assert poison.status == "DEAD_LETTER"
        assert poison.last_error == "INTEGRITY_REVOCATION_LINEAGE_MISMATCH"
        assert poison.attempts == 0
        assert poison.worker_lease_owner is None
        assert poison.worker_lease_expires_at is None


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("malformed_artifact", "INTEGRITY_REVOCATION_ARTIFACT_MALFORMED"),
        ("artifact_hash", "INTEGRITY_REVOCATION_ARTIFACT_HASH_INVALID"),
        ("artifact_diagnosis", "INTEGRITY_REVOCATION_ARTIFACT_HASH_INVALID"),
        ("revocation_diagnosis", "INTEGRITY_REVOCATION_LINEAGE_MISMATCH"),
        ("revocation_artifact", "INTEGRITY_REVOCATION_ARTIFACT_NOT_FOUND"),
        ("revocation_evidence", "INTEGRITY_REVOCATION_EVIDENCE_NOT_FOUND"),
        ("revocation_conclusion", "INTEGRITY_REVOCATION_LINEAGE_MISMATCH"),
        ("review_diagnosis", "INTEGRITY_REVOCATION_LINEAGE_MISMATCH"),
        ("review_evidence", "INTEGRITY_REVOCATION_REVIEW_NOT_FOUND"),
        ("review_revision", "INTEGRITY_REVOCATION_REVIEW_NOT_FOUND"),
    ],
)
def test_revocation_claim_quarantines_integrity_variants(mutation, expected):
    outbox_id, revocation_id, artifact_id, digest, evidence_id = _insert_revocation(
        f"diag-revocation-{mutation}"
    )
    with new_session() as session:
        revocation = session.get(DiagnosisArtifactRevocationModel, revocation_id)
        artifact = session.get(FrozenDiagnosisArtifactModel, artifact_id)
        review = session.query(DiagnosisEvidenceReviewModel).filter_by(
            evidence_id=evidence_id, revision=1
        ).one()
        if mutation == "malformed_artifact":
            artifact.canonical_json = "{}"
        elif mutation == "artifact_hash":
            artifact.artifact_hash = "sha256:" + "0" * 64
        elif mutation == "artifact_diagnosis":
            artifact.canonical_json = artifact.canonical_json.replace(
                '"diagnosis_id":"diag-revocation-',
                '"diagnosis_id":"other-',
                1,
            )
        elif mutation == "revocation_diagnosis":
            revocation.diagnosis_id = "other"
        elif mutation == "revocation_artifact":
            revocation.artifact_id = "other"
        elif mutation == "revocation_evidence":
            revocation.evidence_id = "other"
        elif mutation == "revocation_conclusion":
            revocation.conclusion_hash = "sha256:" + "d" * 64
        elif mutation == "review_diagnosis":
            review.diagnosis_id = "other"
        elif mutation == "review_evidence":
            review.evidence_id = "other"
        elif mutation == "review_revision":
            revocation.review_revision = 2
        session.commit()

    claimed = DiagnosisStore().claim_artifact_revocation_outbox(
        "worker-a", now=utcnow()
    )

    assert claimed == []
    with new_session() as session:
        row = session.get(DiagnosisArtifactRevocationOutboxModel, outbox_id)
        assert row.status == "DEAD_LETTER"
        assert row.last_error == expected
        assert row.attempts == 0
        assert row.worker_lease_owner is None


def test_revocation_claim_quarantines_missing_lineage_rows():
    cases = [
        ("artifact", FrozenDiagnosisArtifactModel),
        ("evidence", DiagnosisEvidenceModel),
        ("review", DiagnosisEvidenceReviewModel),
    ]
    for suffix, model in cases:
        outbox_id, revocation_id, artifact_id, _, evidence_id = _insert_revocation(
            f"diag-revocation-missing-{suffix}"
        )
        with new_session() as session:
            if suffix == "artifact":
                session.delete(session.get(model, artifact_id))
            elif suffix == "evidence":
                session.delete(session.get(model, evidence_id))
            else:
                session.delete(session.query(model).filter_by(
                    evidence_id=evidence_id, revision=1
                ).one())
            session.commit()

        assert DiagnosisStore().claim_artifact_revocation_outbox(
            "worker-a", now=utcnow()
        ) == []
        with new_session() as session:
            row = session.get(DiagnosisArtifactRevocationOutboxModel, outbox_id)
            assert row.status == "DEAD_LETTER"
            assert row.last_error == {
                "artifact": "INTEGRITY_REVOCATION_ARTIFACT_NOT_FOUND",
                "evidence": "INTEGRITY_REVOCATION_EVIDENCE_NOT_FOUND",
                "review": "INTEGRITY_REVOCATION_REVIEW_NOT_FOUND",
            }[suffix]


def test_revocation_claim_suppresses_revocation_no_longer_required():
    outbox_id, _, _, _, evidence_id = _insert_revocation(
        "diag-revocation-claim-suppressed"
    )
    with new_session() as session:
        evidence = session.get(DiagnosisEvidenceModel, evidence_id)
        evidence.lifecycle_status = "ACTIVE"
        evidence.trust_status = "TRUSTED"
        evidence.superseded_by = None
        session.commit()

    assert DiagnosisStore().claim_artifact_revocation_outbox(
        "worker-a", now=utcnow()
    ) == []

    with new_session() as session:
        row = session.get(DiagnosisArtifactRevocationOutboxModel, outbox_id)
        assert row.status == "SUPPRESSED"
        assert row.last_error == "SUPPRESS_REVOCATION_NO_LONGER_REQUIRED"
        assert row.attempts == 0
        assert row.published_at is None
        assert row.worker_lease_owner is None
        assert row.worker_lease_expires_at is None
    assert DiagnosisStore().claim_artifact_revocation_outbox(
        "worker-b", now=utcnow() + timedelta(days=1)
    ) == []


def test_revocation_claim_suppression_does_not_starve_healthy():
    suppressed_id, _, _, _, evidence_id = _insert_revocation(
        "diag-revocation-claim-suppressed-first"
    )
    with new_session() as session:
        evidence = session.get(DiagnosisEvidenceModel, evidence_id)
        evidence.lifecycle_status = "ACTIVE"
        evidence.trust_status = "TRUSTED"
        session.commit()
    healthy_id, _, _, _, _ = _insert_revocation(
        "diag-revocation-claim-after-suppressed"
    )

    claimed = DiagnosisStore().claim_artifact_revocation_outbox(
        "worker-a", limit=1, now=utcnow()
    )

    assert [item["outbox_id"] for item in claimed] == [healthy_id]
    with new_session() as session:
        suppressed = session.get(
            DiagnosisArtifactRevocationOutboxModel, suppressed_id
        )
        assert suppressed.status == "SUPPRESSED"


def test_revocation_claim_uses_immutable_historical_review():
    outbox_id, _, _, _, evidence_id = _insert_revocation(
        "diag-revocation-history", review_revision=2
    )

    claimed = DiagnosisStore().claim_artifact_revocation_outbox(
        "worker-a", now=utcnow()
    )

    assert [item["outbox_id"] for item in claimed] == [outbox_id]
    assert claimed[0]["review_revision"] == 1
    with new_session() as session:
        evidence = session.get(DiagnosisEvidenceModel, evidence_id)
        assert evidence.review_revision == 2


def test_revocation_dispatcher_publishes_complete_event_lineage():
    outbox_id, revocation_id, artifact_id, digest, evidence_id = (
        _insert_revocation("diag-revocation-dispatch")
    )
    from server.app.event_bus import BUS

    assert dispatch_artifact_revocation_once(
        DiagnosisStore(), "revocation-worker", revocation_event_bus_deliver
    ) == 1

    with new_session() as session:
        row = session.get(DiagnosisArtifactRevocationOutboxModel, outbox_id)
        assert row.status == "PUBLISHED"
        assert row.published_at is not None
        assert row.worker_lease_owner is None
    event = BUS.get_history()[-1]
    assert event["event"] == "diagnosis_artifact_revoked"
    assert event["data"] == {
        "revocation_id": revocation_id,
        "diagnosis_id": "diag-revocation-dispatch",
        "artifact_id": artifact_id,
        "artifact_hash": digest,
        "conclusion_hash": "sha256:" + "c" * 64,
        "evidence_id": evidence_id,
        "review_revision": 1,
        "reason": "evidence invalidated",
    }


def test_revocation_dispatcher_pre_delivery_suppresses_without_delivery(monkeypatch):
    outbox_id, _, _, _, evidence_id = _insert_revocation(
        "diag-revocation-pre-delivery-suppressed"
    )
    store = DiagnosisStore()
    original_validate = store.validate_artifact_revocation_delivery

    def revalidate_after_evidence_restored(*args, **kwargs):
        with new_session() as session:
            evidence = session.get(DiagnosisEvidenceModel, evidence_id)
            evidence.lifecycle_status = "ACTIVE"
            evidence.trust_status = "TRUSTED"
            session.commit()
        return original_validate(*args, **kwargs)

    monkeypatch.setattr(
        store,
        "validate_artifact_revocation_delivery",
        revalidate_after_evidence_restored,
    )
    delivered = []

    assert dispatch_artifact_revocation_once(
        store, "worker-a", delivered.append
    ) == 1

    assert delivered == []
    with new_session() as session:
        row = session.get(DiagnosisArtifactRevocationOutboxModel, outbox_id)
        assert row.status == "SUPPRESSED"
        assert row.last_error == "SUPPRESS_REVOCATION_NO_LONGER_REQUIRED"
        assert row.attempts == 0
        assert row.published_at is None
        assert row.worker_lease_owner is None


def test_revocation_dispatcher_pre_delivery_integrity_dead_letters(monkeypatch):
    outbox_id, revocation_id, _, _, _ = _insert_revocation(
        "diag-revocation-pre-delivery-integrity"
    )
    store = DiagnosisStore()
    original_validate = store.validate_artifact_revocation_delivery

    def revalidate_after_lineage_corrupted(*args, **kwargs):
        with new_session() as session:
            revocation = session.get(DiagnosisArtifactRevocationModel, revocation_id)
            revocation.conclusion_hash = "sha256:" + "0" * 64
            session.commit()
        return original_validate(*args, **kwargs)

    monkeypatch.setattr(
        store,
        "validate_artifact_revocation_delivery",
        revalidate_after_lineage_corrupted,
    )
    delivered = []

    assert dispatch_artifact_revocation_once(
        store, "worker-a", delivered.append
    ) == 1

    assert delivered == []
    with new_session() as session:
        row = session.get(DiagnosisArtifactRevocationOutboxModel, outbox_id)
        assert row.status == "DEAD_LETTER"
        assert row.last_error == "INTEGRITY_REVOCATION_LINEAGE_MISMATCH"
        assert row.attempts == 0
        assert row.published_at is None
        assert row.worker_lease_owner is None


def test_revocation_delivery_fact_survives_semantic_change_during_delivery(monkeypatch):
    outbox_id, _, _, _, evidence_id = _insert_revocation(
        "diag-revocation-ack-suppressed"
    )
    store = DiagnosisStore()
    log_events = []
    monkeypatch.setattr(
        outbox_dispatcher,
        "_safe_log",
        lambda level, event, **fields: log_events.append((level, event, fields)),
    )

    def deliver(_message):
        with new_session() as session:
            evidence = session.get(DiagnosisEvidenceModel, evidence_id)
            evidence.lifecycle_status = "ACTIVE"
            evidence.trust_status = "TRUSTED"
            session.commit()

    assert dispatch_artifact_revocation_once(
        store, "worker-a", deliver
    ) == 1

    with new_session() as session:
        row = session.get(DiagnosisArtifactRevocationOutboxModel, outbox_id)
        assert row.status == "PUBLISHED"
        assert row.last_error is None
        assert row.published_at is not None
        assert row.worker_lease_owner is None
        assert row.worker_lease_expires_at is None
    assert [event for _, event, _ in log_events] == [
        "diagnosis_artifact_revocation_delivered"
    ]


def test_revocation_delivery_fact_survives_integrity_change_during_delivery(monkeypatch):
    outbox_id, revocation_id, _, _, _ = _insert_revocation(
        "diag-revocation-ack-integrity"
    )
    store = DiagnosisStore()
    log_events = []
    monkeypatch.setattr(
        outbox_dispatcher,
        "_safe_log",
        lambda level, event, **fields: log_events.append((level, event, fields)),
    )

    def deliver(_message):
        with new_session() as session:
            revocation = session.get(DiagnosisArtifactRevocationModel, revocation_id)
            revocation.conclusion_hash = "sha256:" + "0" * 64
            session.commit()

    assert dispatch_artifact_revocation_once(
        store, "worker-a", deliver
    ) == 1

    with new_session() as session:
        row = session.get(DiagnosisArtifactRevocationOutboxModel, outbox_id)
        assert row.status == "PUBLISHED"
        assert row.last_error is None
        assert row.published_at is not None
        assert row.worker_lease_owner is None
        assert row.worker_lease_expires_at is None
    assert [event for _, event, _ in log_events] == [
        "diagnosis_artifact_revocation_delivered"
    ]


def test_revocation_claim_recovers_expired_dispatching_lease():
    outbox_id, _, _, _, _ = _insert_revocation("diag-revocation-expired")
    store = DiagnosisStore()
    start = utcnow()
    first = store.claim_artifact_revocation_outbox(
        "worker-a", lease_seconds=10, now=start
    )
    assert [item["outbox_id"] for item in first] == [outbox_id]

    reclaimed = store.claim_artifact_revocation_outbox(
        "worker-b", now=start + timedelta(seconds=11)
    )
    assert [item["outbox_id"] for item in reclaimed] == [outbox_id]
    assert reclaimed[0]["worker_lease_owner"] == "worker-b"


def test_revocation_dispatcher_ack_failure_is_recoverable_and_redelivers_identity(
    monkeypatch,
):
    outbox_id, revocation_id, _, _, _ = _insert_revocation(
        "diag-revocation-ack-crash"
    )
    store = DiagnosisStore()
    start = utcnow()
    deliveries = []
    original_ack = store.mark_artifact_revocation_published

    def crash_before_ack(*_args, **_kwargs):
        raise RuntimeError("simulated acknowledgement crash")

    monkeypatch.setattr(
        store, "mark_artifact_revocation_published", crash_before_ack
    )
    assert dispatch_artifact_revocation_once(
        store, "worker-a", lambda message: deliveries.append(message["revocation_id"])
    ) == 0
    assert deliveries == [revocation_id]
    with new_session() as session:
        row = session.get(DiagnosisArtifactRevocationOutboxModel, outbox_id)
        assert row.status == "DISPATCHING"
        assert row.worker_lease_owner == "worker-a"
        lease_expires_at = row.worker_lease_expires_at

    assert store.claim_artifact_revocation_outbox(
        "worker-b", now=lease_expires_at
    ) == []
    reclaimed = store.claim_artifact_revocation_outbox(
        "worker-b", now=lease_expires_at + timedelta(microseconds=1)
    )
    assert [message["revocation_id"] for message in reclaimed] == [revocation_id]
    deliveries.append(reclaimed[0]["revocation_id"])
    monkeypatch.setattr(store, "mark_artifact_revocation_published", original_ack)
    acknowledged = store.mark_artifact_revocation_published(
        outbox_id,
        "worker-b",
        now=lease_expires_at + timedelta(microseconds=1),
    )

    assert deliveries == [revocation_id, revocation_id]
    assert acknowledged["status"] == "PUBLISHED"


def test_revocation_pre_delivery_gate_requires_owner_and_live_lease():
    outbox_id, _, _, _, _ = _insert_revocation(
        "diag-revocation-pre-delivery-owner"
    )
    store = DiagnosisStore()
    start = utcnow()
    store.claim_artifact_revocation_outbox(
        "worker-a", lease_seconds=10, now=start
    )

    with pytest.raises(ValueError, match="lease owner mismatch"):
        store.validate_artifact_revocation_delivery(
            outbox_id, "worker-b", now=start
        )
    with pytest.raises(ValueError, match="lease expired"):
        store.validate_artifact_revocation_delivery(
            outbox_id, "worker-a", now=start + timedelta(seconds=11)
        )
    with pytest.raises(ValueError, match="lease owner mismatch"):
        store.finalize_artifact_revocation_gate(
            outbox_id, "worker-b", now=start
        )
    with pytest.raises(ValueError, match="lease expired"):
        store.finalize_artifact_revocation_gate(
            outbox_id, "worker-a", now=start + timedelta(seconds=11)
        )


def test_revocation_failed_row_retries_only_after_backoff():
    outbox_id, _, _, _, _ = _insert_revocation("diag-revocation-retry")
    store = DiagnosisStore()
    start = utcnow()
    store.claim_artifact_revocation_outbox("worker-a", now=start)
    assert store.fail_artifact_revocation_outbox(
        outbox_id, "worker-a", "temporary failure", now=start, max_attempts=2
    ) == "FAILED"
    assert store.claim_artifact_revocation_outbox("worker-b", now=start) == []
    claimed = store.claim_artifact_revocation_outbox(
        "worker-b", now=start + timedelta(seconds=11)
    )
    assert [item["outbox_id"] for item in claimed] == [outbox_id]


def test_revocation_owner_and_lease_are_required_for_mutation():
    outbox_id, _, _, _, _ = _insert_revocation("diag-revocation-owner")
    store = DiagnosisStore()
    start = utcnow()
    store.claim_artifact_revocation_outbox("worker-a", lease_seconds=10, now=start)

    with pytest.raises(ValueError, match="lease owner mismatch"):
        store.mark_artifact_revocation_published(outbox_id, "worker-b", now=start)
    with pytest.raises(ValueError, match="lease owner mismatch"):
        store.fail_artifact_revocation_outbox(
            outbox_id, "worker-b", "not mine", now=start
        )
    with pytest.raises(ValueError, match="lease expired"):
        store.mark_artifact_revocation_published(
            outbox_id, "worker-a", now=start + timedelta(seconds=11)
        )
    with pytest.raises(ValueError, match="lease expired"):
        store.fail_artifact_revocation_outbox(
            outbox_id, "worker-a", "too late", now=start + timedelta(seconds=11)
        )


def test_revocation_ack_is_idempotent_and_failure_dead_letters():
    outbox_id, _, _, _, _ = _insert_revocation("diag-revocation-ack")
    store = DiagnosisStore()
    now = utcnow()
    store.claim_artifact_revocation_outbox("worker-a", now=now)
    published = store.mark_artifact_revocation_published(outbox_id, "worker-a", now=now)
    repeated = store.mark_artifact_revocation_published(outbox_id, "worker-a", now=now)
    assert published["status"] == repeated["status"] == "PUBLISHED"

    dead_id, _, _, _, _ = _insert_revocation("diag-revocation-dead")
    dead_now = utcnow()
    store.claim_artifact_revocation_outbox("worker-a", now=dead_now)
    assert store.fail_artifact_revocation_outbox(
        dead_id, "worker-a", "x" * 3000, max_attempts=1, now=dead_now
    ) == "DEAD_LETTER"
    with new_session() as session:
        row = session.get(DiagnosisArtifactRevocationOutboxModel, dead_id)
        assert row.attempts == 1
        assert len(row.last_error) == 2000
        assert row.worker_lease_owner is None


def test_revocation_dispatcher_records_delivery_failure():
    outbox_id, _, _, _, _ = _insert_revocation("diag-revocation-delivery-failure")

    def reject(_message):
        raise RuntimeError("revocation downstream unavailable")

    assert dispatch_artifact_revocation_once(
        DiagnosisStore(), "worker-a", reject, max_attempts=2
    ) == 1
    with new_session() as session:
        row = session.get(DiagnosisArtifactRevocationOutboxModel, outbox_id)
        assert row.status == "FAILED"
        assert row.attempts == 1
        assert row.last_error == "revocation downstream unavailable"
        assert row.worker_lease_owner is None


def test_artifact_dispatcher_publishes_complete_event_lineage():
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
    with pytest.raises(ValueError, match="lease owner mismatch"):
        store.mark_artifact_outbox_published(outbox_id, "worker-a", now=now)

    delivering = store.mark_artifact_outbox_delivering(
        outbox_id, "worker-a", now=now
    )
    assert delivering["status"] == "DELIVERING"
    published = store.mark_artifact_outbox_published(outbox_id, "worker-a", now=now)
    assert published["status"] == "PUBLISHED"
    repeated = store.mark_artifact_outbox_published(outbox_id, "worker-a", now=now)
    assert repeated["status"] == "PUBLISHED"


@pytest.mark.parametrize(
    ("initial_status", "expected_status"),
    [
        ("PENDING", "SUPPRESSED"),
        ("FAILED", "SUPPRESSED"),
        ("DISPATCHING", "SUPPRESSED"),
        ("PUBLISHED", "PUBLISHED"),
    ],
)
def test_evidence_invalidation_suppresses_only_unpublished_artifact_ready_rows(
    initial_status,
    expected_status,
):
    diagnosis_id = f"diag-review-suppression-{initial_status.lower()}"
    outbox_id, artifact_id, _, evidence_id = _insert_publishable_artifact(
        diagnosis_id,
        outbox_status=initial_status,
    )
    with new_session() as session:
        original_published_at = session.get(
            DiagnosisArtifactOutboxModel, outbox_id
        ).published_at

    DiagnosisStore().review_evidence(
        diagnosis_id=diagnosis_id,
        evidence_id=evidence_id,
        lifecycle_status="INVALID",
        trust_status="LOW_TRUST",
        reviewer_id="reviewer",
        reason="evidence invalidated",
        expected_revision=0,
    )

    with new_session() as session:
        row = session.get(DiagnosisArtifactOutboxModel, outbox_id)
        assert row.status == expected_status
        assert session.query(DiagnosisArtifactRevocationModel).filter(
            DiagnosisArtifactRevocationModel.artifact_id == artifact_id
        ).count() == 1
        if expected_status == "SUPPRESSED":
            assert row.last_error == "SUPPRESS_ARTIFACT_REVOKED"
            assert row.published_at is None
            assert row.worker_lease_owner is None
            assert row.worker_lease_expires_at is None
        else:
            assert row.last_error is None
            assert row.published_at == original_published_at


def test_claim_suppresses_semantically_ineligible_artifact_without_starving_healthy():
    poison_id, _, _, evidence_id = _insert_publishable_artifact(
        "diag-semantic-poison"
    )
    with new_session() as session:
        evidence = session.get(DiagnosisEvidenceModel, evidence_id)
        evidence.lifecycle_status = "INVALID"
        evidence.trust_status = "LOW_TRUST"
        session.commit()
    healthy_id, _, _, _ = _insert_publishable_artifact(
        "diag-semantic-healthy"
    )

    claimed = DiagnosisStore().claim_artifact_outbox(
        "worker-a", now=utcnow()
    )

    assert [item["outbox_id"] for item in claimed] == [healthy_id]
    with new_session() as session:
        poison = session.get(DiagnosisArtifactOutboxModel, poison_id)
        assert poison.status == "SUPPRESSED"
        assert poison.last_error == "SUPPRESS_ARTIFACT_EVIDENCE_INELIGIBLE"
        assert poison.worker_lease_owner is None
        assert poison.worker_lease_expires_at is None


def test_artifact_dispatcher_pre_delivery_semantic_gate_blocks_delivery(monkeypatch):
    outbox_id, _, _, evidence_id = _insert_publishable_artifact(
        "diag-artifact-pre-delivery-semantic"
    )
    store = DiagnosisStore()
    original_validate = store.validate_artifact_delivery

    def invalidate_before_validation(*args, **kwargs):
        with new_session() as session:
            evidence = session.get(DiagnosisEvidenceModel, evidence_id)
            evidence.lifecycle_status = "INVALID"
            evidence.trust_status = "LOW_TRUST"
            session.commit()
        return original_validate(*args, **kwargs)

    monkeypatch.setattr(store, "validate_artifact_delivery", invalidate_before_validation)
    delivered = []

    assert dispatch_artifact_once(store, "worker-a", delivered.append) == 1

    assert delivered == []
    with new_session() as session:
        row = session.get(DiagnosisArtifactOutboxModel, outbox_id)
        assert row.status == "SUPPRESSED"
        assert row.last_error == "SUPPRESS_ARTIFACT_EVIDENCE_INELIGIBLE"
        assert row.published_at is None
        assert row.worker_lease_owner is None
        assert row.worker_lease_expires_at is None


def test_artifact_dispatcher_pre_delivery_integrity_gate_blocks_delivery(monkeypatch):
    outbox_id, artifact_id, _, _ = _insert_publishable_artifact(
        "diag-artifact-pre-delivery-integrity"
    )
    store = DiagnosisStore()
    original_validate = store.validate_artifact_delivery

    def corrupt_before_validation(*args, **kwargs):
        with new_session() as session:
            artifact = session.get(FrozenDiagnosisArtifactModel, artifact_id)
            artifact.canonical_json = artifact.canonical_json.replace(
                '"model-1"', '"model-2"', 1
            )
            session.commit()
        return original_validate(*args, **kwargs)

    monkeypatch.setattr(store, "validate_artifact_delivery", corrupt_before_validation)
    delivered = []

    assert dispatch_artifact_once(store, "worker-a", delivered.append) == 1

    assert delivered == []
    with new_session() as session:
        row = session.get(DiagnosisArtifactOutboxModel, outbox_id)
        assert row.status == "DEAD_LETTER"
        assert row.last_error == "INTEGRITY_ARTIFACT_HASH_INVALID"
        assert row.published_at is None
        assert row.worker_lease_owner is None
        assert row.worker_lease_expires_at is None


def test_artifact_delivery_fact_survives_evidence_invalidation_during_delivery():
    diagnosis_id = "diag-artifact-delivery-invalidation"
    outbox_id, artifact_id, _, evidence_id = _insert_publishable_artifact(
        diagnosis_id
    )
    store = DiagnosisStore()

    def deliver(_message):
        store.review_evidence(
            diagnosis_id=diagnosis_id,
            evidence_id=evidence_id,
            lifecycle_status="INVALID",
            trust_status="LOW_TRUST",
            reviewer_id="reviewer",
            reason="invalidated during downstream delivery",
            expected_revision=0,
        )

    assert dispatch_artifact_once(store, "worker-a", deliver) == 1

    with new_session() as session:
        row = session.get(DiagnosisArtifactOutboxModel, outbox_id)
        assert row.status == "PUBLISHED"
        assert row.last_error is None
        assert row.published_at is not None
        assert row.worker_lease_owner is None
        assert row.worker_lease_expires_at is None
        assert session.query(DiagnosisArtifactRevocationModel).filter(
            DiagnosisArtifactRevocationModel.artifact_id == artifact_id
        ).count() == 1


def test_artifact_delivery_failure_after_boundary_is_retryable():
    outbox_id, _, _ = _insert_artifact("diag-artifact-delivering-failure")

    def reject(_message):
        with new_session() as session:
            row = session.get(DiagnosisArtifactOutboxModel, outbox_id)
            assert row.status == "DELIVERING"
        raise RuntimeError("downstream unavailable after delivery boundary")

    assert dispatch_artifact_once(
        DiagnosisStore(), "worker-a", reject, max_attempts=2
    ) == 1

    with new_session() as session:
        row = session.get(DiagnosisArtifactOutboxModel, outbox_id)
        assert row.status == "FAILED"
        assert row.attempts == 1
        assert row.last_error == "downstream unavailable after delivery boundary"
        assert row.worker_lease_owner is None
        assert row.worker_lease_expires_at is None


def test_expired_delivering_lease_is_reclaimed_by_another_worker():
    outbox_id, _, _ = _insert_artifact("diag-artifact-delivering-expired")
    store = DiagnosisStore()
    start = utcnow()
    store.claim_artifact_outbox("worker-a", lease_seconds=10, now=start)
    delivering = store.mark_artifact_outbox_delivering(
        outbox_id, "worker-a", now=start
    )
    assert delivering["status"] == "DELIVERING"

    assert store.claim_artifact_outbox(
        "worker-b", now=start + timedelta(seconds=10)
    ) == []
    reclaimed = store.claim_artifact_outbox(
        "worker-b", now=start + timedelta(seconds=10, microseconds=1)
    )

    assert [message["outbox_id"] for message in reclaimed] == [outbox_id]
    assert reclaimed[0]["status"] == "DELIVERING"
    assert reclaimed[0]["worker_lease_owner"] == "worker-b"


def test_delivering_recovery_ignores_later_gate_closure():
    diagnosis_id = "diag-artifact-delivering-invalidated"
    outbox_id, artifact_id, _, evidence_id = _insert_publishable_artifact(
        diagnosis_id
    )
    store = DiagnosisStore()
    start = utcnow()
    store.claim_artifact_outbox("worker-a", lease_seconds=10, now=start)
    store.mark_artifact_outbox_delivering(outbox_id, "worker-a", now=start)
    store.review_evidence(
        diagnosis_id=diagnosis_id,
        evidence_id=evidence_id,
        lifecycle_status="INVALID",
        trust_status="LOW_TRUST",
        reviewer_id="reviewer",
        reason="invalidated after external delivery started",
        expected_revision=0,
    )

    reclaimed = store.claim_artifact_outbox(
        "worker-b", now=start + timedelta(seconds=11)
    )

    assert [message["outbox_id"] for message in reclaimed] == [outbox_id]
    assert reclaimed[0]["status"] == "DELIVERING"
    with new_session() as session:
        row = session.get(DiagnosisArtifactOutboxModel, outbox_id)
        assert row.status == "DELIVERING"
        assert row.last_error is None
        assert session.query(DiagnosisArtifactRevocationModel).filter(
            DiagnosisArtifactRevocationModel.artifact_id == artifact_id
        ).count() == 1


def test_artifact_ack_failure_redelivers_same_identity_after_lease_expiry(monkeypatch):
    outbox_id, artifact_id, digest = _insert_artifact(
        "diag-artifact-delivering-ack-crash"
    )
    store = DiagnosisStore()
    deliveries = []
    original_ack = store.mark_artifact_outbox_published

    def crash_before_ack(*_args, **_kwargs):
        raise RuntimeError("simulated acknowledgement crash")

    monkeypatch.setattr(store, "mark_artifact_outbox_published", crash_before_ack)
    assert dispatch_artifact_once(
        store,
        "worker-a",
        lambda message: deliveries.append(
            (message["artifact_id"], message["artifact_hash"])
        ),
    ) == 0
    assert deliveries == [(artifact_id, digest)]
    with new_session() as session:
        row = session.get(DiagnosisArtifactOutboxModel, outbox_id)
        assert row.status == "DELIVERING"
        lease_expires_at = row.worker_lease_expires_at

    assert store.claim_artifact_outbox(
        "worker-b", now=lease_expires_at
    ) == []
    reclaimed = store.claim_artifact_outbox(
        "worker-b", now=lease_expires_at + timedelta(microseconds=1)
    )
    assert [message["artifact_id"] for message in reclaimed] == [artifact_id]
    assert reclaimed[0]["status"] == "DELIVERING"
    assert reclaimed[0]["artifact_hash"] == digest
    deliveries.append((reclaimed[0]["artifact_id"], reclaimed[0]["artifact_hash"]))
    monkeypatch.setattr(store, "mark_artifact_outbox_published", original_ack)
    acknowledged = store.mark_artifact_outbox_published(
        outbox_id,
        "worker-b",
        now=lease_expires_at + timedelta(microseconds=1),
    )

    assert deliveries == [(artifact_id, digest), (artifact_id, digest)]
    assert acknowledged["status"] == "PUBLISHED"


def test_delivery_boundary_atomically_persists_newly_closed_gate(monkeypatch):
    outbox_id, _, _, evidence_id = _insert_publishable_artifact(
        "diag-artifact-boundary-gate"
    )
    store = DiagnosisStore()
    original_boundary = store.mark_artifact_outbox_delivering

    def invalidate_before_boundary(*args, **kwargs):
        with new_session() as session:
            evidence = session.get(DiagnosisEvidenceModel, evidence_id)
            evidence.lifecycle_status = "INVALID"
            evidence.trust_status = "LOW_TRUST"
            session.commit()
        return original_boundary(*args, **kwargs)

    monkeypatch.setattr(
        store, "mark_artifact_outbox_delivering", invalidate_before_boundary
    )
    delivered = []

    assert dispatch_artifact_once(store, "worker-a", delivered.append) == 1

    assert delivered == []
    with new_session() as session:
        row = session.get(DiagnosisArtifactOutboxModel, outbox_id)
        assert row.status == "SUPPRESSED"
        assert row.last_error == "SUPPRESS_ARTIFACT_EVIDENCE_INELIGIBLE"
        assert row.published_at is None
        assert row.worker_lease_owner is None
        assert row.worker_lease_expires_at is None


def test_artifact_pre_delivery_gate_requires_owner_and_live_lease():
    outbox_id, _, _, _ = _insert_publishable_artifact(
        "diag-artifact-delivery-lease"
    )
    store = DiagnosisStore()
    start = utcnow()
    store.claim_artifact_outbox(
        "worker-a", lease_seconds=10, now=start
    )

    with pytest.raises(ValueError, match="lease owner mismatch"):
        store.validate_artifact_delivery(outbox_id, "worker-b", now=start)
    with pytest.raises(ValueError, match="lease expired"):
        store.validate_artifact_delivery(
            outbox_id, "worker-a", now=start + timedelta(seconds=11)
        )
    with pytest.raises(ValueError, match="lease owner mismatch"):
        store.finalize_artifact_gate(outbox_id, "worker-b", now=start)
    with pytest.raises(ValueError, match="lease expired"):
        store.finalize_artifact_gate(
            outbox_id, "worker-a", now=start + timedelta(seconds=11)
        )
    with pytest.raises(ValueError, match="publication gate is open"):
        store.finalize_artifact_gate(outbox_id, "worker-a", now=start)


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


def test_concurrent_file_sqlite_revocation_claim_has_single_winner(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'revocation-claim.db'}")
    reset_engine()
    init_db()
    outbox_id, _, _, _, _ = _insert_revocation("diag-concurrent-revocation-claim")
    barrier = Barrier(2)

    def claim(worker_id: str):
        barrier.wait()
        return DiagnosisStore().claim_artifact_revocation_outbox(
            worker_id, now=utcnow()
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(claim, worker_id)
            for worker_id in ("worker-a", "worker-b")
        ]
        results = [future.result() for future in futures]

    claimed = [item for result in results for item in result]
    assert [item["outbox_id"] for item in claimed] == [outbox_id]
    with new_session() as session:
        row = session.get(DiagnosisArtifactRevocationOutboxModel, outbox_id)
        assert row.status == "DISPATCHING"
        assert row.worker_lease_owner == claimed[0]["worker_lease_owner"]


def test_dispatcher_cli_selects_revocation_worker(monkeypatch):
    calls = []

    monkeypatch.setattr(
        outbox_dispatcher,
        "run_artifact_revocation_worker",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "server.app.database.init_db",
        lambda: calls.append(("init",)),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "outbox_dispatcher",
            "--artifact-revocation-outbox",
            "--worker-id",
            "rev-worker",
            "--poll-seconds",
            "0.25",
            "--once",
        ],
    )

    outbox_dispatcher.main()

    assert calls[0] == ("init",)
    assert calls[1][0][1] == "rev-worker"
    assert calls[1][1] == {"poll_seconds": 0.25, "once": True}


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
    store.mark_artifact_outbox_delivering(outbox_id, "worker-a", now=start)
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
    store.mark_artifact_outbox_delivering(
        outbox_id, "worker-b", now=start + timedelta(seconds=11)
    )
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
    store.mark_artifact_outbox_delivering(outbox_id, "worker-a", now=start)

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


def test_freeze_rejects_changed_persisted_conclusion_after_immutable_winner(
    monkeypatch,
):
    diagnosis_id = "diag-conflict"
    _insert_freeze_session(diagnosis_id)
    first = DiagnosisStore().freeze_diagnosis_artifact(diagnosis_id)

    with new_session() as session:
        diagnosis = session.get(DiagnosisSessionModel, diagnosis_id)
        diagnosis.conclusion_versions_json = [{
            "classification": "leak",
            "integrity_hash": "sha256:" + "a" * 64,
            "verification": {"status": "passed"},
        }]
        session.commit()

    with pytest.raises(ValueError, match="冻结产物完整性冲突"):
        DiagnosisStore().freeze_diagnosis_artifact(diagnosis_id)

    with new_session() as session:
        artifact = session.query(FrozenDiagnosisArtifactModel).one()
        outbox = session.query(DiagnosisArtifactOutboxModel).one()
        assert artifact.artifact_hash == first["artifact_hash"]
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
