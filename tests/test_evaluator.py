from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.diagnosis.store import DiagnosisStore, utcnow
from server.app.evaluation.artifacts import artifact_hash, canonical_artifact_json
from server.app.evaluation.evaluator import ArtifactEvaluationError, DiagnosisArtifactEvaluator
from server.app.evaluation.oracle_repository import EvaluationOracleRepository
from server.app.evaluation.schemas import EvaluationRequest, FrozenDiagnosisArtifact
from server.app.models import (
    DiagnosisEvaluationModel,
    DiagnosisSessionModel,
    FrozenDiagnosisArtifactModel,
)


@pytest.fixture(autouse=True)
def _db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    reset_engine()


def _stored_artifact(payload=None):
    payload = payload or {
        "schema_version": "diagnosis-artifact-v1",
        "diagnosis_id": "diag-1",
        "case_id": "case-1",
        "terminal_status": "COMPLETED",
        "conclusion": {
            "cluster_assessment": {
                "root_location": {
                    "type": "self",
                    "target_ref": "svc-a",
                },
                "domain_cause": {"type": "cpu"},
                "classification": "hotspot",
            },
            "verification": {"status": "passed"},
        },
        "model_version": "model-1",
        "planner_version": "planner-1",
    }
    validated = FrozenDiagnosisArtifact.model_validate(payload)
    canonical = canonical_artifact_json(validated)
    return {"payload": payload, "canonical_json": canonical, "artifact_hash": artifact_hash(canonical)}


def _persist_artifact(artifact):
    now = utcnow()
    payload = artifact["payload"]
    with new_session() as session:
        if session.get(DiagnosisSessionModel, payload["diagnosis_id"]) is None:
            session.add(DiagnosisSessionModel(
                id=payload["diagnosis_id"],
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
            id="artifact:diag-1",
            diagnosis_id=payload["diagnosis_id"],
            schema_version=payload["schema_version"],
            terminal_status=payload["terminal_status"],
            canonical_json=artifact["canonical_json"],
            artifact_hash=artifact["artifact_hash"],
            created_at=now,
        ))
        session.commit()


class StubStore:
    def __init__(self, artifact):
        self.artifact = artifact

    def get_frozen_diagnosis_artifact(self, artifact_id):
        return self.artifact if artifact_id == "artifact:diag-1" else None


def _evaluator(tmp_path, artifact=None, oracle_payload=None):
    path = tmp_path / "oracle.json"
    if oracle_payload is not False:
        path.write_text(
            json.dumps(
                oracle_payload
                or {
                    "case-1": {
                        "expected_domain_type": "cpu",
                        "expected_location_type": "self",
                    }
                }
            ),
            encoding="utf-8",
        )
    return DiagnosisArtifactEvaluator(
        StubStore(artifact or _stored_artifact()),
        EvaluationOracleRepository(path),
    )


def _request(artifact, version="v1"):
    return EvaluationRequest(
        artifact_id="artifact:diag-1",
        expected_artifact_hash=artifact["artifact_hash"],
        evaluator_version=version,
    )


def test_evaluator_persists_per_field_match_and_is_idempotent(tmp_path):
    artifact = _stored_artifact()
    evaluator = _evaluator(tmp_path, artifact)
    first = evaluator.evaluate(_request(artifact))
    second = evaluator.evaluate(_request(artifact))
    assert first["status"] == "COMPLETED"
    assert first["result"] == {"passed": True, "matches": {"instance_id": True, "location_type": True, "domain_type": True, "classification": True}}
    assert second["evaluation_id"] == first["evaluation_id"]
    with new_session() as session:
        assert session.query(DiagnosisEvaluationModel).count() == 1


def test_concurrent_duplicate_success_recovers_single_winner(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'success.db'}")
    reset_engine()
    init_db()
    artifact = _stored_artifact()
    _persist_artifact(artifact)
    evaluator = _evaluator(tmp_path, artifact)
    request = _request(artifact)
    barrier = Barrier(2)

    def evaluate_once():
        barrier.wait()
        return evaluator.evaluate(request)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: evaluate_once(), range(2)))

    assert results[0]["evaluation_id"] == results[1]["evaluation_id"]
    assert all(item["status"] == "COMPLETED" for item in results)
    with new_session() as session:
        assert session.query(DiagnosisEvaluationModel).count() == 1


