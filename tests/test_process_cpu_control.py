import json
from types import SimpleNamespace

import pytest

from server.app.metric_analyzers import _parse_sys_metrics_document
from server.app.drop_insight.hypothesis_predicate import _compute_hypothesis_predicate


def document():
    return {"schema_version": "sys_metrics.v2", "pid": 123,
            "clock_ticks_per_second": 100,
            "samples": [{"offset_sec": 0, "captured_at_unix_ms": 1000, "process_start_ticks": 42, "process_cpu_ticks": 100},
                        {"offset_sec": 2, "captured_at_unix_ms": 3000, "process_start_ticks": 42, "process_cpu_ticks": 300}]}


def hypothesis(criteria=None):
    return SimpleNamespace(statement="Target process CPU hotspot",
                           expected_observations_json=["source_hot_function dominates function samples"],
                           falsification_criteria_json=criteria or ["Target process CPU usage is below 50%"])


def test_real_analyzer_counter_window_makes_control_reachable():
    metadata = _parse_sys_metrics_document(document())
    result = _compute_hypothesis_predicate(hypothesis(), metadata)
    assert result["outcome"] == "CONTROL"
    assert result["criterion_indexes"] == [0]
    assert result["metrics"]["process_cpu_core_usage"] == 100
    assert result["metrics"]["start_cpu_ticks"] == 100
    assert "function" not in result["reason"]


def test_low_cpu_is_counter_and_zero_is_observed():
    raw = document()
    raw["samples"][1]["process_cpu_ticks"] = 100
    result = _compute_hypothesis_predicate(hypothesis(), _parse_sys_metrics_document(raw))
    assert result["outcome"] == "COUNTER"
    assert result["metrics"]["process_cpu_core_usage"] == 0


@pytest.mark.parametrize("criterion", ["目标进程 CPU 占用率低于 50%。", "process cpu below 50%", "Target process CPU usage remains below 100%."])
def test_explicit_threshold_languages_and_exact_boundary(criterion):
    result = _compute_hypothesis_predicate(hypothesis([criterion]), _parse_sys_metrics_document(document()))
    assert result["outcome"] == "CONTROL"


@pytest.mark.parametrize("criterion", ["CPU samples evenly distributed", "I/O wait exceeds 50%", "CPU usage is low", "Host CPU below 50%", "Target process CPU below 50% and I/O wait is high", "Target process CPU below 0%", "Target process CPU below 10001%"])
def test_does_not_cover_other_domains_or_vague_criteria(criterion):
    result = _compute_hypothesis_predicate(hypothesis([criterion]), _parse_sys_metrics_document(document()))
    assert result is None or result["outcome"] != "CONTROL"


def test_only_exact_matching_slot_is_covered():
    result = _compute_hypothesis_predicate(hypothesis(["IO wait dominates", "Target process CPU below 50%", "source_hot_function absent"]), _parse_sys_metrics_document(document()))
    assert result["criterion_indexes"] == [1]


def test_conflicting_thresholds_keep_counter():
    result = _compute_hypothesis_predicate(hypothesis(["Target process CPU below 50%", "Target process CPU below 150%"]), _parse_sys_metrics_document(document()))
    assert result["outcome"] == "COUNTER"
    assert result["criterion_indexes"] == [1]


@pytest.mark.parametrize("key,value", [("process_cpu_ticks", None), ("process_cpu_ticks", -1), ("process_cpu_ticks", 1.5), ("process_cpu_ticks", True), ("process_start_ticks", None), ("process_start_ticks", 42.5)])
def test_incomplete_counter_samples_never_gain_control(key, value):
    raw = document()
    raw["samples"][0][key] = value
    if key == "process_start_ticks":
        with pytest.raises(ValueError):
            _parse_sys_metrics_document(raw)
    else:
        result = _compute_hypothesis_predicate(hypothesis(), _parse_sys_metrics_document(raw))
        assert result["outcome"] == "NEUTRAL"


@pytest.mark.parametrize("value", [None, 0, -1, True, 100.5, float("nan"), float("inf")])
def test_bad_clock_frequency_never_gains_control(value):
    raw = document()
    raw["clock_ticks_per_second"] = value
    result = _compute_hypothesis_predicate(hypothesis(), _parse_sys_metrics_document(raw))
    assert result["outcome"] == "NEUTRAL"


