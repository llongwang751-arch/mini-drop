from __future__ import annotations

import json

import pytest

from server.app.evaluation.real_world_admission import canonical_hash
from server.app.evaluation.real_world_oracle import (
    RealWorldOracleRepositoryV1,
    RealWorldOracleScorerV1,
    RealWorldOracleUnavailableError,
)


_PRIVATE_ROOT = "synthetic-private-root"
_PRIVATE_SUMMARY = "synthetic expected summary"
_PRIVATE_COUNTERFACTUAL = "synthetic counterfactual"
_PRIVATE_LOCATION = "src/private_target.py"


def _write_oracle(path, **case_overrides):
    case = {
        "case_id": "synthetic-case",
        "root_cause_id": _PRIVATE_ROOT,
        "expected_summary": _PRIVATE_SUMMARY,
        "counterfactual": _PRIVATE_COUNTERFACTUAL,
        "required_locations": [_PRIVATE_LOCATION],
        "expected_terminal": "ROOT_CAUSE",
    }
    case.update(case_overrides)
    path.write_text(json.dumps({
        "schema_version": "real-world-oracle-v1",
        "cases": [case],
    }), encoding="utf-8")


def _evidence(evidence_id: str, role: str, recorded_at: str) -> dict:
    value = {
        "evidence_id": evidence_id,
        "run_id": "run-synthetic",
        "case_id": "synthetic-case",
        "role": role,
        "recorded_at": recorded_at,
        "observed": {"value": 1},
    }
    value["integrity_hash"] = canonical_hash(value)
    return value


def _run() -> dict:
    evidence = [
        _evidence("ev-baseline", "baseline", "2026-08-21T01:00:00Z"),
        _evidence("ev-incident", "incident", "2026-08-21T02:00:00Z"),
        _evidence("ev-verification", "verification", "2026-08-21T03:00:00Z"),
    ]
    hashes = sorted(item["integrity_hash"] for item in evidence)
    run = {
        "run_id": "run-synthetic",
        "case_id": "synthetic-case",
        "status": "COMPLETED",
        "execution_fidelity": "FULL_UPSTREAM_REPLAY",
        "predicted_root_cause_id": _PRIVATE_ROOT,
        "predicted_locations": [_PRIVATE_LOCATION],
        "abstained": False,
        "evidence": evidence,
        "evidence_refs": ["ev-incident"],
        "counter_evidence_refs": ["ev-baseline", "ev-verification"],
    }
    admission = {
        "schema_version": "real-world-admission-v1",
        "case_id": "synthetic-case",
        "run_id": "run-synthetic",
        "execution_fidelity": "FULL_UPSTREAM_REPLAY",
        "terminal_status": "COMPLETED",
        "comparator_passed": True,
        "repository_url": "https://example.invalid/repository",
        "base_sha": "1" * 40,
        "fix_sha": "2" * 40,
        "source_integrity_hash": "sha256:" + "3" * 64,
        "dependency_lock_hash": "sha256:" + "4" * 64,
        "harness_integrity_hash": "sha256:" + "5" * 64,
        "environment": {
            "os": "linux",
            "architecture": "amd64",
            "runtime": "python3",
            "container_image_digest": "sha256:" + "6" * 64,
        },
        "repetitions": [
            {
                "repetition_id": f"repetition-{index}",
                "terminal_status": "COMPLETED",
                "baseline_stable": True,
                "incident_reproduced": True,
                "verification_recovered": True,
                "comparator_passed": True,
                "evidence_roles": ["baseline", "incident", "verification"],
                "evidence_hashes": hashes,
            }
            for index in range(3)
        ],
    }
    admission["admission_hash"] = canonical_hash(admission)
    run["admission"] = admission
    return run


def _scorer(tmp_path):
    path = tmp_path / "synthetic-oracle.json"
    _write_oracle(path)
    return RealWorldOracleScorerV1(
        RealWorldOracleRepositoryV1(path), b"k" * 32,
    )


def test_versioned_adapter_scores_full_replay_with_safe_projection(tmp_path):
    score = _scorer(tmp_path).score(_run(), minimum_repetitions=3)

    assert score.verdict == "PASS"
    assert all(score.gates.values())
    rendered = score.model_dump_json()
    for private_value in (
        "synthetic-case", _PRIVATE_ROOT, _PRIVATE_SUMMARY,
        _PRIVATE_COUNTERFACTUAL, _PRIVATE_LOCATION,
    ):
        assert private_value not in rendered
    assert "expected_" not in rendered
    assert "predicted_" not in rendered


def test_mechanism_reproduction_is_harness_invalid_not_scored(tmp_path):
    run = _run()
    run["execution_fidelity"] = "MECHANISM_REPRO"

    score = _scorer(tmp_path).score(run, minimum_repetitions=3)

    assert score.verdict == "HARNESS_INVALID"
    assert score.gates["formal_admission"] is False


def test_explicit_unscored_claim_is_harness_invalid_not_formal_verdict(tmp_path):
    run = _run()
    run["scoring_status"] = "UNSCORED"
    run["admission"] = None

    score = _scorer(tmp_path).score(run, minimum_repetitions=3)

    assert score.verdict == "HARNESS_INVALID"
    assert score.verdict not in {"PASS", "FAIL"}
    assert score.gates["formal_admission"] is False


def test_valid_harness_with_wrong_diagnosis_is_fail(tmp_path):
    run = _run()
    run["predicted_root_cause_id"] = "wrong"

    score = _scorer(tmp_path).score(run, minimum_repetitions=3)

    assert score.verdict == "FAIL"
    assert score.gates["formal_admission"] is True
    assert score.gates["root_cause"] is False


def test_oracle_values_are_redacted_from_repr_and_errors(tmp_path):
    path = tmp_path / "synthetic-oracle.json"
    _write_oracle(path)
    oracle = RealWorldOracleRepositoryV1(path).load("synthetic-case")
    assert repr(oracle) == "RealWorldOracleV1(<redacted>)"
    assert _PRIVATE_ROOT not in repr(oracle)

    path.write_text(json.dumps({
        "schema_version": "real-world-oracle-v1",
        "cases": [{
            "case_id": "synthetic-case",
            "root_cause_id": _PRIVATE_ROOT,
            "expected_summary": _PRIVATE_SUMMARY,
            "counterfactual": _PRIVATE_COUNTERFACTUAL,
            "required_locations": [],
            "expected_terminal": "ROOT_CAUSE",
        }],
    }), encoding="utf-8")
    with pytest.raises(RealWorldOracleUnavailableError) as caught:
        RealWorldOracleRepositoryV1(path).load("synthetic-case")
    assert _PRIVATE_ROOT not in str(caught.value)


@pytest.mark.parametrize("mutation", [
    lambda payload: payload.update(schema_version="unknown"),
    lambda payload: payload.update(extra=True),
    lambda payload: payload["cases"].append(payload["cases"][0].copy()),
    lambda payload: payload["cases"][0].update(extra=True),
])
def test_adapter_fails_closed_on_schema_drift(tmp_path, mutation):
    path = tmp_path / "synthetic-oracle.json"
    _write_oracle(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutation(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RealWorldOracleUnavailableError):
        RealWorldOracleRepositoryV1(path).load("synthetic-case")
