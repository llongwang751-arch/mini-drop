from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

import pytest

from scripts.real_world_benchmark import public_plan, score_results, validate_suite
from server.app.evaluation.real_world_admission import (
    canonical_hash,
    validate_formal_admission,
)


_PRIVATE_ROOT = "synthetic-private-root"
_PRIVATE_SUMMARY = "synthetic expected summary"
_PRIVATE_COUNTERFACTUAL = "synthetic counterfactual"
_PRIVATE_LOCATION = "src/private_target.py"
_SYNTHETIC_CASE_ID = "synthetic-case"


def _write_oracle(path) -> None:
    path.write_text(json.dumps({
        "schema_version": "real-world-oracle-v1",
        "cases": [{
            "case_id": _SYNTHETIC_CASE_ID,
            "root_cause_id": _PRIVATE_ROOT,
            "expected_summary": _PRIVATE_SUMMARY,
            "counterfactual": _PRIVATE_COUNTERFACTUAL,
            "required_locations": [_PRIVATE_LOCATION],
            "expected_terminal": "ROOT_CAUSE",
        }],
    }), encoding="utf-8")


def _evidence(
    evidence_id: str,
    *,
    run_id: str = "run-1",
    case_id: str = "RW-GRAFANA-123359",
    role: str = "incident",
    recorded_at: str | None = None,
) -> dict:
    phase_times = {
        "baseline": "2026-08-20T11:00:00Z",
        "incident": "2026-08-20T12:00:00Z",
        "verification": "2026-08-20T13:00:00Z",
    }
    item = {
        "evidence_id": evidence_id,
        "run_id": run_id,
        "case_id": case_id,
        "role": role,
        "evidence_type": "metric_snapshot",
        "recorded_at": recorded_at or phase_times.get(role, "2026-08-20T12:00:00Z"),
        "producer": "benchmark-harness",
        "observed": {"queue_depth": 42},
    }
    encoded = json.dumps(
        item, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    item["integrity_hash"] = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
    return item


def _admission(run: dict, *, repetitions: int = 3) -> dict:
    evidence_hashes = sorted(item["integrity_hash"] for item in run["evidence"])
    admission = {
        "schema_version": "real-world-admission-v1",
        "case_id": run["case_id"],
        "run_id": run["run_id"],
        "execution_fidelity": "FULL_UPSTREAM_REPLAY",
        "terminal_status": "COMPLETED",
        "comparator_passed": True,
        "repository_url": "https://github.com/grafana/grafana",
        "base_sha": "1" * 40,
        "fix_sha": "2" * 40,
        "source_integrity_hash": "sha256:" + "3" * 64,
        "dependency_lock_hash": "sha256:" + "4" * 64,
        "harness_integrity_hash": "sha256:" + "5" * 64,
        "environment": {
            "os": "linux",
            "architecture": "amd64",
            "runtime": "go1.24",
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
                "evidence_hashes": evidence_hashes,
            }
            for index in range(1, repetitions + 1)
        ],
    }
    admission["admission_hash"] = canonical_hash(admission)
    return admission


def _rehash_admission(run: dict) -> None:
    meaningful = {
        key: value
        for key, value in run["admission"].items()
        if key != "admission_hash"
    }
    run["admission"]["admission_hash"] = canonical_hash(meaningful)


def _run(**overrides) -> dict:
    evidence = [
        _evidence("ev-baseline", role="baseline"),
        _evidence("ev-incident", role="incident"),
        _evidence("ev-verification", role="verification"),
    ]
    run = {
        "run_id": "run-1",
        "case_id": "RW-GRAFANA-123359",
        "predicted_root_cause_id": "workqueue_pointer_identity_breaks_deduplication",
        "predicted_locations": ["pkg/registry/apis/provisioning/controller/repository.go"],
        "evidence": evidence,
        "evidence_refs": ["ev-baseline", "ev-incident"],
        "counter_evidence_refs": ["ev-verification"],
        "snapshot_roles": ["baseline", "incident", "verification"],
        "abstained": False,
        "confidence": 0.85,
        "duration_seconds": 12.0,
        "tool_calls": 4,
        "status": "COMPLETED",
        "execution_fidelity": "FULL_UPSTREAM_REPLAY",
    }
    run.update(overrides)
    if "admission" not in overrides:
        minimum_repetitions = 5 if run["case_id"] == "RW-K8S-140886" else 3
        run["admission"] = _admission(run, repetitions=minimum_repetitions)
    return run