@pytest.mark.parametrize("key,value", [("process_cpu_core_usage", float("nan")), ("process_cpu_core_usage", 99), ("end_cpu_ticks", 99), ("sample_count", 1), ("end_unix_ms", 1500), ("source", "application_snapshot")])
def test_tampered_or_invalid_window_is_neutral(key, value):
    metadata = _parse_sys_metrics_document(document())
    metadata["process_cpu_window"][key] = value
    assert _compute_hypothesis_predicate(hypothesis(), metadata)["outcome"] == "NEUTRAL"


def test_legacy_identity_and_application_only_cpu_cannot_be_control():
    raw = document()
    raw["schema_version"] = "sys_metrics.v1"
    metadata = _parse_sys_metrics_document(raw)
    assert _compute_hypothesis_predicate(hypothesis(), metadata)["outcome"] == "NEUTRAL"
    metadata = _parse_sys_metrics_document(document())
    metadata["process_cpu_window"] = None
    metadata["summary"]["process_cpu_core_usage"] = 100
    assert _compute_hypothesis_predicate(hypothesis(), metadata)["outcome"] == "NEUTRAL"


def test_pid_reuse_is_rejected_before_control():
    raw = document()
    raw["samples"][1]["process_start_ticks"] = 99
    with pytest.raises(ValueError, match="identity changed"):
        _parse_sys_metrics_document(raw)


def envelope(metadata, tool="sys_metrics", sample_count=5):
    from server.app.drop_insight.evidence import EvidenceEnvelope
    from server.app.drop_insight.artifact_evidence import assess_artifact_evidence
    assessment = assess_artifact_evidence("sys_metrics", "sys_metrics", metadata, analyzer_validated=True)
    return EvidenceEnvelope(
        evidence_id=tool, diagnosis_id="diagnosis", evidence_type="SYS_METRICS",
        source={"tool_name": tool, "task_id": tool, "task_attempt_id": tool,
                "artifact_id": tool, "artifact_sha256": "a" * 64,
                "analysis_job_id": tool, "analyzer_version": "1",
                "analyzer_output_schema_version": metadata.get("schema_version", "profile.v1")},
        scope={"agent_id": "agent", "pid": 123},
        time_range={"start": "1970-01-01T00:00:01Z", "end": "1970-01-01T00:00:10Z"},
        observation={"metadata": metadata}, limitations=list(assessment.limitations),
        quality={"level": "HIGH", "sample_count": sample_count, "degraded": False,
                 "target_match": True, "time_overlap": True})


def test_control_passes_existing_provenance_but_alone_cannot_verify_root():
    from server.app.drop_insight.claim_verifier import verify_report_claims
    raw = document()
    raw["samples"] = [{"offset_sec": i, "captured_at_unix_ms": 1000 + i * 1000, "process_start_ticks": 42,
                       "process_cpu_ticks": 100 + 100 * i,
                       "application_metrics_json": json.dumps({"schema_version": "mini-drop.application-metrics.v1", "host_pid": 123})} for i in range(5)]
    metadata = _parse_sys_metrics_document(raw)
    h = hypothesis()
    metadata["hypothesis_predicate"] = _compute_hypothesis_predicate(h, metadata)
    control = envelope(metadata)
    result = verify_report_claims([("CONTROL", control)],
                                  expected_observations=h.expected_observations_json,
                                  falsification_criteria=h.falsification_criteria_json)
    assert result["control_claim_count"] > 0
    assert result["status"] == "INSUFFICIENT_EVIDENCE"
    assert result["coverage_ratio"] == 0.5
    assert result["verification"]["missing_expected"] == [0]


@pytest.mark.parametrize("rejection", ["target_match", "time_overlap", "analyzer_validated", "schema_valid", "sample_count", "artifact_sha256"])
def test_control_cannot_bypass_existing_source_gates(rejection):
    from server.app.drop_insight.claim_verifier import verify_report_claims
    raw = document()
    raw["samples"] = [{"offset_sec": i, "captured_at_unix_ms": 1000 + i * 1000,
                       "process_start_ticks": 42, "process_cpu_ticks": 100 + 100 * i}
                      for i in range(5)]
    metadata = _parse_sys_metrics_document(raw)
    h = hypothesis()
    metadata["hypothesis_predicate"] = _compute_hypothesis_predicate(h, metadata)
    control = envelope(metadata)
    from server.app.drop_insight.evidence import classify_evidence
    assert classify_evidence(control)["can_support_conclusion"]
    if rejection == "artifact_sha256":
        control.source.artifact_sha256 = ""
    elif rejection == "sample_count":
        control.quality.sample_count = 2
    else:
        setattr(control.quality, rejection, False)
    result = verify_report_claims([("CONTROL", control)],
                                  expected_observations=h.expected_observations_json,
                                  falsification_criteria=h.falsification_criteria_json)
    assert result["control_claim_count"] == 0
    assert not result["has_independent_counter_or_control"]
    assert result["rejected_claims"]


