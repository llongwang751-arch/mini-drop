from copy import deepcopy
from types import SimpleNamespace
import json
import pytest

from server.app.drop_insight.cpu_criteria import cpu_utilization_hypothesis, process_cpu_thresholds, cpu_observation_plan
from server.app.drop_insight.diagnosis_agent import request_diagnostic_probe, DiagnosisAgentContext, _diagnosis_system_prompt

# Exact first MODEL proposal from the preserved 2026-09-28 Python pilot.
PILOT_PROPOSAL = {'reasoning_summary': '如果 CPU 升高由 Python 用户代码引起，py-spy 应能捕获到少数函数占用大量 CPU 时间', 'tool_name': 'start_pyspy_profile', 'hypotheses': [{'statement': '目标 Python 进程存在用户态热点函数，导致 CPU 持续升高', 'expected_observations': ['py-spy 样本集中在少数 Python 函数（如 Top3 函数占比超过 50%）', '火焰图显示明显的函数调用热点区域'], 'falsification_criteria': ['Python 栈样本均匀分布，无明显热点函数集中', '主要样本落在解释器内部或系统库而非用户代码'], 'rationale': '如果 CPU 升高由 Python 用户代码引起，py-spy 应能捕获到少数函数占用大量 CPU 时间'}]}

def evaluate(proposal):
    return json.loads(request_diagnostic_probe.func(**proposal, runtime=SimpleNamespace(
        context=SimpleNamespace(allowed_tools=("start_pyspy_profile", "collect_sys_metrics")))))


def test_real_pilot_high_cpu_proposal_without_independent_counter_is_rejected():
    proposal = deepcopy(PILOT_PROPOSAL)
    result = evaluate(proposal)
    assert result["accepted"] is False
    assert result["code"] == "INVALID_FALSIFICATION"
    assert "collect_sys_metrics" in result["reason"]
    assert proposal == PILOT_PROPOSAL


@pytest.mark.parametrize("criterion", ["目标进程 CPU 占用率低于 50%", "Target process CPU usage is below 30%"])
def test_adding_cpu_threshold_cannot_silently_validate_old_stronger_claim(criterion):
    proposal = deepcopy(PILOT_PROPOSAL)
    proposal["hypotheses"][0]["falsification_criteria"].append(criterion)
    result = evaluate(proposal)
    assert result["accepted"] is False
    assert proposal["hypotheses"][0]["falsification_criteria"][-1] == criterion


@pytest.mark.parametrize("criterion", ["目标进程CPU占用率在故障时间窗内保持平稳且低于30%", "Target process CPU below 50% and I/O is high", "Python 栈样本分散", "目标进程 CPU 低于 0%"])
def test_compound_or_other_domain_counter_cannot_satisfy_cpu_plan(criterion):
    proposal = deepcopy(PILOT_PROPOSAL)
    proposal["hypotheses"][0]["falsification_criteria"] = [criterion]
    assert evaluate(proposal)["accepted"] is False


@pytest.mark.parametrize("statement", ["Python 用户态源码热点需要定位", "Python 存在 GIL 竞争", "目标锁等待导致请求变慢", "CPU 未升高但请求出现等待", "Java GC 暂停导致延迟"])
def test_non_cpu_utilization_claim_is_not_forced_to_invent_cpu_threshold(statement):
    proposal = deepcopy(PILOT_PROPOSAL)
    proposal["hypotheses"][0]["statement"] = statement
    assert evaluate(proposal)["accepted"] is True


@pytest.mark.parametrize("statement", ["CPU 持续升高", "CPU由少数Python热点函数主导", "Target process has high CPU usage", "Process CPU usage remains elevated", "A CPU-bound loop dominates the process"])
def test_explicit_cpu_utilization_assertions_are_recognized(statement):
    assert cpu_utilization_hypothesis(statement)


def test_shared_parser_covers_only_matching_slots():
    assert process_cpu_thresholds(["IO wait dominates", "目标进程 CPU 低于 40%", "Target process CPU below 30% and I/O high"]) == [(1, 40.0)]


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_runtime_prompt_always_includes_evidence_contract(language):
    context = DiagnosisAgentContext(diagnosis_id="test", query="CPU high", target={}, category="CPU_HOTSPOT",
                                    rule_plan={}, allowed_tools=("collect_sys_metrics",),
                                    user_preferences={"response_language": language})
    request = SimpleNamespace(runtime=SimpleNamespace(context=context),
                              override=lambda **kwargs: kwargs)
    result = _diagnosis_system_prompt.wrap_model_call(request, lambda value: value)
    text = str(result)
    assert "collect_sys_metrics" in text
    assert "falsification_criteria" in text
    assert "单核" in text


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
@pytest.mark.parametrize("with_threshold", [False, True])
def test_legacy_runtime_validates_actual_model_proposal_and_preserves_contract(monkeypatch, language, with_threshold):
    from server.app.drop_insight import adaptive_planner
    proposal = deepcopy(PILOT_PROPOSAL)
    if with_threshold:
        proposal["hypotheses"][0].update(cpu_observation_plan("PYTHON"))
    captured = []
    def chat(payload, timeout):
        captured.append(payload)
        return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"tool_calls": [
            {"function": {"arguments": json.dumps(proposal, ensure_ascii=False)}}]}}]})
    monkeypatch.setenv("MINI_DROP_AGENT_FRAMEWORK", "legacy")
    monkeypatch.setattr(adaptive_planner, "is_feature_enabled", lambda _: True)
    monkeypatch.setattr(adaptive_planner, "get_ai_settings", lambda: SimpleNamespace(provider="test", model="test"))
    monkeypatch.setattr(adaptive_planner, "chat_completions", chat)
    result = adaptive_planner.propose_hypothesis_plan(
        query="Python CPU 持续升高", target={}, category="PYTHON_RUNTIME", rule_plan={"tool_name": "start_pyspy_profile"},
        allowed_tools=["start_pyspy_profile", "collect_sys_metrics"], retrieval_trace={"test": True},
        user_preferences={"response_language": language})
    assert (result is not None) is with_threshold
    assert "独立的操作系统 CPU 数值反证" in captured[0]["messages"][0]["content"]
    if result:
        assert result["hypotheses"][0]["falsification_criteria"] == proposal["hypotheses"][0]["falsification_criteria"]
