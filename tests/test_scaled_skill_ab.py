import json
from pathlib import Path

import pytest

from server.app.drop_insight.scaled_skill_ab import (
    assign_ab_arm,
    calibrate_retrieval_gates,
    evaluate_scaled_ab,
    expand_catalog,
    run_retrieval_stability,
)


FIXTURE = Path("tests/fixtures/skill_retrieval_benchmark.json")


def _catalog() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_expansion_is_deterministic_and_covers_safety_families() -> None:
    first = expand_catalog(
        _catalog(), positive_variants=2, negative_variants=3, drift_variants=1
    )
    second = expand_catalog(
        _catalog(), positive_variants=2, negative_variants=3, drift_variants=1
    )

    assert first == second
    assert len(first) == 60
    assert {item.variant_family for item in first} == {
        "POSITIVE_PARAPHRASE",
        "PSEUDO_SIMILAR_NEGATIVE",
        "ENVIRONMENT_DRIFT",
        "CAPABILITY_DRIFT",
    }


def test_ab_assignment_is_sticky_and_validates_ratio() -> None:
    assert assign_ab_arm("diagnosis-1") == assign_ab_arm("diagnosis-1")
    assert assign_ab_arm("diagnosis-1", treatment_ratio=0) == "NO_SKILL"
    assert assign_ab_arm("diagnosis-1", treatment_ratio=1) == "SKILL_ENABLED"
    with pytest.raises(ValueError, match="between zero and one"):
        assign_ab_arm("diagnosis-1", treatment_ratio=1.1)


def test_scaled_ab_reports_proxy_boundary_and_safety_metrics() -> None:
    catalog = _catalog()
    cases = expand_catalog(
        catalog, positive_variants=3, negative_variants=3, drift_variants=1
    )
    report = evaluate_scaled_ab(catalog, cases)

    assert report["dataset"]["case_count"] == len(cases)
    assert report["skill_enabled"]["accuracy"] > report["baseline_no_skill"]["accuracy"]
    assert report["measurement_boundary"]["evidence_verified_root_cause_accuracy"] == "NOT_MEASURED"
    assert report["measurement_boundary"]["production_ab_effect"] == "NOT_MEASURED"


def test_calibration_and_stability_are_reproducible() -> None:
    catalog = _catalog()
    cases = expand_catalog(
        catalog, positive_variants=2, negative_variants=2, drift_variants=1
    )
    calibration = calibrate_retrieval_gates(catalog, cases)
    stability = run_retrieval_stability(
        catalog, cases, minimum_iterations=2
    )

    assert calibration["split"]["train"] + calibration["split"]["validation"] == len(cases)
    assert 0.25 <= calibration["selected"]["match_threshold"] <= 0.65
    assert stability["iterations"] == 2
    assert stability["errors"] == 0
    assert stability["decision_drift_iterations"] == 0
    assert stability["passed"] is True
