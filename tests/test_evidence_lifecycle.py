from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.diagnosis.store import DiagnosisStore
from server.app.models import (
    DiagnosisConclusionInvalidationModel,
    DiagnosisRevalidationRequestModel,
    DiagnosisSessionModel,
)


@pytest.fixture(autouse=True)
def _database(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'evidence.db'}")
    reset_engine()
    init_db()
    yield
    reset_engine()


def _create_diagnosis(store: DiagnosisStore, diagnosis_id: str) -> None:
    store.create_session({
        "diagnosis_id": diagnosis_id,
        "creator_id": "review-test",
        "raw_query": "verify Evidence lifecycle",
        "status": "COLLECTING",
        "policy_profile": "test",
        "model_version": "none",
        "planner_version": "test",
        "deadline_at": datetime.now(timezone.utc) + timedelta(hours=1),
    })


def _add_evidence(
    store: DiagnosisStore,
    diagnosis_id: str,
    evidence_id: str,
) -> dict:
    return store.add_evidence({
        "evidence_id": evidence_id,
        "diagnosis_id": diagnosis_id,
        "source_type": "metric",
        "source_system": "test",
        "query_or_probe": "bounded test probe",
        "integrity_hash": f"sha256:{evidence_id:0<64}"[:71],
        "observed_value": {"value": evidence_id},
    })


def test_new_evidence_is_unreviewed_and_not_ai_eligible() -> None:
    store = DiagnosisStore()
    _create_diagnosis(store, "diag-a")

    evidence = _add_evidence(store, "diag-a", "ev-new")

    assert evidence["lifecycle_status"] == "ACTIVE"
    assert evidence["trust_status"] == "UNREVIEWED"
    assert evidence["review_revision"] == 0
    assert store.list_evidence("diag-a", eligible_only=True) == []


def test_only_active_trusted_unsuperseded_evidence_is_eligible() -> None:
    store = DiagnosisStore()
    _create_diagnosis(store, "diag-a")
    for evidence_id in (
        "ev-trusted",
        "ev-unreviewed",
        "ev-low-trust",
        "ev-excluded",
        "ev-invalid",
        "ev-old",
        "ev-replacement",
    ):
        _add_evidence(store, "diag-a", evidence_id)

    store.review_evidence(
        diagnosis_id="diag-a",
        evidence_id="ev-trusted",
        lifecycle_status="active",
        trust_status="trusted",
        reviewer_id="reviewer-a",
    )
    store.review_evidence(
        diagnosis_id="diag-a",
        evidence_id="ev-low-trust",
        lifecycle_status="ACTIVE",
        trust_status="LOW_TRUST",
        reviewer_id="reviewer-a",
    )
    store.review_evidence(
        diagnosis_id="diag-a",
        evidence_id="ev-excluded",
        lifecycle_status="EXCLUDED",
        trust_status="TRUSTED",
        reviewer_id="reviewer-a",
    )
    store.review_evidence(
        diagnosis_id="diag-a",
        evidence_id="ev-invalid",
        lifecycle_status="INVALID",
        trust_status="TRUSTED",
        reviewer_id="reviewer-a",
    )
    store.review_evidence(
        diagnosis_id="diag-a",
        evidence_id="ev-replacement",
        lifecycle_status="ACTIVE",
        trust_status="TRUSTED",
        reviewer_id="reviewer-a",
    )
    store.review_evidence(
        diagnosis_id="diag-a",
        evidence_id="ev-old",
        lifecycle_status="SUPERSEDED",
        trust_status="TRUSTED",
        superseded_by="ev-replacement",
        reviewer_id="reviewer-a",
    )

    eligible_ids = {
        item["evidence_id"]
        for item in store.list_evidence("diag-a", eligible_only=True)
    }
    assert eligible_ids == {"ev-trusted", "ev-replacement"}


def test_review_history_is_append_only_and_revision_checked() -> None:
    store = DiagnosisStore()
    _create_diagnosis(store, "diag-a")
    _add_evidence(store, "diag-a", "ev-reviewed")

    first = store.review_evidence(
        diagnosis_id="diag-a",
        evidence_id="ev-reviewed",
        lifecycle_status="ACTIVE",
        trust_status="TRUSTED",
        reviewer_id=" reviewer-a ",
        reason=" first review ",
        expected_revision=0,
    )
    second = store.review_evidence(
        diagnosis_id="diag-a",
        evidence_id="ev-reviewed",
        lifecycle_status="EXCLUDED",
        trust_status="LOW_TRUST",
        reviewer_id="reviewer-b",
        reason="conflicting source",
        expected_revision=1,
    )

    assert first["review_revision"] == 1
    assert second["review_revision"] == 2
    history = store.list_evidence_reviews("diag-a", "ev-reviewed")
    assert [item["revision"] for item in history] == [1, 2]
    assert [item["reviewer_id"] for item in history] == ["reviewer-a", "reviewer-b"]
    assert [item["reason"] for item in history] == ["first review", "conflicting source"]

    with pytest.raises(ValueError, match="revision conflict"):
        store.review_evidence(
            diagnosis_id="diag-a",
            evidence_id="ev-reviewed",
            lifecycle_status="ACTIVE",
            trust_status="TRUSTED",
            reviewer_id="reviewer-c",
            expected_revision=1,
        )
    assert len(store.list_evidence_reviews("diag-a", "ev-reviewed")) == 2
    assert store.get_evidence("diag-a", "ev-reviewed")["review_revision"] == 2


