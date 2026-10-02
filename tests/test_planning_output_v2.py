"""Production output boundaries and persisted no-probe paths with isolated transports."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from server.app.agent_runtime.planning_output import (
    PLANNING_OUTPUT_SCHEMA, PlanningOutput, planning_output_schema, validate_planning_output,
)
from server.app.ai_provider import AISettings
from server.app.database import new_session
from server.app.drop_insight import adaptive_planner, diagnosis_agent, service
from server.app.drop_insight.schemas import RunPlannerRequest
from server.app.models import (
    DropInsightEventModel, DropInsightHypothesisModel, DropInsightReportModel,
    DropInsightSessionModel, DropInsightToolCallModel,
)
from tests.test_cpu_control_continuation import seeded  # noqa: F401
from tests.test_seven_gap_planning import planning  # noqa: F401

REAL_KNOWLEDGE_RETRIEVAL = service._record_planner_knowledge_retrieval


def output(disposition="NORMAL"):
    return {
        "schema_version": PLANNING_OUTPUT_SCHEMA, "disposition": disposition,
        "reasoning_summary": "当前描述或规划范围内没有可验证的异常假设",
        "tool_name": None, "hypotheses": [],
        "missing_evidence": ["缺少本次有效采样窗口"] if disposition == "INSUFFICIENT_EVIDENCE" else [],
        "limitations": ["仅判断用户描述或规划范围，不确认真实健康或根因"],
        "causal_root_cause_verified": False,
    }


def investigate():
    return {**output("INVESTIGATE"), "tool_name": "start_pyspy_profile",
            "hypotheses": [{**service.cpu_observation_plan("PYTHON"), "rationale": "先分别核验两类真实观察"}]}


@pytest.mark.parametrize("disposition", ["NORMAL", "INSUFFICIENT_EVIDENCE", "REFUSED"])
def test_noninvestigation_is_valid_without_probe_or_anomaly(disposition):
    proposal = output(disposition)
    assert validate_planning_output(proposal, []) == proposal
    assert proposal["hypotheses"] == [] and proposal["tool_name"] is None


@pytest.mark.parametrize("disposition", ["NORMAL", "INSUFFICIENT_EVIDENCE", "REFUSED"])
@pytest.mark.parametrize("forgery", ["tool", "hypothesis", "causal", "scope", "unknown-field"])
def test_no_probe_contract_rejects_hidden_action_or_unbounded_claim(disposition, forgery):
    proposal = output(disposition)
    if forgery == "tool":
        proposal["tool_name"] = "delete_database"
    elif forgery == "hypothesis":
        proposal["hypotheses"] = investigate()["hypotheses"]
    elif forgery == "causal":
        proposal["causal_root_cause_verified"] = True
    elif forgery == "scope":
        proposal["missing_evidence" if disposition == "INSUFFICIENT_EVIDENCE" else "limitations"] = []
    else:
        proposal["shell_command"] = "unapproved-action"
    with pytest.raises(ValueError):
        validate_planning_output(proposal, ["start_pyspy_profile"])


@pytest.mark.parametrize("forgery", ["empty", "counter-empty", "failed", "whitelist", "cpu-contract", "whitespace"])
def test_investigation_keeps_falsification_numeric_and_authorization_gates(forgery):
    proposal = investigate()
    if forgery == "empty":
        proposal["hypotheses"] = []
    elif forgery == "counter-empty":
        proposal["hypotheses"][0]["falsification_criteria"] = []
    elif forgery == "failed":
        proposal["hypotheses"][0]["falsification_criteria"] = ["Agent 离线导致采集失败"]
    elif forgery == "whitelist":
        proposal["tool_name"] = "start_jvm_profile"
    elif forgery == "cpu-contract":
        proposal["hypotheses"][0]["statement"] += "；这个函数导致了所有慢请求"
    else:
        proposal["hypotheses"][0]["expected_observations"] = ["   "]
    with pytest.raises(ValueError):
        validate_planning_output(proposal, ["start_pyspy_profile"])


@pytest.mark.parametrize("condition", [
    "同负载下 HTTP 请求不再超时且 p95 低于阈值",
    "HTTP requests complete without timeout under the same load",
    "Business requests no longer timed out in the same window",
])
def test_business_timeout_refutation_is_distinct_from_collector_timeout(condition):
    proposal = investigate()
    proposal["tool_name"] = "collect_network_diagnostics"
    proposal["hypotheses"] = [{"statement": "业务请求存在网络传输延迟", "rationale": "需要真实请求观测核验",
                               "expected_observations": ["同负载的请求超时率升高"],
                               "falsification_criteria": [condition]}]
    result = validate_planning_output(proposal, ["collect_network_diagnostics"])
    assert result["hypotheses"][0]["falsification_criteria"] == [condition]


@pytest.mark.parametrize("condition", ["采集超时", "工具超时", "远程采集连接失败", "collection timed out", "tool timeout"])
def test_actual_collection_failure_never_becomes_refutation(condition):
    proposal = investigate()
    proposal["hypotheses"][0]["falsification_criteria"] = [condition]
    with pytest.raises(ValueError, match="采集失败"):
        validate_planning_output(proposal, ["start_pyspy_profile"])


def test_legacy_probe_normalizes_to_v2_without_rewriting_criteria():
    old = investigate()
    for key in ["schema_version", "disposition", "missing_evidence", "limitations", "causal_root_cause_verified"]:
        old.pop(key)
    result = validate_planning_output(old, ["start_pyspy_profile"])
    assert result["disposition"] == "INVESTIGATE"
    assert result["hypotheses"][0]["falsification_criteria"] == old["hypotheses"][0]["falsification_criteria"]


def test_legacy_advertised_schema_allows_nullable_tool_and_empty_hypotheses():
    schema = planning_output_schema([])
    assert schema["properties"]["tool_name"]["anyOf"] == [{"type": "null"}]
    assert schema["properties"]["hypotheses"].get("minItems", 0) == 0
    assert set(schema["required"]) == set(PlanningOutput.model_fields)
    nested = schema["$defs"]["PlanningHypothesis"]
    assert set(nested["required"]) == set(nested["properties"])
    assert nested["properties"]["falsification_criteria"]["minItems"] == 1


def agent_context():
    return diagnosis_agent.DiagnosisAgentContext(
        diagnosis_id="test-planning-v2", query="只阅读当前已绑定服务的描述",
        target={}, category="UNKNOWN", rule_plan={}, allowed_tools=(),
    )


@pytest.mark.parametrize("disposition", ["NORMAL", "INSUFFICIENT_EVIDENCE", "REFUSED"])
@pytest.mark.parametrize("framework", ["legacy", "langgraph"])
def test_actual_runtime_returns_valid_no_probe_as_result_not_provider_failure(monkeypatch, disposition, framework):
    proposal = output(disposition)
    monkeypatch.setattr(adaptive_planner, "is_feature_enabled", lambda _: True)
    settings = AISettings("full", "test", "https://example.invalid", "test", "test", True, True, True)
    monkeypatch.setattr(adaptive_planner, "get_ai_settings", lambda: settings)
    monkeypatch.setenv("MINI_DROP_AGENT_FRAMEWORK", framework)
    captured = []
    if framework == "legacy":
        def transport(payload, timeout):
            captured.append(payload)
            return SimpleNamespace(status_code=200, json=lambda: {"choices": [{"message": {"tool_calls": [
                {"function": {"name": "emit_diagnosis_plan", "arguments": json.dumps(proposal)}}]}}]})
        monkeypatch.setattr(adaptive_planner, "chat_completions", transport)
    else:
        class FakeAgent:
            def invoke(self, *args, **kwargs):
                captured.append(kwargs)
                response = diagnosis_agent.finish_diagnosis_plan.func(
                    output=PlanningOutput.model_validate(proposal), runtime=SimpleNamespace(context=kwargs["context"]),
                )
                return {"messages": [
                    AIMessage(content="", tool_calls=[{"name": "finish_diagnosis_plan", "args": {"output": proposal},
                                                        "id": "real-tool-result", "type": "tool_call"}]),
                    ToolMessage(content=response, tool_call_id="real-tool-result", name="finish_diagnosis_plan"),
                ]}
        monkeypatch.setattr(diagnosis_agent, "_agent_for", lambda _: FakeAgent())
        monkeypatch.setattr(diagnosis_agent, "get_agent_runtime_status", lambda: {"actual_backend": "memory"})
    result = adaptive_planner.propose_hypothesis_plan(
        diagnosis_id="test-planning-v2", query="只阅读当前服务描述", target={}, category="UNKNOWN",
        rule_plan={}, allowed_tools=[], retrieval_trace={"scope": "public-only"}, user_preferences={},
    )
    assert len(captured) == 1
    assert result is not None and result["disposition"] == disposition
    assert result["tool_name"] is None and result["hypotheses"] == []
    assert result["causal_root_cause_verified"] is False
    if framework == "legacy":
        assert "mini-drop.planning-output.v2" in captured[0]["messages"][0]["content"]


@pytest.mark.parametrize("forgery", ["no-tool-result", "wrong-id", "different-output", "later-rejection", "retroactive-ai"])
def test_agent_completion_needs_matching_executed_tool_and_cannot_accept_unexecuted_prose(forgery):
    proposal = output()
    ai = AIMessage(content="", tool_calls=[{"name": "finish_diagnosis_plan", "args": {"output": proposal},
                                           "id": "accepted", "type": "tool_call"}])
    tool = ToolMessage(content=json.dumps({"accepted": True, "proposal": proposal}),
                       tool_call_id="accepted", name="finish_diagnosis_plan")
    messages = [ai, tool]
    if forgery == "no-tool-result":
        messages = [ai]
    elif forgery == "wrong-id":
        messages[1] = ToolMessage(content=tool.content, tool_call_id="other", name=tool.name)
    elif forgery == "different-output":
        messages[1] = ToolMessage(content=json.dumps({"accepted": True, "proposal": output("REFUSED")}),
                                  tool_call_id="accepted", name=tool.name)
    elif forgery == "later-rejection":
        messages.append(ToolMessage(content=json.dumps({"accepted": False}), tool_call_id="bad", name=tool.name))
    else:
        messages = [tool, ai]
    assert diagnosis_agent._accepted_tool_payload(messages) is None


@pytest.mark.parametrize("disposition", ["NORMAL", "INSUFFICIENT_EVIDENCE", "REFUSED"])
def test_english_no_probe_display_never_invents_chinese_rule_anomaly(disposition):
    proposal = output(disposition)
    proposal["reasoning_summary"] = "This planning context has no actionable anomaly."
    result = diagnosis_agent.normalize_diagnosis_plan_for_display(proposal, {"tool_name": "start_perf_profile"})
    assert result["hypotheses"] == [] and result["tool_name"] is None
    assert result["disposition"] == disposition
    assert result["language_normalization"] == "SERVER_DISPOSITION_LABEL"


@pytest.mark.parametrize("disposition", ["NORMAL", "INSUFFICIENT_EVIDENCE", "REFUSED"])
@pytest.mark.parametrize("phase", ["initial", "intervention", "feedback", "counter", "insufficient"])
def test_all_five_persisted_planner_callers_stop_before_creating_anomaly_or_probe(planning, disposition, phase):
    proposal = output(disposition)
    dispatches = planning("Python CPU 服务描述，无需声称根因", "PYTHON", proposal, skill_tool="start_pyspy_profile")
    original = deepcopy(proposal)
    with new_session() as db:
        if phase != "initial":
            db.add(DropInsightHypothesisModel(id="prior", diagnosis_id="diag", **{
                "statement": "上一轮的假设需要保留", "expected_observations_json": ["观察支持该方向"],
                "falsification_criteria_json": ["观察反驳该方向"], "status": "OPEN", "source": "MODEL",
                "round_index": 1, "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}))
        if phase == "intervention":
            db.add(DropInsightEventModel(id="turn", diagnosis_id="diag", sequence=1,
                event_type="diagnosis.intervention_submitted", actor="USER", occurred_at=datetime.now(timezone.utc),
                payload_json={"intervention_id": "owned-turn", "hypothesis_id": "prior", "action": "ADD_CONTEXT",
                              "message": "只描述范围", "round_index": 2}))
        db.commit()
    if phase == "initial":
        result = service.run_diagnosis_planner("diag", RunPlannerRequest())
        assert result["planning_disposition"] == disposition
    elif phase == "intervention":
        result = service._apply_diagnosis_intervention("diag", "turn")
        assert result["planning_output"]["disposition"] == disposition
        replay = service._apply_diagnosis_intervention("diag", "turn")
        assert replay["planning_output"] == result["planning_output"]
    elif phase == "feedback":
        feedback = SimpleNamespace(id="feedback-test", corrected_cause="只描述范围", feedback_note=None,
                                   hypothesis_id="prior", feedback_label="partial")
        assert service._replan_from_feedback("diag", feedback) is None
    elif phase == "counter":
        assert service._replan_from_counter_evidence("diag", "prior", "old-report") is None
    else:
        assert service._replan_after_insufficient_evidence("diag", "prior", "old-report") is None
    with new_session() as db:
        events = db.query(DropInsightEventModel).filter_by(event_type="planner.output_recorded").all()
        assert len(events) == 1 and events[0].payload_json["planning_disposition"] == disposition
        payload = events[0].payload_json
        assert payload["is_evidence"] is False and payload["health_check_performed"] is False
        assert payload["new_tool_requested"] is False and payload["causal_root_cause_verified"] is False
        assert db.get(DropInsightSessionModel, "diag").status == "INSUFFICIENT_EVIDENCE"
        assert db.query(DropInsightHypothesisModel).count() == (0 if phase == "initial" else 1)
        assert db.query(DropInsightToolCallModel).count() == db.query(DropInsightReportModel).count() == 0
        assert not db.query(DropInsightEventModel).filter_by(event_type="health_check.completed").all()
    assert dispatches == [] and proposal == original


@pytest.mark.parametrize("disposition", ["NORMAL", "INSUFFICIENT_EVIDENCE", "REFUSED"])
def test_unknown_authorized_target_can_return_no_probe_without_forcing_clarification(planning, disposition):
    dispatches = planning("只描述服务现在的情况", "PYTHON", output(disposition))
    with new_session() as db:
        db.get(DropInsightSessionModel, "diag").status = "UNDERSTANDING"
        db.commit()
    result = service.run_diagnosis_planner("diag", RunPlannerRequest())
    assert result["planning_disposition"] == disposition and dispatches == []
    with new_session() as db:
        assert db.query(DropInsightHypothesisModel).count() == 0
        assert not db.query(DropInsightEventModel).filter_by(event_type="planner.needs_clarification").all()


@pytest.mark.parametrize("proposal", [None, investigate()])
def test_unknown_with_failed_model_or_investigation_keeps_original_clarification_gate(planning, proposal):
    dispatches = planning("只描述服务现在的情况", "PYTHON", proposal)
    with new_session() as db:
        db.get(DropInsightSessionModel, "diag").status = "UNDERSTANDING"
        db.commit()
    result = service.run_diagnosis_planner("diag", RunPlannerRequest())
    assert result["status"] == "NEEDS_CLARIFICATION" and dispatches == []
    with new_session() as db:
        assert db.query(DropInsightHypothesisModel).count() == 0


@pytest.mark.parametrize("status", ["CANCELLED", "FAILED"])
def test_late_no_probe_never_reopens_cancelled_or_failed_session(planning, status):
    planning("Python CPU 信息", "PYTHON", output())
    with new_session() as db:
        db.get(DropInsightSessionModel, "diag").status = status
        db.commit()
    result = service._record_noninvestigation_plan("diag", output(), phase="INITIAL_PLAN", effect_key="late-output")
    assert result["status"] == status
    with new_session() as db:
        assert db.get(DropInsightSessionModel, "diag").status == status
        assert not db.query(DropInsightEventModel).filter_by(event_type="planner.output_recorded").all()


def test_no_probe_keeps_active_real_collection_and_its_reservation(planning):
    planning("Python CPU 信息", "PYTHON", output())
    with new_session() as db:
        db.add(DropInsightToolCallModel(id="active", diagnosis_id="diag", tool_name="collect_sys_metrics",
            arguments_json={}, policy_decision="ALLOW", policy_checks_json=[], policy_reason="Already admitted",
            requested_by="system", status="RUNNING", budget_reservation_status="RESERVED",
            created_at=datetime.now(timezone.utc)))
        db.commit()
    result = service._record_noninvestigation_plan("diag", output(), phase="FEEDBACK_REPLAN", effect_key="stop-new")
    assert result["status"] == "COLLECTING_EVIDENCE"
    with new_session() as db:
        call = db.get(DropInsightToolCallModel, "active")
        assert call.status == "RUNNING" and call.budget_reservation_status == "RESERVED"
        assert db.query(DropInsightHypothesisModel).count() == 0
        event = db.query(DropInsightEventModel).filter_by(event_type="planner.output_recorded").one()
        assert event.payload_json["finalization"]["reason"] == "ACTIVE_TOOL_CALLS"


def test_no_probe_keeps_best_accepted_report_and_does_not_write_model_root_cause(planning):
    planning("Python CPU 信息", "PYTHON", output())
    now = datetime.now(timezone.utc)
    with new_session() as db:
        db.add(DropInsightHypothesisModel(id="supported", diagnosis_id="diag", statement="已由当前采集支持的方向",
            expected_observations_json=["支持观察"], falsification_criteria_json=["相反观察"], status="SUPPORTED",
            source="MODEL", round_index=1, created_at=now, updated_at=now))
        db.add(DropInsightReportModel(id="original-report", diagnosis_id="diag", hypothesis_id="supported",
            conclusion="已有独立证据支持的有限结论", confidence=900,
            evidence_refs_json=["actual-support"], counter_evidence_refs_json=["actual-control"],
            assumptions_json=[], limitations_json=["仅现有证据范围"], next_actions_json=[], claims_json=[],
            verification_json={"status": "VERIFIED", "coverage_ratio": 1.0}, created_at=now))
        db.commit()
    result = service._record_noninvestigation_plan("diag", output(), phase="COUNTER_EVIDENCE_REPLAN",
                                                 effect_key="preserve-real-report")
    assert result["status"] == "COMPLETED"
    with new_session() as db:
        assert db.query(DropInsightReportModel).count() == 1
        report = db.get(DropInsightReportModel, "original-report")
        assert report.conclusion == "已有独立证据支持的有限结论"
        assert report.limitations_json == ["仅现有证据范围"]
        assert db.query(DropInsightToolCallModel).count() == 0
        event = db.query(DropInsightEventModel).filter_by(event_type="planner.output_recorded").one()
        assert event.payload_json["finalization"]["best_report_id"] == "original-report"


def test_real_health_check_entry_bypasses_model_disposition_and_keeps_original_check_contract(planning, monkeypatch):
    dispatches = planning("检查当前服务资源", "PYTHON", output("REFUSED"))
    monkeypatch.setattr(service, "propose_hypothesis_plan", lambda **_: pytest.fail("health check must bypass the model"))
    with new_session() as db:
        db.add(DropInsightEventModel(id="health-entry", diagnosis_id="diag", sequence=1,
            event_type="diagnosis.created", actor="USER", occurred_at=datetime.now(timezone.utc),
            payload_json={"health_check": True}))
        db.commit()
    result = service.run_diagnosis_planner("diag", RunPlannerRequest())
    assert result["tool_call"]["tool_name"] == "collect_sys_metrics"
    assert len(dispatches) == 1
    with new_session() as db:
        assert not db.query(DropInsightEventModel).filter_by(event_type="planner.output_recorded").all()


def test_model_unavailable_for_known_anomaly_keeps_audited_registered_rule_fallback(planning):
    dispatches = planning("Python CPU 持续升高", "PYTHON", None)
    result = service.run_diagnosis_planner("diag", RunPlannerRequest())
    assert result["planner_kind"] == "DETERMINISTIC_RULES"
    assert "模型调用不可用" in result["reasoning_summary"]
    assert result["hypothesis"]["source"] == "REGISTERED_OBSERVATION_RULE"
    assert len(dispatches) == 1
    with new_session() as db:
        assert not db.query(DropInsightEventModel).filter_by(event_type="planner.output_recorded").all()


def test_retrieval_admission_uses_user_text_without_server_category_to_create_relevance(planning, monkeypatch):
    planning("月球潮汐对服务有什么影响", "PYTHON", output())
    captured = []
    monkeypatch.setattr(service, "build_retrieval_trace", lambda query, **kwargs: captured.append((query, kwargs)) or {})
    REAL_KNOWLEDGE_RETRIEVAL(
        "diag", query="月球潮汐对服务有什么影响", category="CPU_HOTSPOT", user_correction="暂时无指标",
        phase="test", effect_key="relevance-is-user-only",
    )
    assert captured == [("月球潮汐对服务有什么影响\nCPU_HOTSPOT\n暂时无指标",
                          {"relevance_query": "月球潮汐对服务有什么影响\n暂时无指标"})]

def test_server_information_contract_persists_model_zero_provenance_without_health(planning):
    from server.app.agent_runtime.planning_request import informational_planning_output
    query = "现在没有观察到异常，只描述当前信息所覆盖的范围；不声称全局健康，不提出异常假设或采集动作。"
    planning(query, "PYTHON", None)
    proposal = informational_planning_output(query, target={"pid": 123, "runtime": "PYTHON"})
    assert proposal is not None
    result = service._record_noninvestigation_plan("diag", proposal, phase="INITIAL_PLAN", effect_key="info-only")
    assert result["planning_disposition"] == "NORMAL"
    assert result["planner_kind"] == "SERVER_REQUEST_INTENT" and result["model_invocations"] == 0
    with new_session() as db:
        event = db.query(DropInsightEventModel).filter_by(event_type="planner.output_recorded").one()
        payload = event.payload_json
        assert payload["planner_kind"] == "SERVER_REQUEST_INTENT" and payload["model_invocations"] == 0
        assert payload["planning_request_intent"]["intent"] == "INFORMATION_ONLY"
        assert payload["health_check_performed"] is False and payload["is_evidence"] is False
        assert db.query(DropInsightHypothesisModel).count() == db.query(DropInsightToolCallModel).count() == 0
        assert not db.query(DropInsightEventModel).filter_by(event_type="health_check.completed").all()


@pytest.mark.parametrize("model_count", [True, 1])
def test_server_information_contract_cannot_forge_model_zero_metadata(planning, model_count):
    from server.app.agent_runtime.planning_request import informational_planning_output
    query = "只描述已有信息，不采集，不做健康判断。"
    planning(query, "PYTHON", None)
    proposal = informational_planning_output(query)
    assert proposal is not None
    proposal["model_invocations"] = model_count
    with pytest.raises(ValueError):
        service._record_noninvestigation_plan("diag", proposal, phase="INITIAL_PLAN", effect_key="forged-info-only")
    with new_session() as db:
        assert not db.query(DropInsightEventModel).filter_by(event_type="planner.output_recorded").all()
