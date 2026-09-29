from copy import deepcopy
from types import SimpleNamespace
import pytest
from server.app.drop_insight.cpu_criteria import cpu_observation_plan, compile_cpu_observation_contract, cpu_plan_validation_error
from server.app.drop_insight.hypothesis_predicate import _compute_hypothesis_predicate
from server.app.metric_analyzers import _parse_sys_metrics_document


def hypothesis(plan):
    return SimpleNamespace(statement=plan["statement"], expected_observations_json=plan["expected_observations"], falsification_criteria_json=plan["falsification_criteria"])


def compile_plan(plan, **kwargs):
    return compile_cpu_observation_contract(plan["statement"], plan["expected_observations"], plan["falsification_criteria"], **kwargs)


@pytest.mark.parametrize("runtime", ["PYTHON", "GO"])
@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
@pytest.mark.parametrize("threshold", [0.000001, 20, 50, 100])
def test_factory_compiles_exact_observations_for_both_runtimes(runtime, language, threshold):
    plan = cpu_observation_plan(runtime, threshold, language=language)
    result = compile_plan(plan)
    assert result["status"] == "SUPPORTED"
    assert result["executable"]
    assert result["cpu_threshold"] == threshold
    assert result["expected_indexes"] == [0]
    assert cpu_plan_validation_error([plan]) is None


@pytest.mark.parametrize("runtime", ["PYTHON", "GO"])
@pytest.mark.parametrize("stronger", ["TopN函数占比超过50%", "函数热点在多个窗口保持稳定", "所有慢请求均由该函数导致", "CPU与profile在同一采样窗相关"])
def test_stronger_expected_slot_is_not_keyword_covered(runtime, stronger):
    plan = cpu_observation_plan(runtime)
    plan["expected_observations"].append(stronger)
    result = compile_plan(plan)
    assert result["status"] == "UNSUPPORTED"
    assert any(s["kind"] == "expected" and s["index"] == 1 for s in result["unsupported_slots"])
    metadata = {"schema_version": "go_pprof_analysis.v1" if runtime == "GO" else "pyspy_analysis.v1", "top_functions": [
        {"name": "main.businessWork", "file": "business.go", "line": 10, "percent": 90}]}
    result = _compute_hypothesis_predicate(hypothesis(plan), metadata)
    assert result["outcome"] == "NEUTRAL"
    assert result["criterion_indexes"] == []


@pytest.mark.parametrize("runtime", ["PYTHON", "GO"])
def test_extra_falsification_is_rejected_without_removing_or_rewriting_it(runtime):
    plan = cpu_observation_plan(runtime)
    plan["falsification_criteria"].append("样本均匀分布在多个函数")
    before = deepcopy(plan)
    result = compile_plan(plan)
    assert result["status"] == "UNSUPPORTED"
    assert any(s["kind"] == "falsification" and s["index"] == 1 for s in result["unsupported_slots"])
    assert plan == before


@pytest.mark.parametrize("statement,expected", [
    ("Python GIL contention", ["py-spy CPU waiting samples"]),
    ("Python锁竞争", ["CPU samples are in mutex"]),
    ("Go网络等待", ["pprof CPU samples may include network calls"]),
    ("Python普通源码路径", ["source function CPU samples"]),
])
def test_other_domains_with_cpu_in_expected_are_not_forced_to_assert_high_cpu(statement, expected):
    result = compile_cpu_observation_contract(statement, expected, ["等待栈没有出现"])
    assert result["status"] == "NOT_APPLICABLE"


@pytest.mark.parametrize("runtime,percentage,accepted", [("GO", 19.99, False), ("GO", 20, True), ("PYTHON", 69.99, False), ("PYTHON", 70, True)])
def test_profile_threshold_has_same_meaning_as_registered_expected(runtime, percentage, accepted):
    plan = cpu_observation_plan(runtime)
    metadata = {"schema_version": "go_pprof_analysis.v1" if runtime == "GO" else "pyspy_analysis.v1", "top_functions": [
        {"name": "main.businessWork", "file": "business.go", "line": 10, "percent": percentage}]}
    result = _compute_hypothesis_predicate(hypothesis(plan), metadata)
    assert (result["outcome"] == "SUPPORT") is accepted
    if accepted:
        assert result["criterion_indexes"] == compile_plan(plan)["expected_indexes"]
        assert result["metrics"]["dominant_percent"] == percentage
        assert result["metrics"]["observation_contract"] == compile_plan(plan)["contract_id"]


