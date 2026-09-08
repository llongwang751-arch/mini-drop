from datetime import datetime, timedelta, timezone

from server.app.drop_insight.evidence import (
    EvidenceEnvelope,
    EvidenceQuality,
    EvidenceScope,
    EvidenceSource,
    EvidenceTimeRange,
)
from server.app.drop_insight.service import _derive_report_conclusion


def _profile_evidence(metadata: dict, *, sample_count: int = 2842) -> EvidenceEnvelope:
    started = datetime(2026, 9, 7, 15, 44, tzinfo=timezone.utc)
    return EvidenceEnvelope(
        evidence_id="ev_java_alloc",
        diagnosis_id="insight_java",
        evidence_type="JAVA_ASYNC_PROFILE",
        source=EvidenceSource(
            tool_name="start_jvm_profile",
            task_id="task_java",
            task_attempt_id="attempt_java",
            artifact_id="809",
            artifact_sha256="a" * 64,
            analysis_job_id="analysis_java",
            analyzer_type="java_async",
            analyzer_version="test",
            analyzer_output_schema_version="java_async_profile.v1",
        ),
        scope=EvidenceScope(agent_id="agent_java", pid=277641),
        time_range=EvidenceTimeRange(
            start=started,
            end=started + timedelta(seconds=30),
        ),
        observation={"metadata": metadata},
        quality=EvidenceQuality(
            level="HIGH",
            sample_count=sample_count,
            degraded=False,
            target_match=True,
            time_overlap=True,
        ),
    )


def test_java_alloc_report_names_observed_function_and_boundary():
    evidence = _profile_evidence({
        "schema_version": "java_async_profile.v1",
        "profile_event": "alloc",
        "top_functions": [
            {"name": "Hotspot$$Lambda.0x00001.run", "percent": 100.0},
            {"name": "Hotspot.lambda$startWorkers$1", "percent": 100.0},
            {"name": "byte[]", "percent": 100.0},
        ],
        "hypothesis_predicate": {
            "outcome": "SUPPORT",
            "metrics": {
                "dominant_function": "Hotspot$$Lambda.0x00001.run",
                "dominant_percent": 100.0,
                "profile_event": "alloc",
            },
        },
    })

    conclusion = _derive_report_conclusion(
        "JVM 可能存在业务热点、GC 压力或锁竞争",
        support_refs=["ev_java_alloc"],
        counter_refs=[],
        supporting=[evidence],
        verification_status="PARTIAL_WITHOUT_COUNTER",
    )

    assert conclusion.startswith("阶段性根因：")
    assert "Hotspot.lambda$startWorkers$1" in conclusion
    assert "2842 个有效样本" in conclusion
    assert "byte[]" in conclusion
    assert "没有独立证明 GC 暂停或锁竞争" in conclusion
    assert "SUPPORTED" not in conclusion


def test_java_alloc_report_renders_independent_gc_counter_window():
    evidence = _profile_evidence({
        "schema_version": "java_async_profile.v1",
        "profile_event": "alloc",
        "top_functions": [
            {"name": "Hotspot.lambda$startWorkers$1", "percent": 78.0},
            {"name": "byte[]", "percent": 65.0},
        ],
        "jvm_gc_counters": {
            "schema_version": "jvm_gc_metrics.v1",
            "delta": {
                "allocated_bytes": 67108864,
                "gc_count": 11,
                "gc_time_ms": 83,
                "heap_used_bytes": -1024,
            },
        },
        "hypothesis_predicate": {
            "outcome": "SUPPORT",
            "metrics": {
                "dominant_function": "Hotspot.lambda$startWorkers$1",
                "dominant_percent": 78.0,
                "profile_event": "alloc",
            },
        },
    })

    conclusion = _derive_report_conclusion(
        "GC 停顿由堆内存分配压力导致",
        support_refs=["ev_java_alloc"],
        counter_refs=[],
        supporting=[evidence],
        verification_status="VERIFIED",
    )

    assert conclusion.startswith("根因结论：")
    assert "GC 11 次" in conclusion
    assert "GC 耗时增加 83 ms" in conclusion
    assert "不能冒充 Full GC 次数" in conclusion


def test_verified_profile_uses_final_root_cause_title():
    evidence = _profile_evidence({
        "schema_version": "go_pprof_analysis.v1",
        "top_functions": [{"name": "main.goCPUHotFunction", "percent": 82.5}],
        "hypothesis_predicate": {
            "outcome": "SUPPORT",
            "metrics": {
                "dominant_function": "main.goCPUHotFunction",
                "dominant_percent": 82.5,
            },
        },
    }, sample_count=1200)

    conclusion = _derive_report_conclusion(
        "Go 服务存在 CPU 热点",
        support_refs=["ev_go"],
        counter_refs=[],
        supporting=[evidence],
        verification_status="VERIFIED",
    )

    assert conclusion.startswith("根因结论：")
    assert "main.goCPUHotFunction" in conclusion
    assert "82.5%" in conclusion


def test_support_without_specific_finding_is_not_promoted_to_root_cause():
    conclusion = _derive_report_conclusion(
        "CPU、GC 或锁竞争之一可能是原因",
        support_refs=["ev_generic"],
        counter_refs=[],
        supporting=[],
        verification_status="PARTIAL_WITHOUT_COUNTER",
    )

    assert conclusion.startswith("阶段性判断：")
    assert "尚未定位到具体函数、资源或依赖" in conclusion
    assert "最终根因" in conclusion