def test_concurrent_duplicate_failure_recovers_single_winner(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'failure.db'}")
    reset_engine()
    init_db()
    artifact = _stored_artifact()
    _persist_artifact(artifact)
    evaluator = _evaluator(tmp_path, artifact)
    request = EvaluationRequest(
        artifact_id="artifact:diag-1",
        expected_artifact_hash="sha256:" + "0" * 64,
        evaluator_version="v1",
    )
    barrier = Barrier(2)

    def evaluate_once():
        barrier.wait()
        with pytest.raises(ArtifactEvaluationError) as captured:
            evaluator.evaluate(request)
        return captured.value.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        codes = list(executor.map(lambda _: evaluate_once(), range(2)))

    assert codes == ["ARTIFACT_HASH_MISMATCH", "ARTIFACT_HASH_MISMATCH"]
    with new_session() as session:
        row = session.query(DiagnosisEvaluationModel).one()
        assert row.status == "FAILED"
        assert row.failure_code == "ARTIFACT_HASH_MISMATCH"


def test_evaluator_reports_mismatch_without_mutating_artifact(tmp_path):
    payload = _stored_artifact()["payload"]
    payload["conclusion"]["cluster_assessment"]["domain_cause"]["type"] = "memory"
    artifact = _stored_artifact(payload)
    result = _evaluator(tmp_path, artifact).evaluate(_request(artifact))
    assert result["result"]["passed"] is False
    assert result["result"]["matches"]["domain_type"] is False


def test_evaluator_fail_closed_matrix(tmp_path):
    evaluator = _evaluator(tmp_path)
    with pytest.raises(ArtifactEvaluationError, match="ARTIFACT_NOT_FOUND"):
        evaluator.evaluate(EvaluationRequest(artifact_id="missing", expected_artifact_hash="sha256:" + "0" * 64, evaluator_version="v1"))
    artifact = _stored_artifact()
    bad_request = EvaluationRequest(artifact_id="artifact:diag-1", expected_artifact_hash="sha256:" + "0" * 64, evaluator_version="v1")
    with pytest.raises(ArtifactEvaluationError, match="ARTIFACT_HASH_MISMATCH"):
        evaluator.evaluate(bad_request)
    malformed = dict(artifact)
    malformed["payload"] = {"schema_version": "bad"}
    with pytest.raises(ArtifactEvaluationError, match="ARTIFACT_MALFORMED"):
        _evaluator(tmp_path, malformed).evaluate(_request(artifact))


def test_artifact_malformed_is_persisted_for_real_parent(tmp_path):
    artifact = _stored_artifact()
    _persist_artifact(artifact)
    malformed = dict(artifact)
    malformed["payload"] = {"schema_version": "bad"}

    with pytest.raises(ArtifactEvaluationError, match="ARTIFACT_MALFORMED"):
        _evaluator(tmp_path, malformed).evaluate(_request(artifact))

    with new_session() as session:
        row = session.query(DiagnosisEvaluationModel).one()
        assert row.status == "FAILED"
        assert row.failure_code == "ARTIFACT_MALFORMED"
        assert row.result_json == {}


def test_existing_artifact_failure_is_persisted_without_oracle_values(tmp_path):
    artifact = _stored_artifact()
    _persist_artifact(artifact)
    request = EvaluationRequest(
        artifact_id="artifact:diag-1",
        expected_artifact_hash="sha256:" + "0" * 64,
        evaluator_version="v1",
    )
    evaluator = _evaluator(tmp_path, artifact)

    for _ in range(2):
        with pytest.raises(ArtifactEvaluationError, match="ARTIFACT_HASH_MISMATCH"):
            evaluator.evaluate(request)

    with new_session() as session:
        rows = session.query(DiagnosisEvaluationModel).all()
        assert len(rows) == 1
        assert rows[0].status == "FAILED"
        assert rows[0].failure_code == "ARTIFACT_HASH_MISMATCH"
        assert rows[0].result_json == {}
        serialized = json.dumps(rows[0].to_dict(), default=str).lower()
        assert "expected_domain_type" not in serialized
        assert "cpu" not in serialized


def test_missing_artifact_failure_is_not_written_to_fk_bound_table(tmp_path):
    request = EvaluationRequest(
        artifact_id="missing",
        expected_artifact_hash="sha256:" + "0" * 64,
        evaluator_version="v1",
    )
    with pytest.raises(ArtifactEvaluationError, match="ARTIFACT_NOT_FOUND"):
        _evaluator(tmp_path).evaluate(request)
    with new_session() as session:
        assert session.query(DiagnosisEvaluationModel).count() == 0


