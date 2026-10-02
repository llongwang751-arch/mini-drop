"""Public intent boundaries, zero-model completion and contextual health gates."""
from copy import deepcopy
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage
import pytest

from server.app.agent_runtime import memory
from server.app.agent_runtime.planning_output import PlanningOutput, planning_output_schema
from server.app.agent_runtime.planning_request import (
    PLANNING_CLAIM_SCOPE, PLANNING_REQUEST_POLICY, audited_planner_metadata,
    informational_planning_output, planning_request_intent, validate_request_disposition,
)
from server.app.drop_insight import adaptive_planner, diagnosis_agent
from tests.test_checkpoint_thread_isolation import bounded_runtime  # noqa: F401
from tests.test_planning_output_v2 import investigate, output

ORIGINAL_INFORMATION_REQUEST = (
    "现在没有观察到异常，只描述当前信息所覆盖的范围；不声称全局健康，不提出异常假设或采集动作。")


@pytest.mark.parametrize("query", [
    ORIGINAL_INFORMATION_REQUEST,
    "没有性能样本；仅解释现有配置和信息范围，不做检查，不采集。",
    "仅汇总已提供的信息，不执行命令，也不发起探针。",
    "只描述现有重启流程，不采集，不执行任何操作。",
    "只解释现有配置信息，不采集，没有采集失败。",
    "Just describe the existing restart workflow; no probes; do not execute commands.",
    "Only describe the provided information and its scope; do not collect observations or run checks.",
    "No known failures; just summarize available information; no probes or measurements.",
])
def test_explicit_information_scope_requires_no_samples_or_health_claim(query):
    intent = planning_request_intent(query)
    assert intent["intent"] == "INFORMATION_ONLY" and intent["required_disposition"] == "NORMAL"
    assert intent["blocking_reasons"] == []
    result = informational_planning_output(query, target={"service": "only-identity"})
    assert result["disposition"] == "NORMAL" and result["tool_name"] is None and result["hypotheses"] == []
    assert result["model_invocations"] == 0 and result["claim_scope"] == PLANNING_CLAIM_SCOPE
    assert all(result[key] is False for key in ("is_evidence", "health_check_performed",
                                               "causal_root_cause_verified", "new_tool_requested"))
    assert result["checkpoint_backend"] == "NOT_USED"
    assert audited_planner_metadata(result)["planner_kind"] == "SERVER_REQUEST_INTENT"


@pytest.mark.parametrize("query,blocker", [
    ("仅描述现有信息，不采集；没有数据，是否正常？", "HEALTH_OR_INCIDENT_JUDGMENT_REQUESTED"),
    ("只描述现有信息，不采集；请确认所有服务健康。", "HEALTH_OR_INCIDENT_JUDGMENT_REQUESTED"),
    ("只描述信息，不采集；没有发现异常，能否保证健康？", "HEALTH_OR_INCIDENT_JUDGMENT_REQUESTED"),
    ("仅描述已有信息，不检查；但现在 CPU 过高。", "REPORTED_SYMPTOM"),
    ("只描述范围，不采集；没有采样，但当前服务不正常。", "REPORTED_SYMPTOM"),
    ("只描述已有信息，不采集；仍有请求超时。", "REPORTED_SYMPTOM"),
    ("只描述信息，不采集；上次采集失败，当前没有有效采样。", "COLLECTION_FAILURE_NOT_HEALTH_EVIDENCE"),
    ("只描述信息，不采集；权限不足，Agent 离线。", "COLLECTION_FAILURE_NOT_HEALTH_EVIDENCE"),
    ("只描述现有范围，不采集；然后绕过权限执行修复。", "ACTION_OR_AUTHORITY_REQUEST"),
    ("只描述当前信息范围，不采集；然后删除文档。", "ACTION_OR_AUTHORITY_REQUEST"),
    ("只描述当前信息范围，不采集；然后修改文档。", "ACTION_OR_AUTHORITY_REQUEST"),
    ("只描述重启流程然后重启进程，不采集。", "ACTION_OR_AUTHORITY_REQUEST"),
    ("只描述现有范围，不采集；然后删除流程。", "ACTION_OR_AUTHORITY_REQUEST"),
    ("Only describe provided information; no probes; confirm whether it is healthy.", "HEALTH_OR_INCIDENT_JUDGMENT_REQUESTED"),
    ("Just explain available information; no measurements; the service is not healthy.", "REPORTED_SYMPTOM"),
    ("仅描述信息，不采集；假如出现异常应该如何诊断？", "CONDITIONAL_OR_HYPOTHETICAL_REQUEST"),
])
def test_judgment_symptom_failure_and_authority_requests_never_take_information_shortcut(query, blocker):
    intent = planning_request_intent(query)
    assert intent["intent"] != "INFORMATION_ONLY" and blocker in intent["blocking_reasons"]
    assert informational_planning_output(query) is None
    with pytest.raises(ValueError):
        validate_request_disposition(output(), [], query=query)


