"""Versioned production checkpoint keys survive LangGraph's empty root namespace."""
from copy import deepcopy
import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
import pytest

from server.app.agent_runtime import memory
from server.app.agent_runtime.runtime import checkpoint_thread_id
from server.app.ai_provider import AISettings
from server.app.drop_insight import diagnosis_agent


@pytest.fixture
def bounded_runtime(monkeypatch):
    monkeypatch.setattr(diagnosis_agent, "_provider_circuit_open", lambda _: (False, None))
    monkeypatch.setattr(diagnosis_agent, "_record_provider_success", lambda _: None)
    monkeypatch.setattr(diagnosis_agent, "planning_seconds", lambda *_: 60)
    monkeypatch.setattr(diagnosis_agent, "get_agent_runtime_status", lambda: {"actual_backend": "memory"})
    monkeypatch.setattr(memory, "load_investigation_memory", lambda _: {})
    return AISettings("full", "test", "https://example.invalid", "test", "test", True, True, True)


def test_production_planning_uses_isolated_keys_and_keeps_legacy_history(bounded_runtime, monkeypatch):
    proposal = {
        "schema_version": "mini-drop.planning-output.v2", "disposition": "NORMAL",
        "reasoning_summary": "描述范围内没有提出待验证异常", "tool_name": None, "hypotheses": [],
        "missing_evidence": [], "limitations": ["仅规划范围，未进行实际健康检查"],
        "causal_root_cause_verified": False,
    }
    graph = StateGraph(MessagesState, context_schema=diagnosis_agent.DiagnosisAgentContext)
    graph.add_node("validated_finish", lambda _: {"messages": [
        AIMessage(content="", tool_calls=[{"name": "finish_diagnosis_plan", "args": {"output": proposal},
                                           "id": "finish-call", "type": "tool_call"}]),
        ToolMessage(content=json.dumps({"accepted": True, "proposal": proposal}),
                    tool_call_id="finish-call", name="finish_diagnosis_plan"),
    ]})
    graph.add_edge(START, "validated_finish")
    graph.add_edge("validated_finish", END)
    saver = InMemorySaver()
    compiled = graph.compile(checkpointer=saver)
    diagnosis_id = "business-diagnosis-unchanged"
    legacy_config = {"configurable": {"thread_id": diagnosis_id, "checkpoint_ns": "legacy-v5"}}
    compiled.invoke({"messages": [HumanMessage(content="旧版本模型消息必须保留且不可复用")]}, legacy_config)
    # Reproduce the actual framework behavior that defeated namespace-only isolation.
    legacy = saver.get_tuple({"configurable": {"thread_id": diagnosis_id}})
    assert legacy.config["configurable"]["checkpoint_ns"] == ""
    assert saver.get_tuple(legacy_config) is None
    legacy_checkpoint = deepcopy(legacy.checkpoint)

    monkeypatch.setattr(diagnosis_agent, "_agent_for", lambda _: compiled)
    context = diagnosis_agent.DiagnosisAgentContext(
        diagnosis_id=diagnosis_id, query="仅描述新版本覆盖的范围", target={}, category="UNKNOWN",
        rule_plan={}, allowed_tools=(),
    )
    result = diagnosis_agent.plan_with_diagnosis_agent(context, bounded_runtime)
    assert result["disposition"] == "NORMAL"
    current_key = checkpoint_thread_id(diagnosis_id, agent_version=diagnosis_agent.AGENT_VERSION)
    current = saver.get_tuple({"configurable": {"thread_id": current_key}})
    assert current is not None and current.config["configurable"]["checkpoint_ns"] == ""
    assert not any("旧版本模型消息" in str(message.content)
                   for message in current.checkpoint["channel_values"]["messages"])
    assert saver.get_tuple({"configurable": {"thread_id": diagnosis_id}}).checkpoint == legacy_checkpoint

    # A later behavior version cannot silently inherit this version's messages.
    monkeypatch.setattr(diagnosis_agent, "AGENT_VERSION", "diagnosis-agent-next-contract")
    diagnosis_agent.plan_with_diagnosis_agent(context, bounded_runtime)
    next_key = checkpoint_thread_id(diagnosis_id, agent_version=diagnosis_agent.AGENT_VERSION)
    next_checkpoint = saver.get_tuple({"configurable": {"thread_id": next_key}})
    assert next_key != current_key and next_checkpoint is not None
    assert len(next_checkpoint.checkpoint["channel_values"]["messages"]) == 3
    assert len(current.checkpoint["channel_values"]["messages"]) == 3
    assert context.diagnosis_id == diagnosis_id


def test_production_scope_uses_its_own_versioned_thread(bounded_runtime, monkeypatch):
    captured = []
    candidate = {"binding_id": "binding-opaque-signed-handle", "eligible": True,
                 "service": "service", "process": "python", "collector_capabilities": ["sys_metrics"]}
    class ScopeAgent:
        def invoke(self, *_args, **kwargs):
            captured.append(kwargs)
            return {"messages": [ToolMessage(name="select_diagnosis_scope", tool_call_id="scope",
                content=json.dumps({"accepted": True, "selection": {
                    "binding_id": candidate["binding_id"], "reasoning_summary": "选择签发的服务候选"}}))]}
    monkeypatch.setattr(diagnosis_agent, "_scope_agent_for", lambda _: ScopeAgent())
    result = diagnosis_agent.select_scope_with_diagnosis_agent(
        diagnosis_id="same-business-id", query="描述服务", candidates=[candidate], settings=bounded_runtime,
    )
    assert result["binding_id"] == candidate["binding_id"]
    assert captured[0]["context"].diagnosis_id == "same-business-id"
    assert captured[0]["config"]["configurable"] == {
        "thread_id": checkpoint_thread_id("same-business-id", agent_version=diagnosis_agent.SCOPE_AGENT_VERSION)}
    assert captured[0]["config"]["configurable"]["thread_id"] != checkpoint_thread_id("same-business-id")


@pytest.mark.parametrize("diagnosis_id,version", [("", "version"), ("  ", "version"), ("diagnosis", "")])
def test_empty_checkpoint_identity_cannot_merge_unrelated_sessions(diagnosis_id, version):
    with pytest.raises(ValueError):
        checkpoint_thread_id(diagnosis_id, agent_version=version)