def test_oracle_unavailable_recovers_same_identity(tmp_path):
    artifact = _stored_artifact()
    _persist_artifact(artifact)
    request = _request(artifact)
    evaluator = _evaluator(tmp_path, artifact, oracle_payload=False)

    with pytest.raises(ArtifactEvaluationError, match="ORACLE_UNAVAILABLE"):
        evaluator.evaluate(request)

    oracle_path = tmp_path / "oracle.json"
    oracle_path.write_text(
        json.dumps({"case-1": {"expected_domain_type": "cpu"}}),
        encoding="utf-8",
    )
    result = evaluator.evaluate(request)
    assert result["status"] == "COMPLETED"
    assert result["result"]["passed"] is True

    with new_session() as session:
        row = session.query(DiagnosisEvaluationModel).one()
        assert row.status == "COMPLETED"
        assert row.failure_code is None


def test_missing_case_id_fails_closed_without_diagnosis_fallback(tmp_path):
    payload = _stored_artifact()["payload"]
    payload["case_id"] = None
    artifact = _stored_artifact(payload)
    _persist_artifact(artifact)

    with pytest.raises(ArtifactEvaluationError, match="CASE_ID_MISSING"):
        _evaluator(
            tmp_path,
            artifact,
            oracle_payload={"diag-1": {"expected_domain_type": "cpu"}},
        ).evaluate(_request(artifact))

    with new_session() as session:
        row = session.query(DiagnosisEvaluationModel).one()
        assert row.failure_code == "CASE_ID_MISSING"


def test_evaluation_identity_has_fixed_length_for_maximum_inputs():
    request = EvaluationRequest(
        artifact_id="a" * 128,
        expected_artifact_hash="sha256:" + "f" * 64,
        evaluator_version="v" * 64,
    )
    identity = DiagnosisArtifactEvaluator._identity(request)
    assert identity.startswith("evaluation:")
    assert len(identity) == len("evaluation:") + 64


def test_artifact_hash_invalid_is_persisted(tmp_path):
    artifact = _stored_artifact()
    artifact["artifact_hash"] = "sha256:" + "1" * 64
    _persist_artifact(artifact)
    request = EvaluationRequest(
        artifact_id="artifact:diag-1",
        expected_artifact_hash=artifact["artifact_hash"],
        evaluator_version="v1",
    )

    with pytest.raises(ArtifactEvaluationError, match="ARTIFACT_HASH_INVALID"):
        _evaluator(tmp_path, artifact).evaluate(request)

    with new_session() as session:
        row = session.query(DiagnosisEvaluationModel).one()
        assert row.failure_code == "ARTIFACT_HASH_INVALID"


def test_store_integrity_failure_projects_hash_invalid(tmp_path):
    artifact = _stored_artifact()
    _persist_artifact(artifact)

    class CorruptStore:
        def get_frozen_diagnosis_artifact(self, artifact_id):
            raise ValueError("sensitive storage integrity detail")

    evaluator = DiagnosisArtifactEvaluator(
        CorruptStore(),
        _evaluator(tmp_path, artifact).oracle_repository,
    )

    with pytest.raises(ArtifactEvaluationError) as captured:
        evaluator.evaluate(_request(artifact))

    assert captured.value.code == "ARTIFACT_HASH_INVALID"
    assert "sensitive storage integrity detail" not in str(captured.value)
    with new_session() as session:
        row = session.query(DiagnosisEvaluationModel).one()
        assert row.failure_code == "ARTIFACT_HASH_INVALID"
        assert row.result_json == {}


def test_failed_evaluation_version_isolation(tmp_path):
    artifact = _stored_artifact()
    _persist_artifact(artifact)
    evaluator = _evaluator(tmp_path, artifact)
    bad_hash = "sha256:" + "0" * 64
    for version in ("v1", "v2"):
        with pytest.raises(ArtifactEvaluationError, match="ARTIFACT_HASH_MISMATCH"):
            evaluator.evaluate(EvaluationRequest(
                artifact_id="artifact:diag-1",
                expected_artifact_hash=bad_hash,
                evaluator_version=version,
            ))
    with new_session() as session:
        rows = session.query(DiagnosisEvaluationModel).all()
        assert {row.evaluator_version for row in rows} == {"v1", "v2"}


def test_evaluator_version_isolation(tmp_path):
    artifact = _stored_artifact()
    evaluator = _evaluator(tmp_path, artifact)
    first = evaluator.evaluate(_request(artifact, "v1"))
    second = evaluator.evaluate(_request(artifact, "v2"))
    assert first["evaluation_id"] != second["evaluation_id"]
    with new_session() as session:
        assert session.query(DiagnosisEvaluationModel).count() == 2