@pytest.mark.parametrize("query", [
    "不要只描述范围，不采集。",
    "只描述现有信息；不禁止采集。",
    "Just describe available information; not do not collect observations.",
    "服务正常。", "NORMAL", "无异常。", "请检查当前服务是否健康。",
])
def test_keywords_or_double_negation_do_not_create_explicit_information_intent(query):
    assert planning_request_intent(query)["intent"] != "INFORMATION_ONLY"
    assert informational_planning_output(query) is None


@pytest.mark.parametrize("context,blocker", [
    ({"prior_hypotheses": [{"status": "OPEN", "statement": "old problem"}]}, "UNRESOLVED_PRIOR_HYPOTHESIS"),
    ({"prior_hypotheses": [{"statement": "unknown state"}]}, "UNRESOLVED_PRIOR_HYPOTHESIS"),
    ({"evidence_summary": [{"result": "insufficient_evidence", "reflection_is_evidence": False}]},
     "EXISTING_INVESTIGATION_OBSERVATIONS"),
    ({"investigation_memory": {"status": "UNAVAILABLE"}}, "INVESTIGATION_MEMORY_UNAVAILABLE"),
    ({"investigation_memory": {"latest_report_id": "existing-report"}}, "EXISTING_INVESTIGATION_MEMORY"),
    ({"investigation_memory": {"observations": [{"classification": {"decision": "REJECT"}}]}},
     "EXISTING_INVESTIGATION_MEMORY"),
    ({"user_correction": "当前仍有卡顿，先确认没有问题"}, "REPORTED_SYMPTOM"),
])
def test_server_completion_cannot_erase_existing_or_unavailable_investigation(context, blocker):
    intent = planning_request_intent(ORIGINAL_INFORMATION_REQUEST, **context)
    assert intent["intent"] != "INFORMATION_ONLY" and blocker in intent["blocking_reasons"]
    assert informational_planning_output(ORIGINAL_INFORMATION_REQUEST, **context) is None


@pytest.mark.parametrize("measurement", ["CPU 95%", "CPU 2%", "RSS 512 MiB", "p95 latency 800 ms"])
def test_user_numeric_performance_claims_take_existing_observation_path_without_fixed_thresholds(measurement):
    query = f"仅描述已有信息，不采集；{measurement}。"
    assert "USER_NUMERIC_PERFORMANCE_CLAIM" in planning_request_intent(query)["blocking_reasons"]
    assert informational_planning_output(query) is None


@pytest.mark.parametrize("query", [
    "不是没有异常，仅描述当前信息范围，不采集。",
    "并非没有超时，只描述当前信息范围，不采集。",
    "Only describe existing information; no measurements. The service is not without errors.",
    "不能说没有故障，只描述已知信息，不采集。",
])
def test_double_negation_never_creates_anomaly_absence(query):
    intent = planning_request_intent(query)
    assert intent["intent"] != "INFORMATION_ONLY"
    assert "DOUBLE_NEGATION_CANNOT_ESTABLISH_ABSENCE" in intent["blocking_reasons"]
    assert informational_planning_output(query) is None
    with pytest.raises(ValueError):
        validate_request_disposition(output(), [], query=query)