def _validate_admission(run: dict) -> dict:
    minimum_repetitions = 5 if run["case_id"] == "RW-K8S-140886" else 3
    return validate_formal_admission(
        run,
        case_id=run["case_id"],
        minimum_repetitions=minimum_repetitions,
        evidence_by_id={item["evidence_id"]: item for item in run["evidence"]},
    )


def _score(
    tmp_path,
    runs: list[dict],
    *,
    oracle_path=None,
) -> dict:
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"product": "example", "runs": runs}), encoding="utf-8")
    return score_results(
        path,
        oracle_path=oracle_path,
        commitment_key=b"k" * 32,
    )


def _assert_unscored(tmp_path, run: dict, error: str) -> None:
    report = _score(tmp_path, [run])
    assert report["submitted_cases"] == 1
    assert report["evaluated_cases"] == 0
    assert report["unscored_cases"] == 1
    assert report["results"][0]["scoring_status"] == "UNSCORED"
    assert error in report["results"][0]["admission_error"]


def test_evaluator_path_uses_versioned_oracle_and_allowlisted_projection(tmp_path, monkeypatch) -> None:
    oracle_path = tmp_path / "synthetic-oracle.json"
    _write_oracle(oracle_path)
    public_case = {
        "case_id": _SYNTHETIC_CASE_ID,
        "minimum_repetitions": 3,
    }
    input_path = tmp_path / "synthetic-results.json"
    monkeypatch.setattr(
        "scripts.real_world_benchmark._load",
        lambda path: (
            json.loads(path.read_text(encoding="utf-8"))
            if path == input_path
            else {
                "dataset": "synthetic",
                "version": "1",
                "public_cases": "cases.json",
                "private_oracles": "ignored.json",
            }
            if path.name == "manifest.json"
            else {"cases": [public_case]}
        ),
    )
    evidence = [
        _evidence("base", run_id="run-1", case_id=_SYNTHETIC_CASE_ID, role="baseline"),
        _evidence("incident", run_id="run-1", case_id=_SYNTHETIC_CASE_ID, role="incident"),
        _evidence("verify", run_id="run-1", case_id=_SYNTHETIC_CASE_ID, role="verification"),
    ]
    run = _run(
        case_id=_SYNTHETIC_CASE_ID,
        predicted_root_cause_id=_PRIVATE_ROOT,
        predicted_locations=[_PRIVATE_LOCATION],
        evidence=evidence,
        evidence_refs=["incident"],
        counter_evidence_refs=["base", "verify"],
    )
    run["admission"] = _admission(run)
    input_path.write_text(
        json.dumps({"product": "example", "runs": [run]}), encoding="utf-8",
    )
    report = score_results(
        input_path,
        oracle_path=oracle_path,
        commitment_key=b"k" * 32,
    )

    row = report["results"][0]
    assert row["scoring_status"] == "SCORED"
    assert row["evaluator_score"]["verdict"] == "PASS"
    assert set(row["evaluator_score"]) == {
        "schema_version", "scorer_id", "opaque_case_token", "run_id", "verdict",
        "gates", "oracle_commitment", "run_digest", "public_contract_digest",
    }
    rendered = json.dumps(report)
    for private_value in (
        _PRIVATE_ROOT, _PRIVATE_SUMMARY, _PRIVATE_COUNTERFACTUAL, _PRIVATE_LOCATION,
    ):
        assert private_value not in rendered


