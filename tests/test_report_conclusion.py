from datetime import datetime, timedelta, timezone

import pytest

from server.app.drop_insight.evidence import (
    EvidenceEnvelope,
    EvidenceQuality,
    EvidenceScope,
    EvidenceSource,
    EvidenceTimeRange,
)
from server.app.drop_insight.report_conclusion import _concrete_report_finding, _derive_next_actions
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



def _finding(metrics, **metadata):
    return _concrete_report_finding([_profile_evidence({
        **metadata, "hypothesis_predicate": {"outcome": "SUPPORT", "metrics": metrics},
    })])


def test_java_relabelled_frame_uses_its_own_percentage():
    text = _finding({"dominant_function": "App$$Lambda.run", "dominant_percent": 99},
                    schema_version="java_async_profile.v1", profile_event="cpu",
                    top_functions=[{"name": "App$$Lambda.run", "percent": 99},
                                   {"name": "App.calculate", "percent": 24}])
    assert "App.calculate" in text
    assert "24.0%" in text
    assert "99.0%" not in text


@pytest.mark.parametrize("value", [None, "bad", float("nan"), float("inf"), -1, 101, True])
def test_unknown_or_invalid_percent_does_not_crash_or_invent_a_ratio(value):
    text = _finding({"dominant_function": "calculate", "dominant_percent": value})
    assert "calculate" in text
    assert "%" not in text


def test_java_relabelled_frame_does_not_inherit_missing_ratio():
    text = _finding({"dominant_function": "App$$Lambda.run", "dominant_percent": 99},
                    schema_version="java_async_profile.v1", profile_event="cpu",
                    top_functions=[{"name": "App.calculate"}])
    assert "App.calculate" in text
    assert "%" not in text


@pytest.mark.parametrize("field", ["gc_count", "gc_time_ms", "allocated_bytes"])
def test_missing_gc_counters_are_not_rendered_as_observed_zero(field):
    delta = {"gc_count": 3, "gc_time_ms": 4, "allocated_bytes": 1024}
    del delta[field]
    text = _finding({"dominant_function": "allocate", "dominant_percent": 70},
                    schema_version="java_async_profile.v1", profile_event="alloc",
                    jvm_gc_counters={"delta": delta})
    assert "独立 JVM 计数器同时记录" not in text


@pytest.mark.parametrize("metrics", [{"lock_wait_count": 2},
    {"lock_wait_count": "bad", "blocker_count": 1},
    {"lock_wait_count": -1, "blocker_count": 1}])
def test_incomplete_lock_counts_do_not_claim_a_confirmed_chain(metrics):
    assert _finding(metrics) is None


@pytest.mark.parametrize("event,label", [("cpu", "CPU 执行"), ("wall", "阻塞/等待"),
    ("lock", "锁等待"), ("unknown", "性能")])
def test_java_profile_event_keeps_observation_scope(event, label):
    text = _finding({"dominant_function": "App.work", "dominant_percent": 25},
                    schema_version="java_async_profile.v1", profile_event=event,
                    top_functions=[None, {}, {"name": "java/lang/Thread.run"},
                                   {"name": "jdk/internal/Runner.run"}])
    assert label in text and "App.work" in text
    assert "仍需" in text


@pytest.mark.parametrize("schema,label", [("pyspy_analysis.v1", "Python 源码"),
    ("go_pprof_analysis.v1", "Go CPU"), ("other.v1", "性能")])
def test_profile_labels_and_zero_percent_preserve_runtime(schema, label):
    text = _finding({"dominant_function": "work", "dominant_percent": 0}, schema_version=schema)
    assert label in text and "0.0%" in text


def test_unknown_sample_count_is_not_printed_as_known():
    e = _profile_evidence({"hypothesis_predicate": {"outcome": "SUPPORT", "metrics": {"dominant_function": "work"}}})
    e.quality.sample_count_known = False
    assert "个有效样本" not in _concrete_report_finding([e])


@pytest.mark.parametrize("metadata", [{}, {"hypothesis_predicate": []},
    {"hypothesis_predicate": {"outcome": "COUNTER"}},
    {"hypothesis_predicate": {"outcome": "SUPPORT", "metrics": []}}])
def test_non_support_or_unstructured_metadata_has_no_concrete_claim(metadata):
    assert _concrete_report_finding([_profile_evidence(metadata)]) is None