def test_invalid_reviews_leave_current_state_and_history_unchanged() -> None:
    store = DiagnosisStore()
    _create_diagnosis(store, "diag-a")
    _create_diagnosis(store, "diag-b")
    _add_evidence(store, "diag-a", "ev-a")
    _add_evidence(store, "diag-b", "ev-b")

    invalid_calls = (
        {"lifecycle_status": "UNKNOWN", "trust_status": "TRUSTED", "reviewer_id": "r"},
        {"lifecycle_status": "ACTIVE", "trust_status": "UNKNOWN", "reviewer_id": "r"},
        {"lifecycle_status": "ACTIVE", "trust_status": "TRUSTED", "reviewer_id": "   "},
        {
            "lifecycle_status": "SUPERSEDED",
            "trust_status": "TRUSTED",
            "reviewer_id": "r",
            "superseded_by": "ev-a",
        },
        {
            "lifecycle_status": "SUPERSEDED",
            "trust_status": "TRUSTED",
            "reviewer_id": "r",
            "superseded_by": "ev-b",
        },
    )
    for values in invalid_calls:
        with pytest.raises(ValueError):
            store.review_evidence(
                diagnosis_id="diag-a",
                evidence_id="ev-a",
                **values,
            )

    evidence = store.get_evidence("diag-a", "ev-a")
    assert evidence["lifecycle_status"] == "ACTIVE"
    assert evidence["trust_status"] == "UNREVIEWED"
    assert evidence["review_revision"] == 0
    assert store.list_evidence_reviews("diag-a", "ev-a") == []


def test_evidence_identity_cannot_replay_across_diagnoses() -> None:
    store = DiagnosisStore()
    _create_diagnosis(store, "diag-a")
    _create_diagnosis(store, "diag-b")
    _add_evidence(store, "diag-a", "ev-shared")

    with pytest.raises(ValueError, match="another diagnosis"):
        _add_evidence(store, "diag-b", "ev-shared")
    assert store.get_evidence("diag-b", "ev-shared") is None


def test_ineligible_review_invalidates_only_referencing_conclusion_versions() -> None:
    store = DiagnosisStore()
    _create_diagnosis(store, "diag-invalidation")
    _add_evidence(store, "diag-invalidation", "ev-invalidated")
    _add_evidence(store, "diag-invalidation", "ev-unaffected")
    first = {
        "integrity_hash": "sha256:" + "1" * 64,
        "root_cause": {"evidence_refs": ["ev-invalidated"]},
        "verification": {"status": "passed"},
    }
    second = {
        "integrity_hash": "sha256:" + "2" * 64,
        "root_cause": {"evidence_refs": ["ev-unaffected"]},
        "verification": {"status": "passed"},
    }
    with new_session() as session:
        diagnosis = session.get(DiagnosisSessionModel, "diag-invalidation")
        diagnosis.conclusion_versions_json = [first, second]
        session.commit()

    store.review_evidence(
        diagnosis_id="diag-invalidation",
        evidence_id="ev-invalidated",
        lifecycle_status="INVALID",
        trust_status="LOW_TRUST",
        reviewer_id="reviewer-a",
        reason="source was corrupt",
    )

    with new_session() as session:
        invalidations = session.query(DiagnosisConclusionInvalidationModel).all()
        requests = session.query(DiagnosisRevalidationRequestModel).all()
        assert [(row.conclusion_hash, row.review_revision) for row in invalidations] == [
            (first["integrity_hash"], 1),
        ]
        assert [(row.conclusion_hash, row.review_revision) for row in requests] == [
            (first["integrity_hash"], 1),
        ]
        diagnosis = session.get(DiagnosisSessionModel, "diag-invalidation")
        assert diagnosis.conclusion_versions_json == [first, second]


def test_each_ineligible_review_revision_appends_one_idempotent_invalidation() -> None:
    store = DiagnosisStore()
    _create_diagnosis(store, "diag-revisions")
    _add_evidence(store, "diag-revisions", "ev-reviewed")
    conclusion = {
        "integrity_hash": "sha256:" + "3" * 64,
        "nested": [{"evidence_refs": ["ev-reviewed"]}],
        "verification": {"status": "passed"},
    }
    with new_session() as session:
        diagnosis = session.get(DiagnosisSessionModel, "diag-revisions")
        diagnosis.conclusion_versions_json = [conclusion]
        session.commit()

    for expected_revision in (0, 1):
        store.review_evidence(
            diagnosis_id="diag-revisions",
            evidence_id="ev-reviewed",
            lifecycle_status="EXCLUDED",
            trust_status="LOW_TRUST",
            reviewer_id="reviewer-a",
            expected_revision=expected_revision,
        )

    with new_session() as session:
        invalidations = session.query(DiagnosisConclusionInvalidationModel).all()
        requests = session.query(DiagnosisRevalidationRequestModel).all()
        assert {row.review_revision for row in invalidations} == {1, 2}
        assert {row.review_revision for row in requests} == {1, 2}
        assert len(invalidations) == 2
        assert len(requests) == 2