def test_real_world_public_suite_is_aligned_without_loading_oracle(monkeypatch) -> None:
    original_load = __import__(
        "scripts.real_world_benchmark", fromlist=["_load"],
    )._load

    def public_only_load(path):
        assert path.name != "oracles.json"
        return original_load(path)

    monkeypatch.setattr("scripts.real_world_benchmark._load", public_only_load)

    result = validate_suite()
    assert result["valid"] is True
    assert result["case_count"] == 7
    assert result["oracle_isolated"] is True


def test_public_plan_does_not_expose_pr_or_answer() -> None:
    rendered = json.dumps(public_plan(), ensure_ascii=False)
    assert "root_cause_id" not in rendered
    assert "github.com/" not in rendered
    assert "fix_sha" not in rendered


def test_valid_admitted_full_upstream_replay_drives_all_evidence_dimensions(tmp_path) -> None:
    report = _score(tmp_path, [_run()])
    assert report["submitted_cases"] == 1
    assert report["evaluated_cases"] == 1
    assert report["unscored_cases"] == 0
    assert report["results"][0]["scoring_status"] == "SCORED"
    assert report["results"][0]["admission_error"] is None
    assert report["top1_exact_rate"] == 1
    assert report["source_location_rate"] == 1
    assert report["evidence_citation_rate"] == 1
    assert report["counter_evidence_rate"] == 1
    assert report["three_phase_snapshot_rate"] == 1
    assert report["evidence_first_score"] == 100


def test_broad_source_location_fragment_earns_no_credit(tmp_path) -> None:
    assert _score(tmp_path, [_run(predicted_locations=["pkg"])])["source_location_rate"] == 0


def test_arbitrary_refs_and_self_reported_roles_earn_no_credit(tmp_path) -> None:
    report = _score(tmp_path, [_run(
        evidence=[], evidence_refs=["snapshot://incident/1"],
        counter_evidence_refs=["anything"],
        snapshot_roles=["baseline", "incident", "verification"],
    )])
    row = report["results"][0]
    assert row["has_evidence_refs"] is False
    assert row["has_counter_evidence_refs"] is False
    assert row["three_phase_snapshot_complete"] is False


def test_refs_only_resolve_valid_current_run_and_case_evidence(tmp_path) -> None:
    wrong_run = _evidence("wrong-run", run_id="another-run", role="baseline")
    wrong_case = _evidence("wrong-case", case_id="RW-OTELPY-4224", role="incident")
    bad_hash = _evidence("bad-hash", role="verification")
    bad_hash["observed"] = {"queue_depth": 999}
    valid = _evidence("valid", role="incident")
    report = _score(tmp_path, [_run(
        evidence=[wrong_run, wrong_case, bad_hash, valid],
        evidence_refs=["wrong-run", "wrong-case", "bad-hash", "valid"],
        counter_evidence_refs=["bad-hash"],
    )])
    row = report["results"][0]
    assert row["has_evidence_refs"] is True
    assert row["has_counter_evidence_refs"] is False
    assert row["three_phase_snapshot_complete"] is False


def test_malformed_evidence_objects_do_not_resolve(tmp_path) -> None:
    malformed = _evidence("malformed")
    del malformed["producer"]
    report = _score(tmp_path, [_run(
        evidence=["malformed", malformed], evidence_refs=["malformed"],
        counter_evidence_refs=[], admission=None,
    )])
    assert report["evidence_citation_rate"] == 0