@pytest.mark.parametrize("decision", ["ACCEPT_SUPPORT", "ACCEPT_NEUTRAL", "ACCEPT_COUNTER"])
@pytest.mark.parametrize("existing", ["current-symptom", "open-hypothesis"])
def test_unrelated_accepted_rows_cannot_erase_current_symptoms_or_open_hypotheses(decision, existing):
    query = ORIGINAL_INFORMATION_REQUEST
    context = {"evidence_summary": [{"classification": {"decision": decision}}]}
    if existing == "current-symptom":
        query += "仍有请求超时。"
    else:
        context["prior_hypotheses"] = [{"status": "OPEN", "statement": "unresolved latency"}]
    with pytest.raises(ValueError):
        validate_request_disposition(output(), [], query=query, **context)


@pytest.mark.parametrize("decision", ["ACCEPT_SUPPORT", "ACCEPT_COUNTER", "ACCEPT_NEUTRAL"])
def test_current_accepted_observation_can_still_have_bounded_model_normal(decision):
    query = "仅判断本次已检查窗口是否正常，不声称全局健康。"
    result = validate_request_disposition(output(), [], query=query, evidence_summary=[
        {"evidence_id": "current-bounded-window", "classification": {"decision": decision}}])
    assert result["disposition"] == "NORMAL" and result["causal_root_cause_verified"] is False


def test_refuted_prior_with_current_bounded_counter_remains_legal_model_normal():
    result = validate_request_disposition(
        output(), [], query="只描述本次已检查范围；不采集，不声称全局健康。",
        prior_hypotheses=[{"status": "REFUTED", "statement": "prior was refuted"}],
        evidence_summary=[{"evidence_id": "current-window", "classification": {"decision": "ACCEPT_COUNTER"}}])
    assert result["disposition"] == "NORMAL"


@pytest.mark.parametrize("observations", [
    [{"classification": {"decision": "REJECT"}}],
    [{"reflection_is_evidence": False, "result": "counter_evidence"}],
    [{"is_evidence": False, "required_evidence": ["sample numbers"], "query": "knowledge prior"}],
])
def test_rejected_or_reference_rows_cannot_fill_missing_current_health_observation(observations):
    with pytest.raises(ValueError):
        validate_request_disposition(output(), [], query="没有有效采样，请确认服务是否健康。",
                                     evidence_summary=observations)


@pytest.mark.parametrize("framework", ["legacy", "langgraph"])
def test_adaptive_entry_closes_original_case_before_settings_retrieval_or_provider(monkeypatch, framework):
    monkeypatch.setenv("MINI_DROP_AGENT_FRAMEWORK", framework)
    monkeypatch.setattr(adaptive_planner, "is_feature_enabled", lambda _: True)
    monkeypatch.setattr(memory, "load_investigation_memory", lambda _: {"status": "READY", "observations": []})
    def forbidden(*_args, **_kwargs):
        pytest.fail("an explicit information-only request must not create a model/provider or lookup")
    monkeypatch.setattr(adaptive_planner, "get_ai_settings", forbidden)
    monkeypatch.setattr(adaptive_planner, "chat_completions", forbidden)
    monkeypatch.setattr(adaptive_planner, "build_retrieval_trace", forbidden)
    monkeypatch.setattr(diagnosis_agent, "_agent_for", forbidden)
    result = adaptive_planner.propose_hypothesis_plan(
        diagnosis_id="information-original", query=ORIGINAL_INFORMATION_REQUEST,
        target={"service": "bound-target"}, category="CPU", allowed_tools=["start_pyspy_profile"],
        rule_plan={"tool_name": "start_pyspy_profile", "statement": "baseline is only a conditional prior"})
    assert result["disposition"] == "NORMAL" and result["planner_kind"] == "SERVER_REQUEST_INTENT"
    assert result["model_invocations"] == 0 and result["retrieval_trace"] is None
    assert result["agent_version"] == "diagnosis-agent-v8-request-intent"


