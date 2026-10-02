"""Real production completion/correction boundaries for disposition-first prompts."""
from copy import deepcopy
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage
import pytest

from server.app.agent_runtime.planning_output import PlanningOutput, validate_planning_output
from server.app.drop_insight import diagnosis_agent
from tests.test_checkpoint_thread_isolation import bounded_runtime  # noqa: F401
from tests.test_planning_output_v2 import output, investigate


def executed_finish(payload, context, call_id):
    response = diagnosis_agent.finish_diagnosis_plan.func(
        output=PlanningOutput.model_validate(payload), runtime=SimpleNamespace(context=context))
    return [AIMessage(content="", tool_calls=[{"name": "finish_diagnosis_plan", "args": {"output": payload},
             "id": call_id, "type": "tool_call"}]),
            ToolMessage(content=response, tool_call_id=call_id, name="finish_diagnosis_plan")]


@pytest.mark.parametrize("disposition", ["NORMAL", "INSUFFICIENT_EVIDENCE", "REFUSED"])
def test_rejected_plan_can_correct_to_each_stop_state_without_probe_or_extra_turn(bounded_runtime, monkeypatch, disposition):
    invalid = investigate()
    invalid["hypotheses"][0]["falsification_criteria"] = ["采集失败或权限不足"]
    stop = output(disposition)
    captured = []

    class CorrectiveAgent:
        def invoke(self, value, *, context, **_kwargs):
            captured.append(value)
            if len(captured) == 1:
                messages = executed_finish(invalid, context, "rejected-call")
                result = json.loads(messages[-1].content)
                assert result["accepted"] is False and result["code"] == "INVALID_PLANNING_OUTPUT"
                return {"messages": messages}
            assert len(captured) == 2, "no third correction or tool request is permitted"
            correction = value["messages"][0]["content"]
            assert "不表示新增业务异常" in correction
            assert all(state in correction for state in ["NORMAL", "INSUFFICIENT_EVIDENCE", "REFUSED"])
            assert "停止查询或探针请求" in correction
            return {"messages": executed_finish(stop, context, "accepted-stop")}

    monkeypatch.setattr(diagnosis_agent, "_agent_for", lambda _: CorrectiveAgent())
    context = diagnosis_agent.DiagnosisAgentContext(
        diagnosis_id="disposition-correction", query="仅判断描述范围", target={}, category="UNKNOWN",
        rule_plan={"tool_name": "start_pyspy_profile"}, allowed_tools=("start_pyspy_profile",))
    result = diagnosis_agent.plan_with_diagnosis_agent(context, bounded_runtime)
    assert len(captured) == 2
    assert result["disposition"] == disposition
    assert result["hypotheses"] == [] and result["tool_name"] is None
    assert result["causal_root_cause_verified"] is False
    assert context.lookup_state["traces"] == []


def test_noninvestigation_success_stops_after_one_correlated_finish(bounded_runtime, monkeypatch):
    calls = []
    class StoppingAgent:
        def invoke(self, value, *, context, **_kwargs):
            calls.append(value)
            return {"messages": executed_finish(output(), context, "single-finish")}
    monkeypatch.setattr(diagnosis_agent, "_agent_for", lambda _: StoppingAgent())
    context = diagnosis_agent.DiagnosisAgentContext(
        diagnosis_id="single-stop", query="仅描述", target={}, category="UNKNOWN", rule_plan={}, allowed_tools=())
    assert diagnosis_agent.plan_with_diagnosis_agent(context, bounded_runtime)["disposition"] == "NORMAL"
    assert len(calls) == 1


def test_timeout_is_not_reinterpreted_as_normal_or_retried(bounded_runtime, monkeypatch):
    calls, failures = [], []
    class TimedOutAgent:
        def invoke(self, *_args, **_kwargs):
            calls.append(True)
            raise TimeoutError("bounded provider transport")
    monkeypatch.setattr(diagnosis_agent, "_agent_for", lambda _: TimedOutAgent())
    monkeypatch.setattr(diagnosis_agent, "_record_provider_failure", lambda _settings, exc: failures.append(type(exc)))
    context = diagnosis_agent.DiagnosisAgentContext(
        diagnosis_id="timeout-stop", query="用户声称正常", target={}, category="UNKNOWN", rule_plan={}, allowed_tools=())
    with pytest.raises(TimeoutError):
        diagnosis_agent.plan_with_diagnosis_agent(context, bounded_runtime)
    assert len(calls) == 1 and failures == [TimeoutError]


def test_investigation_numeric_and_collection_failure_gates_survive_prompt_revision():
    valid = investigate()
    assert validate_planning_output(valid, ["start_pyspy_profile"])["disposition"] == "INVESTIGATE"
    invalid = deepcopy(valid)
    invalid["hypotheses"][0]["falsification_criteria"] = ["采集失败"]
    with pytest.raises(ValueError, match="采集失败"):
        validate_planning_output(invalid, ["start_pyspy_profile"])
    invalid = deepcopy(valid)
    invalid["hypotheses"][0]["statement"] += "，进程 CPU 持续升高"
    invalid["hypotheses"][0]["falsification_criteria"] = ["请求延迟恢复"]
    with pytest.raises(ValueError):
        validate_planning_output(invalid, ["start_pyspy_profile"])
