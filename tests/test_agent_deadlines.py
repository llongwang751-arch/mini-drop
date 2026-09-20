from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from server.app.agent_runtime import deadlines
from server.app.drop_insight import diagnosis_agent as agent


def diagnosis(age, mode="AUTONOMOUS"):
    return SimpleNamespace(id="deadline-test", mode=mode, budget_json={"max_duration_seconds": 300},
                           created_at=datetime.now(timezone.utc) - timedelta(seconds=age))


def test_wall_clock_includes_planning_and_reserves_finalization():
    check = deadlines.probe_deadline_check(diagnosis(270), "start_perf_profile", {"duration_seconds": 15})
    assert check["result"] == "FAIL"
    assert check["required_seconds"] == 45
    assert deadlines.probe_deadline_check(diagnosis(200), "start_perf_profile", {"duration_seconds": 15})["result"] == "PASS"
    assert deadlines.probe_deadline_check(diagnosis(400), "get_agent_status", {})["result"] == "PASS"
    assert deadlines.probe_deadline_check(diagnosis(400, "INTERACTIVE"), "start_perf_profile", {"duration_seconds": 15})["result"] == "PASS"


def test_verifier_exposes_gaps_without_upgrading_partial_report():
    from server.app.drop_insight.claim_verifier import _verification_result
    result = _verification_result([{"direction": "SUPPORT"}], [], {0}, set(), 2, 1)
    assert result["status"] == "PARTIAL_WITHOUT_COUNTER"
    assert result["verification"]["missing_expected"] == [1]
    assert result["verification"]["missing_falsification"] == [0]
    assert result["verification"]["needs_independent_counter_or_control"] is True


def test_model_calls_share_deadline_and_override_provider_timeout(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(agent.time, "monotonic", lambda: clock[0])
    context = SimpleNamespace(planning_deadline=145)
    request = ModelRequest(model=FakeListChatModel(responses=["ok"]), messages=[],
                           runtime=SimpleNamespace(context=context), model_settings={"timeout": 90})
    seen = []
    def handler(req):
        seen.append(req.model_settings["timeout"])
        return "handled"
    agent._bounded_model_call.wrap_model_call(request, handler)
    clock[0] = 140
    agent._bounded_model_call.wrap_model_call(request, handler)
    clock[0] = 145
    with pytest.raises(agent.PlanningDeadlineExceeded):
        agent._bounded_model_call.wrap_model_call(request, handler)
    assert seen == [45, 5]
    assert request.model_settings == {"timeout": 90}


def test_summarization_uses_bounded_copy(monkeypatch):
    monkeypatch.setattr(agent.time, "monotonic", lambda: 100)
    model = FakeListChatModel(responses=["summary"])
    middleware = agent.DeadlineSummarizationMiddleware(model=model, trigger=("messages", 2), keep=("messages", 1))
    captured = []
    def before(bounded, state, runtime):
        captured.append(bounded.model.kwargs["timeout"])
    monkeypatch.setattr(agent.SummarizationMiddleware, "before_model", before)
    middleware.before_model({}, SimpleNamespace(context=SimpleNamespace(planning_deadline=107)))
    assert captured == [7]
    assert middleware.model is model


def test_actual_summarization_with_bound_model(monkeypatch):
    monkeypatch.setattr(agent.time, "monotonic", lambda: 100)
    middleware = agent.DeadlineSummarizationMiddleware(
        model=FakeListChatModel(responses=["bounded summary"]), trigger=("messages", 2), keep=("messages", 1))
    result = middleware.before_model(
        {"messages": [HumanMessage(content="old question"), AIMessage(content="old answer"), HumanMessage(content="new question")]},
        SimpleNamespace(context=SimpleNamespace(planning_deadline=107)))
    assert result is not None
    assert "bounded summary" in str(result)


def test_expired_approval_cannot_create_task(monkeypatch):
    from server.app.database import init_db, new_session, reset_engine
    from server.app.models import DropInsightSessionModel, DropInsightToolCallModel
    from server.app.drop_insight import service
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    try:
        now = datetime.now(timezone.utc)
        session = new_session()
        session.add(DropInsightSessionModel(id="late", query="cpu", target_json={}, time_range_json={},
            mode="AUTONOMOUS", budget_json={"max_duration_seconds": 300}, status="COLLECTING",
            created_at=now - timedelta(seconds=280), updated_at=now))
        session.flush()
        session.add(DropInsightToolCallModel(id="late-call", diagnosis_id="late", tool_name="start_perf_profile",
            arguments_json={"duration_seconds": 15}, status="APPROVED", policy_decision="ALLOW", policy_reason="approved before delay",
            budget_reservation_status="RESERVED", budget_reservation_json={"duration_seconds": 15},
            requested_by="agent", created_at=now))
        session.commit()
        session.close()
        result = service._execute_approved_tool_call("late-call")
        assert result.status == "FAILED"
        assert result.task_id is None
        assert result.budget_reservation_status == "RELEASED"
        assert result.result_json["error"] == "wall_clock_budget_exhausted"
    finally:
        reset_engine()
