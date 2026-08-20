from datetime import datetime, timezone

from server.app.drop_insight.claim_verifier import (
    resolve_json_pointer,
    verify_legacy_report_claims,
    verify_report_claims,
)
from server.app.drop_insight.evidence import EvidenceEnvelope
from server.app.rca.models import CauseEntry, DiagnosisReport


def _envelope(*, outcome="SUPPORT", percent=72.5, sha256="a" * 64):
    return EvidenceEnvelope(
        evidence_id=f"ev-{outcome.lower()}",
        diagnosis_id="diag-1",
        evidence_type="PERF_FLAMEGRAPH_JSON",
        source={
            "tool_name": "perf_cpu",
            "task_id": "task-1",
            "task_attempt_id": "attempt-1",
            "artifact_id": "42",
            "artifact_sha256": sha256,
            "analysis_job_id": "job-1",
            "analyzer_type": "collector.perf_cpu",
            "analyzer_version": "1.0.0",
            "analyzer_output_schema_version": "1.0.0",
            "observation_json_pointer": "/metadata",
        },
        scope={"agent_id": "agent-a", "pid": 123},
        time_range={
            "start": datetime(2026, 8, 5, 1, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 8, 5, 1, 1, tzinfo=timezone.utc),
        },
        observation={
            "metadata": {
                "sample_count": 1000,
                "hypothesis_predicate": {"outcome": outcome},
                "top_functions": [{"name": "calculate_price", "percent": percent}],
            }
        },
        quality={
            "level": "HIGH",
            "sample_count": 1000,
            "degraded": False,
            "target_match": True,
            "time_overlap": True,
            "schema_valid": True,
            "analyzer_validated": True,
            "minimum_samples": 100,
        },
    )


def test_json_pointer_resolves_escaped_tokens_and_arrays():
    assert resolve_json_pointer({"a/b": [{"~key": 7}]}, "/a~1b/0/~0key") == 7


def test_verified_claims_require_support_and_counter_for_final_verification():
    result = verify_report_claims(
        [("SUPPORT", _envelope()), ("COUNTER", _envelope(outcome="COUNTER"))],
        expected_observations=["hotspot exists"],
        falsification_criteria=["independent counter check"],
    )

    assert result["status"] == "VERIFIED"
    assert result["support_claim_count"] >= 1
    assert result["counter_claim_count"] >= 1
    assert result["coverage_ratio"] == 1.0


def test_invalid_artifact_digest_rejects_all_claims():
    result = verify_report_claims(
        [("SUPPORT", _envelope(sha256="forged"))],
        expected_observations=["hotspot exists"],
        falsification_criteria=["no hotspot"],
    )

    assert result["status"] == "INSUFFICIENT_EVIDENCE"
    assert result["claims"] == []
    assert result["rejected_claims"]
    assert "SHA-256" in result["rejected_claims"][0]["reasons"][0]


def test_claimed_value_mismatch_is_rejected(monkeypatch):
    envelope = _envelope(percent=72.5)
    from server.app.drop_insight import claim_verifier

    original = claim_verifier._claim_candidates

    def tampered(role, item):
        claims = original(role, item)
        claims[-1]["claimed_value"] = 99.9
        return claims

    monkeypatch.setattr(claim_verifier, "_claim_candidates", tampered)
    result = verify_report_claims(
        [("SUPPORT", envelope)],
        expected_observations=["hotspot exists"],
        falsification_criteria=["no hotspot"],
    )

    assert any(
        "differs from evidence value" in reason
        for claim in result["rejected_claims"]
        for reason in claim["reasons"]
    )


def _legacy_report(claims=("CPU 用户态占比 85.5%，存在热点",)) -> DiagnosisReport:
    return DiagnosisReport(
        summary="CPU 热点归因",
        ranked_causes=[
            CauseEntry(
                cause_id=f"c{index}",
                confidence=0.8,
                claim=claim,
                evidence_refs=["tool_results/cpu/output/avg_cpu_user_pct"],
            )
            for index, claim in enumerate(claims)
        ],
        facts=["CPU 用户态占比高"],
    )


def _legacy_evidence() -> dict:
    return {
        "tool_results": [
            {
                "tool_name": "cpu",
                "status": "ok",
                "evidence_ref": "tool_results/cpu/output/avg_cpu_user_pct",
                "input": {"tool": "sys_metrics"},
                "output": {"avg_cpu_user_pct": 85.5},
            }
        ]
    }


