from __future__ import annotations

from datetime import datetime, timezone

from server.app.drop_insight.service import _compute_hypothesis_predicate
from server.app.models import DropInsightHypothesisModel
from server.app.state_machine import now_utc


def _hypothesis(expected=None, falsification=None) -> DropInsightHypothesisModel:
    return DropInsightHypothesisModel(
        id="h1",
        diagnosis_id="d1",
        statement="calculate_price 热点",
        expected_observations_json=expected or [],
        falsification_criteria_json=falsification or [],
        status="OPEN",
        created_at=now_utc(),
        updated_at=now_utc(),
    )


def test_predicate_support_when_top_function_matches_expected():
    hypothesis = _hypothesis(
        expected=["perf samples concentrate in calculate_price"],
        falsification=["CPU samples remain evenly distributed"],
    )
    result = _compute_hypothesis_predicate(
        hypothesis,
        {"top_functions": [{"name": "calculate_price", "percent": 75.0}]},
    )
    assert result is not None
    assert result["outcome"] == "SUPPORT"
    assert result["version"] == "hypothesis-predicate-v2"


def test_predicate_counter_when_top_function_matches_falsification():
    hypothesis = _hypothesis(
        expected=["readdir 出现在 top functions"],
        falsification=["calculate_price 出现在 top functions"],
    )
    result = _compute_hypothesis_predicate(
        hypothesis,
        {"top_functions": [{"name": "calculate_price", "percent": 80.0}]},
    )
    assert result is not None
    assert result["outcome"] == "COUNTER"


def test_predicate_none_without_claimable_signal():
    hypothesis = _hypothesis(
        expected=["perf samples concentrate in calculate_price"],
        falsification=["CPU samples remain evenly distributed"],
    )
    result = _compute_hypothesis_predicate(
        hypothesis,
        {"top_functions": [{"name": "readdir", "percent": 40.0}]},
    )
    assert result is None


def test_predicate_none_without_top_functions():
    hypothesis = _hypothesis(expected=["cpu usage"], falsification=["no cpu"])
    assert (
        _compute_hypothesis_predicate(
            hypothesis, {"summary": {"avg_cpu_user_pct": 85.5}}
        )
        is None
    )




def test_predicate_supports_generic_user_space_hotspot_hypothesis():
    hypothesis = _hypothesis(
        expected=[
            "user-space samples exceed 60 percent",
            "one to three high-ratio hot functions exceed 20 percent",
            "hot function belongs to business code or a common library",
        ],
        falsification=["user-space samples are below 40 percent"],
    )
    hypothesis.statement = "CPU spike is caused by a user-space hot function"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {"top_functions": [
            {"name": "go-hotspot", "percent": 100.0},
            {"name": "[[vdso]]", "percent": 3.1},
        ]},
    )
    assert result is not None
    assert result["outcome"] == "SUPPORT"
    assert result["criterion_indexes"] == [0, 1]
    assert result["metrics"]["dominant_function"] == "go-hotspot"


def test_predicate_counters_kernel_hypothesis_with_dominant_user_hotspot():
    hypothesis = _hypothesis(
        expected=["kernel samples exceed 40 percent"],
        falsification=["kernel samples are below 20 percent"],
    )
    hypothesis.statement = "CPU spike is caused by a kernel syscall path"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {"top_functions": [{"name": "go-hotspot", "percent": 100.0}]},
    )
    assert result is not None
    assert result["outcome"] == "COUNTER"
