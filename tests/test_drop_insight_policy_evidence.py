from datetime import datetime, timezone

from server.app.drop_insight.evidence import (
    EvidenceEnvelope,
    calibrate_confidence,
    classify_evidence,
)
from server.app.drop_insight.policy import PolicyContext, evaluate_tool_call
from server.app.drop_insight.artifact_evidence import assess_artifact_evidence


def policy_context(**overrides):
    values = {
        "allowed_agent_ids": frozenset({"agent-a"}),
        "agent_capabilities": frozenset({"perf_cpu", "sys_metrics"}),
        "max_risk_level": "R2",
        "used_tool_calls": 0,
        "max_tool_calls": 12,
    }
    values.update(overrides)
    return PolicyContext(**values)


def test_policy_requires_human_approval_for_perf():
    result = evaluate_tool_call(
        "start_perf_profile",
        {"agent_id": "agent-a", "pid": 123, "duration_seconds": 15, "sample_rate": 99},
        policy_context(),
    )
    assert result["decision"] == "REQUIRE_APPROVAL"


def test_policy_denies_unknown_argument_and_out_of_scope_agent():
    unknown = evaluate_tool_call(
        "get_agent_status",
        {"agent_id": "agent-a", "shell": "anything"},
        policy_context(),
    )
    assert unknown["decision"] == "DENY"
    out_of_scope = evaluate_tool_call(
        "get_agent_status",
        {"agent_id": "agent-b"},
        policy_context(),
    )
    assert out_of_scope["decision"] == "DENY"


def evidence(evidence_id="ev-1", *, degraded=False, sample_count=1000, tool_name="perf"):
    return EvidenceEnvelope(
        evidence_id=evidence_id,
        diagnosis_id="diag-1",
        evidence_type="PERF_HOT_FUNCTION",
        source={
            "tool_name": tool_name,
            "task_id": "task-1",
            "task_attempt_id": "attempt-1",
            "artifact_id": "artifact-1",
            "artifact_sha256": "a" * 64,
            "analysis_job_id": "analysis-job-1",
            "analyzer_type": "collector.perf_cpu",
            "analyzer_version": "1.0",
            "analyzer_output_schema_version": "1.0.0",
            "observation_json_pointer": "/",
        },
        scope={"agent_id": "agent-a", "service": "order-service", "pid": 123},
        time_range={
            "start": datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 7, 27, 10, 1, tzinfo=timezone.utc),
        },
        observation={"entity": "calculate_price", "value": 72.4, "unit": "percent"},
        quality={
            "level": "HIGH",
            "sample_count": sample_count,
            "degraded": degraded,
            "target_match": True,
            "time_overlap": True,
        },
    )


def test_degraded_evidence_is_kept_but_cannot_support_conclusion():
    result = classify_evidence(evidence(degraded=True))
    assert result["decision"] == "ACCEPT_LIMITED"
    assert result["can_support_conclusion"] is False


def test_confidence_is_program_calculated_and_counter_evidence_reduces_it():
    support = [evidence("ev-1", tool_name="perf"), evidence("ev-2", tool_name="sys_metrics")]
    without_counter = calibrate_confidence(support, [], 1.0)
    with_counter = calibrate_confidence(support, [evidence("counter")], 1.0)
    assert 0 < with_counter < without_counter < 1


def test_sys_metrics_uses_a_collector_specific_sample_threshold():
    enough = evidence(
        "sys-enough",
        sample_count=5,
        tool_name="sys_metrics",
    )
    limited = evidence(
        "sys-limited",
        sample_count=4,
        tool_name="sys_metrics",
    )

    assert classify_evidence(enough)["can_support_conclusion"] is True
    assert classify_evidence(limited)["can_support_conclusion"] is False
    assert "样本数不足 5" in classify_evidence(limited)["reasons"]


def test_raw_artifact_is_rejected_as_ai_evidence():
    assessment = assess_artifact_evidence(
        "perf_cpu",
        "raw",
        {"sample_count": 1000},
        analyzer_validated=True,
    )
    envelope = evidence()
    envelope.quality.schema_valid = assessment.schema_valid

    result = classify_evidence(envelope)

    assert result["decision"] == "REJECT"
    assert result["can_support_conclusion"] is False
    assert "产物不属于已注册的诊断证据契约" in result["reasons"]


def test_unknown_sample_count_is_kept_but_not_used_for_conclusion():
    assessment = assess_artifact_evidence(
        "java_async",
        "java_flamegraph_html",
        {"duration_sec": 10},
        analyzer_validated=True,
    )
    envelope = evidence()
    envelope.quality.sample_count = assessment.sample_count
    envelope.quality.sample_count_known = assessment.sample_count_known
    envelope.quality.minimum_samples = assessment.minimum_samples

    result = classify_evidence(envelope)

    assert result["decision"] == "ACCEPT_LIMITED"
    assert result["can_support_conclusion"] is False
    assert "样本数量未知" in result["reasons"]


def test_unvalidated_artifact_is_rejected():
    assessment = assess_artifact_evidence(
        "sys_metrics",
        "sys_metrics",
        {"sample_count": 10},
        analyzer_validated=False,
    )
    envelope = evidence(tool_name="sys_metrics", sample_count=10)
    envelope.quality.analyzer_validated = assessment.analyzer_validated

    result = classify_evidence(envelope)

    assert result["decision"] == "REJECT"
    assert "产物未经过 Analyzer Job 验证" in result["reasons"]