def test_observation_without_metadata_has_no_concrete_claim():
    e = _profile_evidence({}); e.observation = {}
    assert _concrete_report_finding([e]) is None


def test_valid_lock_counts_render_exact_sessions():
    text = _finding({"lock_wait_count": "2", "blocker_count": 1})
    assert "等待会话 2 个" in text and "阻塞会话 1 个" in text


@pytest.mark.parametrize("support,counter,prefix", [([], [], "本轮判断"),
    ([], ["c"], "本轮判断"), (["s"], ["c"], "本轮判断"),
    (["s"], [], "阶段性判断")])
def test_status_alone_cannot_fabricate_a_final_finding(support, counter, prefix):
    text = _derive_report_conclusion("hypothesis", support_refs=support, counter_refs=counter,
                                     verification_status="VERIFIED")
    assert text.startswith(prefix) and not text.startswith("根因结论")


def test_counter_evidence_prevents_final_title_even_with_verified_flag():
    e = _profile_evidence({"hypothesis_predicate": {"outcome": "SUPPORT", "metrics": {"dominant_function": "work"}}})
    text = _derive_report_conclusion("hypothesis", support_refs=["s"], counter_refs=["c"],
                                     supporting=[e], verification_status="VERIFIED")
    assert text.startswith("阶段性根因") and "反证" in text


@pytest.mark.parametrize("support,counter,expected", [([], [], "补充"),
    (["s"], ["c"], "证伪"), (["s"], [], "相同负载")])
def test_next_actions_follow_evidence_state(support, counter, expected):
    assert expected in _derive_next_actions(support_refs=support, counter_refs=counter)[0]


def test_allocated_types_are_distinct_and_bounded():
    text = _finding({"dominant_function": "App.allocate", "dominant_percent": 50},
                    schema_version="java_async_profile.v1", profile_event="alloc",
                    top_functions=[None, {"name": "a[]"}, {"name": "a[]"},
                                   {"name": "b[]"}, {"name": "c[]"}, {"name": "d[]"}])
    assert "a[]、b[]、c[]" in text and "d[]" not in text


def test_stronger_valid_finding_is_selected_without_invalid_ratio_dominance():
    def evidence(name, ratio):
        return _profile_evidence({"hypothesis_predicate": {"outcome": "SUPPORT", "metrics": {
            "dominant_function": name, "dominant_percent": ratio}}})
    text = _concrete_report_finding([evidence("bad", float("inf")), evidence("real", "75")])
    assert "real" in text and "75.0%" in text and "bad" not in text


@pytest.mark.parametrize("contract,percent,label", [
    ("go-profile-and-os-cpu.v1", 25, "累计采样占比 25.0%"),
    ("python-profile-and-os-cpu.v1", 33.3, "按函数归并的采样占比 33.3%"),
    ("go-profile-and-os-cpu.v1", None, None),
])
def test_registered_profile_observation_does_not_claim_dominance_or_causation(contract, percent, label):
    evidence = _profile_evidence({"schema_version": "go_pprof_analysis.v1", "hypothesis_predicate": {
        "outcome": "SUPPORT", "metrics": {"dominant_function": "observed_path", "dominant_percent": percent,
                                              "observation_contract": contract}}})
    result = _concrete_report_finding([evidence])
    assert "观察到可归属路径 `observed_path`" in result
    assert "最集中" not in result
    assert "不证明同窗一致、跨窗稳定" in result
    if label:
        assert label in result
    else:
        assert "%" not in result


@pytest.mark.parametrize("status,counter,title", [("VERIFIED", [], "已验证观测"), ("PARTIAL", [], "阶段性观测"), ("VERIFIED", ["counter"], "阶段性观测")])
def test_registered_observation_outer_title_never_claims_root_cause(status, counter, title):
    evidence = _profile_evidence({"hypothesis_predicate": {"outcome": "SUPPORT", "metrics": {
        "dominant_function": "work", "dominant_percent": 25, "observation_contract": "go-profile-and-os-cpu.v1"}}})
    text = _derive_report_conclusion("observation", support_refs=["s"], counter_refs=counter, supporting=[evidence], verification_status=status)
    assert text.startswith(title)
    assert "根因" not in text
    assert "不证明同窗一致、跨窗稳定" in text
