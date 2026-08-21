from __future__ import annotations

from datetime import date, datetime, timezone
import json

import pytest
from pydantic import ValidationError

from server.app.evaluation.artifacts import artifact_hash, canonical_artifact_json, canonicalize
from server.app.evaluation.oracle_repository import EvaluationOracleRepository, OracleUnavailableError
from server.app.evaluation.schemas import EvaluationOracle, FrozenDiagnosisArtifact


def _artifact(**overrides):
    value = {
        "schema_version": "diagnosis-artifact-v1",
        "diagnosis_id": "diag-1",
        "case_id": "case-1",
        "terminal_status": "COMPLETED",
        "conclusion": {
            "instance_id": "svc-a",
            "location_type": "self",
            "domain_type": "cpu",
            "classification": "hotspot",
            "verification": {"status": "passed"},
        },
        "model_version": "model-1",
        "planner_version": "planner-1",
    }
    value.update(overrides)
    return value


def test_canonical_json_is_stable_for_nested_mapping_order():
    left = _artifact(target_scope={"z": {"b": 2, "a": 1}, "a": 1})
    right = _artifact(target_scope={"a": 1, "z": {"a": 1, "b": 2}})
    assert canonical_artifact_json(left) == canonical_artifact_json(right)
    assert artifact_hash(canonical_artifact_json(left)).startswith("sha256:")


def test_canonicalize_normalizes_dates_utc_and_tuples():
    assert canonicalize(datetime(2026, 8, 21, 12, 0)) == "2026-08-21T12:00:00Z"
    assert canonicalize(datetime(2026, 8, 21, 20, 0, tzinfo=timezone.utc)) == "2026-08-21T20:00:00Z"
    assert canonicalize(date(2026, 8, 21)) == "2026-08-21"
    assert canonicalize(("a", "b")) == ["a", "b"]


def test_canonicalize_preserves_ordered_list_semantics():
    value = [{"id": "b"}, {"id": "a"}]
    assert canonicalize(value) == value
    assert canonical_artifact_json(_artifact(evidence=value)) != canonical_artifact_json(
        _artifact(evidence=list(reversed(value)))
    )


def test_canonicalize_retains_duplicate_entries():
    value = [{"id": "a"}, {"id": "a"}]
    assert canonicalize(value) == value


def test_nan_is_rejected():
    with pytest.raises((ValueError, TypeError, ValidationError)):
        canonical_artifact_json(_artifact(budget={"value": float("nan")}))


def test_artifact_schema_forbids_evaluator_fields_and_invalid_status():
    with pytest.raises(ValidationError):
        FrozenDiagnosisArtifact.model_validate(_artifact(oracle_root_cause_id="secret"))
    with pytest.raises(ValidationError):
        FrozenDiagnosisArtifact.model_validate(_artifact(terminal_status="RUNNING"))


def test_oracle_requires_at_least_one_expected_value():
    with pytest.raises(ValidationError):
        EvaluationOracle.model_validate({"case_id": "case-1"})


def test_oracle_repository_fails_closed_for_missing_malformed_and_unknown(tmp_path):
    repo = EvaluationOracleRepository(tmp_path / "missing.json")
    with pytest.raises(OracleUnavailableError):
        repo.load("case-1")
    path = tmp_path / "oracle.json"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(OracleUnavailableError):
        EvaluationOracleRepository(path).load("case-1")
    path.write_text(json.dumps({"other": {"expected_domain_type": "cpu"}}), encoding="utf-8")
    with pytest.raises(OracleUnavailableError):
        EvaluationOracleRepository(path).load("case-1")


def test_oracle_repository_validates_strict_payload(tmp_path):
    path = tmp_path / "oracle.json"
    path.write_text(json.dumps({"case-1": {"expected_domain_type": "cpu", "extra": "nope"}}), encoding="utf-8")
    with pytest.raises(OracleUnavailableError):
        EvaluationOracleRepository(path).load("case-1")
    path.write_text(json.dumps({"case-1": {"expected_domain_type": "cpu"}}), encoding="utf-8")
    assert EvaluationOracleRepository(path).load("case-1").expected_domain_type == "cpu"