def test_legacy_claim_with_matching_evidence_is_accepted():
    result = verify_legacy_report_claims(_legacy_report(), _legacy_evidence())
    assert result["claims"], "expected at least one accepted legacy claim"
    assert result["claims"][0]["valid"] is True
    assert result["claims"][0]["direction"] == "SUPPORT"
    assert result["rejected_claims"] == []
    assert result["coverage_ratio"] == 1.0


def test_legacy_unresolvable_evidence_reference_is_rejected():
    report = _legacy_report()
    report.ranked_causes[0].evidence_refs = ["tool_results/missing/output/x"]
    result = verify_legacy_report_claims(report, _legacy_evidence())
    assert result["claims"] == []
    assert len(result["rejected_claims"]) == 1
    assert result["rejected_claims"][0]["claim_type"] == "LEGACY_EVIDENCE_REFERENCE"
    assert result["status"] == "INSUFFICIENT_EVIDENCE"


def test_legacy_claim_number_not_supported_by_evidence_is_rejected():
    report = _legacy_report(claims=("CPU 占比 99.9%，存在热点",))
    result = verify_legacy_report_claims(report, _legacy_evidence())
    assert result["claims"] == []
    assert any(
        "is not supported by evidence" in reason
        for claim in result["rejected_claims"]
        for reason in claim["reasons"]
    )


def _sys_envelope() -> EvidenceEnvelope:
    return EvidenceEnvelope(
        evidence_id="ev-sys",
        diagnosis_id="diag-1",
        evidence_type="SYS_METRICS",
        source={
            "tool_name": "sys_metrics",
            "task_id": "task-1",
            "task_attempt_id": "attempt-1",
            "artifact_id": "43",
            "artifact_sha256": "b" * 64,
            "analysis_job_id": "job-1",
            "analyzer_type": "collector.sys_metrics",
            "analyzer_version": "1.0.0",
            "analyzer_output_schema_version": "1.0.0",
            "observation_json_pointer": "/",
        },
        scope={"agent_id": "agent-a", "pid": 123},
        time_range={
            "start": datetime(2026, 8, 5, 1, 0, tzinfo=timezone.utc),
            "end": datetime(2026, 8, 5, 1, 1, tzinfo=timezone.utc),
        },
        observation={
            "summary": {
                "avg_cpu_user_pct": 85.5,
                "vmrss_mb": 2048,
                "fd_count": 300,
            }
        },
        quality={
            "level": "HIGH",
            "sample_count": 10,
            "degraded": False,
            "target_match": True,
            "time_overlap": True,
            "schema_valid": True,
            "analyzer_validated": True,
            "minimum_samples": 5,
        },
    )


def test_sys_metrics_evidence_produces_domain_field_claims():
    result = verify_report_claims(
        [("SUPPORT", _sys_envelope())],
        expected_observations=["hotspot exists"],
        falsification_criteria=["independent counter check"],
    )
    sys_claims = [
        item for item in result["claims"]
        if item["claim_type"].startswith("SYS_METRIC_")
    ]
    assert len(sys_claims) == 3
    by_pointer = {item["json_pointer"]: item["claimed_value"] for item in sys_claims}
    assert by_pointer["/summary/avg_cpu_user_pct"] == 85.5
    assert by_pointer["/summary/vmrss_mb"] == 2048
    assert by_pointer["/summary/fd_count"] == 300
    assert all(item["valid"] for item in sys_claims)


def test_domain_field_claim_with_tampered_value_is_rejected(monkeypatch):
    from server.app.drop_insight import claim_verifier

    original = claim_verifier._claim_candidates

    def tampered(role, item):
        claims = original(role, item)
        for claim in claims:
            if claim["claim_type"] == "SYS_METRIC_PROCESS_RSS":
                claim["claimed_value"] = 999999
        return claims

    monkeypatch.setattr(claim_verifier, "_claim_candidates", tampered)
    result = verify_report_claims(
        [("SUPPORT", _sys_envelope())],
        expected_observations=["hotspot exists"],
        falsification_criteria=["independent counter check"],
    )
    assert any(
        "differs from evidence value" in reason
        for claim in result["rejected_claims"]
        for reason in claim["reasons"]
    )