def test_actual_capture_duration_not_nominal_loop_index_defines_cpu():
    raw = document()
    raw["samples"][1]["captured_at_unix_ms"] = 5000
    metadata = _parse_sys_metrics_document(raw)
    assert metadata["process_cpu_window"]["process_cpu_core_usage"] == 50


@pytest.mark.parametrize("value", [None, 0, 500, 1000, 1500, True, float("nan")])
def test_missing_backward_short_or_invalid_capture_time_stays_neutral(value):
    raw = document()
    raw["samples"][1]["captured_at_unix_ms"] = value
    result = _compute_hypothesis_predicate(hypothesis(), _parse_sys_metrics_document(raw))
    assert result["outcome"] == "NEUTRAL"


def test_optional_application_absence_does_not_invalidate_os_domain():
    from server.app.drop_insight.artifact_evidence import assess_artifact_evidence
    raw = document()
    raw["samples"] = [{"offset_sec": i, "captured_at_unix_ms": 1000 + i * 1000,
                       "process_start_ticks": 42, "process_cpu_ticks": 100 + 100 * i}
                      for i in range(5)]
    metadata = _parse_sys_metrics_document(raw)
    assert metadata["application_metrics"] is None
    assert metadata["application_metrics_status"] == "UNAVAILABLE"
    assert metadata["application_metrics_limitations"]
    assert metadata["signals"].keys() <= {"cpu_hotspot"}
    assessment = assess_artifact_evidence("sys_metrics", "sys_metrics", metadata, analyzer_validated=True)
    assert not assessment.limitations
    raw["schema_version"] = "sys_metrics.v1"
    legacy = _parse_sys_metrics_document(raw)
    assessment = assess_artifact_evidence("sys_metrics", "sys_metrics", legacy, analyzer_validated=True)
    assert any("PID reuse" in item for item in assessment.limitations)


@pytest.mark.parametrize("field,value", [("pid", 123.5), ("namespace_pid", 12.5), ("namespace_pid", 0), ("namespace_pid", True), ("pid", -1)])
def test_fractional_or_invalid_process_identity_is_rejected(field, value):
    raw = document()
    raw[field] = value
    with pytest.raises(ValueError):
        _parse_sys_metrics_document(raw)


@pytest.mark.parametrize("mutation", ["pid", "no_scope_pid", "old_start", "late_end", "bad_window", "zero_start"])
def test_counter_identity_and_capture_time_are_bound_to_evidence_scope(mutation):
    from server.app.drop_insight.evidence import classify_evidence
    raw = document()
    raw["samples"] = [{"offset_sec": i, "captured_at_unix_ms": 1000 + i * 1000,
                       "process_start_ticks": 42, "process_cpu_ticks": 100 + 100 * i}
                      for i in range(5)]
    metadata = _parse_sys_metrics_document(raw)
    item = envelope(metadata)
    assert classify_evidence(item)["can_support_conclusion"]
    if mutation == "pid":
        metadata["process_identity"]["pid"] = 999
    elif mutation == "no_scope_pid":
        item.scope.pid = None
    elif mutation == "old_start":
        metadata["process_cpu_window"]["start_unix_ms"] = 999
    elif mutation == "late_end":
        metadata["process_cpu_window"]["end_unix_ms"] = 10001
    elif mutation == "bad_window":
        metadata["process_cpu_window"] = []
    else:
        metadata["process_cpu_window"]["start_unix_ms"] = 0
    item.observation["metadata"] = metadata
    result = classify_evidence(item)
    assert result["decision"] == "REJECT"
    assert any("CPU counter" in reason for reason in result["reasons"])


def test_counter_capture_exact_boundaries_and_naive_utc_are_accepted():
    from server.app.drop_insight.evidence import classify_evidence
    from datetime import datetime
    raw = document()
    raw["samples"] = [{"offset_sec": i, "captured_at_unix_ms": 1000 + i * 1000,
                       "process_start_ticks": 42, "process_cpu_ticks": 100 + 100 * i}
                      for i in range(5)]
    item = envelope(_parse_sys_metrics_document(raw))
    item.time_range.start = datetime(1970, 1, 1, 0, 0, 1)
    item.time_range.end = datetime(1970, 1, 1, 0, 0, 5)
    assert classify_evidence(item)["can_support_conclusion"]
