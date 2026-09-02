import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from server.app.ai_provider import AISettings
from server.app.drop_insight import adaptive_planner
from server.app.drop_insight.diagnosis_agent import (
    AGENT_FRAMEWORK,
    DiagnosisAgentContext,
    _accepted_tool_payload,
    _database_url_for_psycopg,
    request_diagnostic_probe,
)


def _context(**overrides):
    values = {
        "diagnosis_id": "insight_agent_test",
        "query": "Python 服务 CPU 升高",
        "target": {"agent_id": "agent-a", "pid": 123},
        "category": "PYTHON_RUNTIME",
        "rule_plan": {"tool_name": "start_pyspy_profile"},
        "allowed_tools": ("start_pyspy_profile",),
        "active_skill": {"skill_id": "skill-v1", "probe_order": ["start_pyspy_profile"]},
    }
    values.update(overrides)
    return DiagnosisAgentContext(**values)


def _proposal(tool_name="start_pyspy_profile"):
    return {
        "reasoning_summary": "先验证 Python 用户态热点是否集中",
        "tool_name": tool_name,
        "hypotheses": [
            {
                "statement": "CPU 由少数 Python 热点函数主导",
                "expected_observations": ["采样集中在少数调用栈"],
                "falsification_criteria": ["样本分散且无显著热点"],
                "rationale": "规则基线和服务运行时均指向 Python",
            }
        ],
    }


def test_probe_tool_accepts_only_server_allowlisted_tools():
    runtime = SimpleNamespace(context=_context())
    accepted = request_diagnostic_probe.func(
        **_proposal(),
        runtime=runtime,
    )
    rejected = request_diagnostic_probe.func(
        **_proposal("start_perf_profile"),
        runtime=runtime,
    )

    assert json.loads(accepted)["accepted"] is True
    assert json.loads(rejected)["accepted"] is False


def test_agent_result_is_read_from_validated_tool_call():
    proposal = _proposal()
    messages = [
        AIMessage(
            content="",
            tool_calls=[{
                "name": "request_diagnostic_probe",
                "args": proposal,
                "id": "call-1",
                "type": "tool_call",
            }],
        ),
        ToolMessage(
            content=json.dumps({"accepted": True, "proposal": proposal}),
            tool_call_id="call-1",
            name="request_diagnostic_probe",
        ),
    ]

    parsed = _accepted_tool_payload(messages)

    assert parsed is not None
    assert parsed["tool_name"] == "start_pyspy_profile"
    assert parsed["hypotheses"][0]["falsification_criteria"]


def test_agent_result_rejects_collection_failure_as_counter_evidence():
    proposal = _proposal()
    proposal["hypotheses"][0]["falsification_criteria"] = [
        "py-spy 因权限不足采集失败"
    ]
    messages = [
        AIMessage(
            content="",
            tool_calls=[{
                "name": "request_diagnostic_probe",
                "args": proposal,
                "id": "call-unsafe-counter",
                "type": "tool_call",
            }],
        ),
        ToolMessage(
            content=json.dumps({"accepted": True, "proposal": proposal}),
            tool_call_id="call-unsafe-counter",
            name="request_diagnostic_probe",
        ),
    ]

    assert _accepted_tool_payload(messages) is None


def test_postgres_checkpoint_url_uses_psycopg_native_scheme(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://mini_drop:secret@postgres:5432/mini_drop",
    )

    assert _database_url_for_psycopg() == (
        "postgresql://mini_drop:secret@postgres:5432/mini_drop"
    )


def test_adaptive_planner_delegates_context_and_skill_to_langgraph(monkeypatch):
    captured = {}
    monkeypatch.setenv("MINI_DROP_AGENT_FRAMEWORK", "langgraph")
    monkeypatch.setattr(adaptive_planner, "is_feature_enabled", lambda feature: True)

    def fake_plan(context, settings):
        captured["context"] = context
        captured["settings"] = settings
        return {
            **_proposal(),
            "agent_framework": AGENT_FRAMEWORK,
            "agent_version": "diagnosis-agent-v1",
            "checkpoint_backend": "memory",
        }

    from server.app.drop_insight import diagnosis_agent

    monkeypatch.setattr(diagnosis_agent, "plan_with_diagnosis_agent", fake_plan)
    result = adaptive_planner.propose_hypothesis_plan(
        diagnosis_id="insight_agent_test",
        query="Python 服务 CPU 升高",
        target={"agent_id": "agent-a", "pid": 123},
        category="PYTHON_RUNTIME",
        rule_plan={"tool_name": "start_pyspy_profile"},
        allowed_tools=["start_pyspy_profile"],
        active_skill={"skill_id": "skill-v1"},
    )

    assert result is not None and result["agent_framework"] == AGENT_FRAMEWORK
    assert captured["context"].diagnosis_id == "insight_agent_test"
    assert captured["context"].active_skill == {"skill_id": "skill-v1"}
    assert isinstance(captured["settings"], AISettings)