def test_25_percent_cannot_cover_original_go_50_percent_expected():
    plan = {"statement": "Go 服务存在 CPU 计算热点", "expected_observations": ["pprof TopN函数占用超过50%的CPU样本"], "falsification_criteria": ["目标进程CPU占用率低于50%"]}
    result = _compute_hypothesis_predicate(hypothesis(plan), {"schema_version": "go_pprof_analysis.v1", "top_functions": [
        {"name": "main.businessWork", "file": "business.go", "line": 10, "percent": 25}]})
    assert result["outcome"] == "NEUTRAL"
    assert result["criterion_indexes"] == []


@pytest.mark.parametrize("cpu,expected", [(0, "COUNTER"), (49, "COUNTER"), (50, "CONTROL"), (100, "CONTROL")])
def test_registered_cpu_condition_remains_falsifiable_by_real_counter(cpu, expected):
    raw = {"schema_version": "sys_metrics.v2", "pid": 123, "clock_ticks_per_second": 100,
           "samples": [{"offset_sec": i, "captured_at_unix_ms": 1000 + i * 1000,
                        "process_start_ticks": 42, "process_cpu_ticks": 100 + i * cpu} for i in range(5)]}
    result = _compute_hypothesis_predicate(hypothesis(cpu_observation_plan("PYTHON")), _parse_sys_metrics_document(raw))
    assert result["outcome"] == expected
    assert result["criterion_indexes"] == [0]


@pytest.mark.parametrize("field", ["statement", "expected_observations", "falsification_criteria"])
def test_missing_contract_components_cannot_be_compiled(field):
    plan = cpu_observation_plan("GO")
    plan[field] = "" if field == "statement" else []
    assert compile_plan(plan, runtime="GO")["status"] == "UNSUPPORTED"


def test_causal_statement_cannot_reuse_weak_observation_contract():
    plan = cpu_observation_plan("PYTHON")
    plan["statement"] = "Python CPU升高证明热点函数导致所有慢请求"
    result = compile_plan(plan)
    assert result["status"] == "UNSUPPORTED"
    assert any(s["kind"] == "statement" for s in result["unsupported_slots"])


def test_english_exceeds_strong_claim_cannot_bypass_contract_into_legacy_keywords():
    plan = {"statement": "Go process CPU usage exceeds 90% because a dominant function consumes time",
            "expected_observations": ["A dominant Go business function accounts for more than 50% of CPU samples"],
            "falsification_criteria": ["Target process CPU below 50%"]}
    assert compile_plan(plan)["status"] == "UNSUPPORTED"
    assert cpu_plan_validation_error([plan])
    result = _compute_hypothesis_predicate(hypothesis(plan), {"schema_version": "go_pprof_analysis.v1", "top_functions": [
        {"name": "main.work", "percent": 25, "file": "/app/main.go", "line": 1}]})
    assert result["outcome"] == "NEUTRAL"
    assert not result["criterion_indexes"]


def test_unknown_python_prose_cannot_use_legacy_symbol_match_for_support():
    plan = {"statement": "Python custom cause", "expected_observations": ["businessWork dominates all latency"],
            "falsification_criteria": ["no unexplained latency"]}
    assert compile_plan(plan)["status"] == "NOT_APPLICABLE"
    result = _compute_hypothesis_predicate(hypothesis(plan), {"schema_version": "pyspy_analysis.v1", "top_functions": [
        {"name": "businessWork", "percent": 25, "file": "/app/main.py", "line": 1}]})
    assert result["outcome"] == "NEUTRAL"
    assert not result["criterion_indexes"]


@pytest.mark.parametrize("registered", [False, True])
def test_structured_cpu_label_cannot_bypass_profile_contract(registered):
    plan = cpu_observation_plan("GO") if registered else {"statement": "Go custom cause",
        "expected_observations": ["CPU samples show main.work dominates all latency"], "falsification_criteria": ["no unexplained latency"]}
    result = _compute_hypothesis_predicate(hypothesis(plan), {"schema_version": "go_pprof_analysis.v1",
        "signals": {"cpu_hotspot": {"detected": True, "metrics": {"dominant_percent": 99}}},
        "top_functions": [{"name": "main.work", "percent": 25, "file": "/app/main.go", "line": 1}]})
    assert result["outcome"] == ("SUPPORT" if registered else "NEUTRAL")
    if registered:
        assert result["metrics"]["dominant_percent"] == 25
    else:
        assert result["criterion_indexes"] == []