def test_uncertain_revert_rewards_calibrated_abstention(tmp_path) -> None:
    case_id = "RW-K8S-140886"
    evidence = [
        _evidence("base", case_id=case_id, role="baseline"),
        _evidence("incident", case_id=case_id, role="incident"),
        _evidence("verify", case_id=case_id, role="verification"),
    ]
    report = _score(tmp_path, [_run(
        case_id=case_id, predicted_root_cause_id=None,
        predicted_locations=["controller/resourceclaim"], evidence=evidence,
        evidence_refs=["base", "incident"], counter_evidence_refs=["verify"],
        abstained=True, confidence=0.9,
    )])
    assert report["top1_exact_rate"] == 0
    assert report["abstention_calibration_rate"] == 1
    assert report["mean_confidence_absolute_error"] == 0.1


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("confidence", True, "confidence"), ("confidence", "0.5", "confidence"),
        ("confidence", -0.01, "confidence"), ("confidence", 1.01, "confidence"),
        ("confidence", math.nan, "confidence"), ("confidence", math.inf, "confidence"),
        ("duration_seconds", True, "duration_seconds"),
        ("duration_seconds", "12", "duration_seconds"),
        ("duration_seconds", -0.1, "duration_seconds"),
        ("duration_seconds", math.nan, "duration_seconds"),
        ("duration_seconds", math.inf, "duration_seconds"),
        ("tool_calls", True, "tool_calls"), ("tool_calls", 1.5, "tool_calls"),
        ("tool_calls", -1, "tool_calls"),
        ("predicted_locations", [], "predicted_locations"),
        ("predicted_locations", [""], "predicted_locations"),
        ("predicted_locations", "path", "predicted_locations"),
    ],
)
def test_invalid_run_metrics_are_rejected(tmp_path, field, value, error) -> None:
    with pytest.raises(ValueError, match=error):
        _score(tmp_path, [_run(**{field: value})])


@pytest.mark.parametrize(
    "overrides",
    [
        {"execution_fidelity": "MECHANISM_REPRO"},
        {"scoring_status": "UNSCORED", "admission": None},
    ],
)
def test_unscored_submissions_remain_reported_but_leave_denominators(tmp_path, overrides) -> None:
    report = _score(tmp_path, [_run(**overrides)])
    assert report["submitted_cases"] == 1
    assert report["unscored_cases"] == 1
    assert report["evaluated_cases"] == 0
    assert report["coverage_rate"] == 0
    assert report["top1_exact_rate"] == 0
    assert report["mean_confidence_absolute_error"] is None
    assert report["results"][0]["scoring_status"] == "UNSCORED"


def test_claimed_scored_status_cannot_bypass_missing_admission(tmp_path) -> None:
    _assert_unscored(tmp_path, _run(scoring_status="SCORED", admission=None), "admission missing")


def test_unscored_case_does_not_dilute_scored_rates(tmp_path) -> None:
    unscored = _run(
        case_id="RW-OTELPY-4224", predicted_locations=["wrong"],
        predicted_root_cause_id="wrong", evidence=[], evidence_refs=[],
        counter_evidence_refs=[], execution_fidelity="MECHANISM_REPRO",
    )
    report = _score(tmp_path, [_run(), unscored])
    assert report["submitted_cases"] == 2
    assert report["evaluated_cases"] == 1
    assert report["top1_exact_rate"] == 1
    assert report["evidence_citation_rate"] == 1


