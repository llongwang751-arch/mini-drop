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
        "allowed_pid": 123,
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


def test_autonomous_session_pre_authorizes_registered_perf_only():
    result = evaluate_tool_call(
        "start_perf_profile",
        {"agent_id": "agent-a", "pid": 123, "duration_seconds": 15, "sample_rate": 99},
        policy_context(session_pre_authorized=True),
    )
    assert result["decision"] == "ALLOW"
    assert {item["name"]: item["result"] for item in result["checks"]}[
        "HUMAN_APPROVAL"
    ] == "SESSION_PREAUTHORIZED"

    wrong_target = evaluate_tool_call(
        "start_perf_profile",
        {"agent_id": "agent-a", "pid": 999, "duration_seconds": 15, "sample_rate": 99},
        policy_context(session_pre_authorized=True),
    )
    assert wrong_target["decision"] == "DENY"


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


def test_host_io_cannot_support_target_process_even_with_legacy_support_predicate():
    item = evidence()
    item.scope.pid = 123
    item.observation = {"metadata": {"scope_semantics": "HOST_BLOCK_DEVICE", "target_attributed": False, "hypothesis_predicate": {"outcome": "SUPPORT"}}}
    result = classify_evidence(item)
    assert result["decision"] == "ACCEPT_LIMITED"
    assert not result["can_support_conclusion"]
    assert calibrate_confidence([item], [], 1.0) == 0
    item.observation["metadata"]["target_attributed"] = True
    assert classify_evidence(item)["can_support_conclusion"]


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


def test_generate_sre_remediation_advice_categories():
    from server.app.drop_insight.claim_verifier import generate_sre_remediation_advice

    claims = [
        {"claim_type": "SYS_METRIC_PROCESS_CPU_USAGE", "statement": "进程 CPU 1.8 核"},
        {"claim_type": "SYS_METRIC_MUTEX_WAIT", "statement": "锁等待时间: 120ms", "json_pointer": "/process/mutex_wait_ms"},
        {"claim_type": "SYS_METRIC_MEM_RSS", "statement": "物理内存: 4096MB", "json_pointer": "/summary/rss_mb"},
        {"claim_type": "SYS_METRIC_IO_AWAIT", "statement": "I/O 等待: 25ms", "json_pointer": "/summary/io_await_ms"},
        {"claim_type": "SYS_METRIC_TCP_RETRANS", "statement": "TCP 重传: 8%", "json_pointer": "/summary/tcp_retrans_pct"},
        {"claim_type": "SYS_METRIC_FD_COUNT", "statement": "FD 句柄: 950", "json_pointer": "/summary/fd_count"},
    ]
    for claim in claims:
        claim["direction"] = "SUPPORT"
    advice = generate_sre_remediation_advice(claims, "VERIFIED", conclusion_text="CPU 与锁争用根因确认")
    assert len(advice["mitigations"]) == 1
    assert len(advice["root_cause_fixes"]) >= 6

    # Verify specific mitigations exist
    titles = [m["title"] for m in advice["root_cause_fixes"]]
    assert any("CPU" in t for t in titles)
    assert any("锁" in t for t in titles)
    assert any("内存" in t for t in titles)
    assert any("缓冲" in t or "I/O" in t for t in titles)
    assert any("熔断" in t or "网络" in t for t in titles)
    assert any("FD" in t for t in titles)


def test_verification_result_requires_complete_criteria():
    from server.app.drop_insight.claim_verifier import _verification_result

    # 4 expected items, 4 covered (100%) -> VERIFIED
    res1 = _verification_result(
        claims=[{"direction": "SUPPORT"}, {"direction": "CONTROL"}],
        rejected=[],
        covered_expected={0, 1, 2, 3},
        covered_falsification=set(),
        n_expected=4,
        n_falsification=0,
    )
    assert res1["status"] == "VERIFIED"
    assert "remediation" in res1

    # Missing criteria cannot be averaged away, even with a control.
    res2 = _verification_result(
        claims=[{"direction": "SUPPORT"}, {"direction": "CONTROL"}],
        rejected=[],
        covered_expected={0, 1, 2, 3},
        covered_falsification=set(),
        n_expected=5,
        n_falsification=0,
    )
    assert res2["status"] == "PARTIAL_WITHOUT_COUNTER"
    assert res2["coverage_ratio"] == 0.8

    # 5 expected items, 3 covered (60%), 0 counters -> PARTIAL_WITHOUT_COUNTER
    res3 = _verification_result(
        claims=[{"direction": "SUPPORT"}, {"direction": "CONTROL"}],
        rejected=[],
        covered_expected={0, 1, 2},
        covered_falsification=set(),
        n_expected=5,
        n_falsification=0,
    )
    assert res3["status"] == "PARTIAL_WITHOUT_COUNTER"


def test_diagnostic_target_trace_context():
    from server.app.drop_insight.schemas import DiagnosticTarget

    target = DiagnosticTarget(
        service="order-service",
        agent_id="agent-prod-1",
        pid=1024,
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
    )
    dumped = target.model_dump(mode="json")
    assert dumped["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert dumped["span_id"] == "00f067aa0ba902b7"
    assert dumped["service"] == "order-service"