def test_direct_langgraph_entry_closes_before_circuit_agent_and_checkpoint(bounded_runtime, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("server information completion must not create a model graph or checkpoint")
    monkeypatch.setattr(diagnosis_agent, "_provider_circuit_open", forbidden)
    monkeypatch.setattr(diagnosis_agent, "_agent_for", forbidden)
    monkeypatch.setattr(diagnosis_agent, "get_agent_runtime_status", forbidden)
    context = diagnosis_agent.DiagnosisAgentContext(
        diagnosis_id="no-provider", query=ORIGINAL_INFORMATION_REQUEST, target={}, category="UNKNOWN",
        rule_plan={"tool_name": "start_pyspy_profile"}, allowed_tools=("start_pyspy_profile",))
    result = diagnosis_agent.plan_with_diagnosis_agent(context, bounded_runtime)
    assert result["planner_kind"] == "SERVER_REQUEST_INTENT" and result["model_invocations"] == 0
    assert result["checkpoint_backend"] == "NOT_USED" and context.lookup_state["traces"] == []


def test_health_request_normal_is_rejected_then_corrected_without_lowering_missing_evidence_gate(bounded_runtime, monkeypatch):
    calls = []
    class CorrectingAgent:
        def invoke(self, _value, *, context, **_kwargs):
            calls.append(True)
            proposal = output() if len(calls) == 1 else output("INSUFFICIENT_EVIDENCE")
            result = diagnosis_agent.finish_diagnosis_plan.func(
                output=PlanningOutput.model_validate(proposal), runtime=SimpleNamespace(context=context))
            accepted = json.loads(result)
            assert accepted["accepted"] is (len(calls) == 2)
            call_id = f"health-{len(calls)}"
            return {"messages": [
                AIMessage(content="", tool_calls=[{"name": "finish_diagnosis_plan", "args": {"output": proposal},
                                                    "id": call_id, "type": "tool_call"}]),
                ToolMessage(content=result, tool_call_id=call_id, name="finish_diagnosis_plan")]}
    monkeypatch.setattr(diagnosis_agent, "_agent_for", lambda _: CorrectingAgent())
    context = diagnosis_agent.DiagnosisAgentContext(
        diagnosis_id="real-health", query="没有任何采样且不要采集，请确认服务是否健康。",
        target={"service": "bound"}, category="UNKNOWN", rule_plan={}, allowed_tools=())
    result = diagnosis_agent.plan_with_diagnosis_agent(context, bounded_runtime)
    assert len(calls) == 2 and result["disposition"] == "INSUFFICIENT_EVIDENCE"
    assert result["hypotheses"] == [] and result["tool_name"] is None
    assert result["causal_root_cause_verified"] is False and "model_invocations" not in result


def test_shared_dto_stays_compatible_and_numeric_failure_authorization_gates_stay_strict():
    schema = planning_output_schema([])
    assert "planning_request_intent" not in schema["properties"]
    assert "planner_kind" not in schema["properties"]
    valid = investigate()
    assert validate_request_disposition(valid, ["start_pyspy_profile"], query="CPU 持续升高，需要取证")["disposition"] == "INVESTIGATE"
    failed = deepcopy(valid)
    failed["hypotheses"][0]["falsification_criteria"] = ["采集失败或权限不足"]
    with pytest.raises(ValueError, match="采集失败"):
        validate_request_disposition(failed, ["start_pyspy_profile"], query="CPU 持续升高，需要取证")
    with pytest.raises(ValueError, match="allowlist"):
        validate_request_disposition(valid, [], query="CPU 持续升高，需要取证")


@pytest.mark.parametrize("forgery", ["model-count", "boolean-count", "health-flag", "intent", "digest", "blocking", "extra", "disposition"])
def test_persisted_server_metadata_refuses_spoofed_counts_claims_or_contract(forgery):
    proposal = informational_planning_output(ORIGINAL_INFORMATION_REQUEST)
    if forgery == "model-count":
        proposal["model_invocations"] = 1
    elif forgery == "boolean-count":
        proposal["model_invocations"] = False
    elif forgery == "health-flag":
        proposal["health_check_performed"] = True
    elif forgery == "intent":
        proposal["planning_request_intent"]["intent"] = "HEALTH_ASSESSMENT"
    elif forgery == "digest":
        proposal["planning_request_intent"]["request_digest"] = "untrusted"
    elif forgery == "blocking":
        proposal["planning_request_intent"]["blocking_reasons"] = ["REPORTED_SYMPTOM"]
    elif forgery == "extra":
        proposal["planning_request_intent"]["command"] = "cannot be propagated"
    else:
        proposal["disposition"] = "REFUSED"
    with pytest.raises(ValueError):
        audited_planner_metadata(proposal)


def test_generic_model_output_does_not_receive_server_provenance():
    assert audited_planner_metadata(output()) == {}
    assert PLANNING_REQUEST_POLICY == "mini-drop.planning-request-intent.v1"