def test_duplicate_case_is_rejected_before_field_validation(tmp_path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _score(tmp_path, [
            {"case_id": "RW-GRAFANA-123359"},
            {"case_id": "RW-GRAFANA-123359"},
        ])


@pytest.mark.parametrize(
    "mutation,error",
    [
        (lambda run: run.pop("execution_fidelity"), "requires FULL_UPSTREAM_REPLAY"),
        (lambda run: run.update(execution_fidelity="MECHANISM_REPRO"), "requires FULL_UPSTREAM_REPLAY"),
        (lambda run: run["admission"].pop("execution_fidelity"), "fidelity mismatch"),
        (lambda run: run["admission"].update(execution_fidelity="MECHANISM_REPRO"), "fidelity mismatch"),
        (lambda run: run["admission"].pop("repository_url"), "missing repository_url"),
        (lambda run: run["admission"].update(case_id="RW-OTELPY-4224"), "ownership mismatch"),
        (lambda run: run["admission"].update(run_id="forged-run"), "ownership mismatch"),
        (lambda run: run["admission"].update(base_sha="not-a-commit"), "commit identity invalid"),
        (lambda run: run["admission"].update(source_integrity_hash="sha256:bad"), "source_integrity_hash"),
        (lambda run: run["admission"].update(dependency_lock_hash="sha256:bad"), "dependency_lock_hash"),
        (lambda run: run["admission"].update(harness_integrity_hash="sha256:bad"), "harness_integrity_hash"),
        (lambda run: run["admission"].pop("environment"), "missing environment"),
        (lambda run: run["admission"]["environment"].update(container_image_digest="latest"), "container image"),
    ],
)
def test_missing_or_wrong_fidelity_and_provenance_fail_closed(tmp_path, mutation, error) -> None:
    run = _run()
    mutation(run)
    _rehash_admission(run)
    _assert_unscored(tmp_path, run, error)


@pytest.mark.parametrize(
    "mutation,error",
    [
        (lambda run: run["admission"].update(repetitions=run["admission"]["repetitions"][:2]), "repetitions insufficient"),
        (lambda run: run["admission"]["repetitions"][1].update(repetition_id="repetition-1"), "duplicate repetition"),
        (lambda run: run["admission"]["repetitions"][0].update(baseline_stable=False), "unstable repetition"),
        (lambda run: run["admission"]["repetitions"][0].update(incident_reproduced=False), "unstable repetition"),
        (lambda run: run["admission"]["repetitions"][0].update(verification_recovered=False), "unstable repetition"),
        (lambda run: run["admission"]["repetitions"][0].update(terminal_status="FAILED"), "repetition incomplete"),
    ],
)
def test_repetition_gate_failures_are_unscored(tmp_path, mutation, error) -> None:
    run = _run()
    mutation(run)
    _rehash_admission(run)
    _assert_unscored(tmp_path, run, error)


def test_comparator_failure_is_unscored(tmp_path) -> None:
    run = _run()
    run["admission"]["comparator_passed"] = False
    _rehash_admission(run)
    _assert_unscored(tmp_path, run, "comparator failed")


@pytest.mark.parametrize(
    "mutation,error",
    [
        (lambda run: run["evidence"][2].update(role="incident"), "evidence roles invalid"),
        (lambda run: run["evidence"].append(deepcopy(run["evidence"][0])), "evidence roles invalid"),
        (lambda run: run["evidence"][1].update(recorded_at="2026-08-20T10:00:00Z"), "evidence phase order invalid"),
        (lambda run: run["admission"]["repetitions"][0].update(evidence_roles=["incident", "baseline", "verification"]), "repetition roles invalid"),
        (lambda run: run["admission"]["repetitions"][0].update(evidence_hashes=["sha256:" + "f" * 64] * 3), "repetition evidence mismatch"),
    ],
)
def test_evidence_role_hash_and_order_failures_are_unscored(tmp_path, mutation, error) -> None:
    run = _run()
    mutation(run)
    for item in run["evidence"]:
        meaningful = {key: value for key, value in item.items() if key != "integrity_hash"}
        item["integrity_hash"] = canonical_hash(meaningful)
    _rehash_admission(run)
    _assert_unscored(tmp_path, run, error)


def test_stale_evidence_hash_after_content_mutation_is_rejected() -> None:
    run = _run()
    run["evidence"][1]["observed"] = {"queue_depth": 999}
    _rehash_admission(run)

    with pytest.raises(ValueError, match="evidence integrity mismatch"):
        _validate_admission(run)


def test_invalid_evidence_hash_format_is_rejected() -> None:
    run = _run()
    run["evidence"][1]["integrity_hash"] = "sha256:bad"
    _rehash_admission(run)

    with pytest.raises(ValueError, match="evidence integrity hash invalid"):
        _validate_admission(run)


def test_bad_admission_hash_is_unscored(tmp_path) -> None:
    run = _run()
    run["admission"]["admission_hash"] = "sha256:" + "0" * 64
    _assert_unscored(tmp_path, run, "admission integrity mismatch")
