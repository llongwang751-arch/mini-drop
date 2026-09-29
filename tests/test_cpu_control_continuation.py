from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight import service
from server.app.models import (AgentModel, DropInsightEvidenceModel, DropInsightHypothesisModel,
                               DropInsightReportModel, DropInsightSessionModel, DropInsightToolCallModel)


@pytest.fixture
def seeded(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    now = datetime.now(timezone.utc)
    contract = service.cpu_observation_plan("PYTHON")
    binding = SimpleNamespace(agent_id="agent", pid=123)
    monkeypatch.setattr(service, "_current_target_binding", lambda diagnosis: binding)
    monkeypatch.setattr(service, "_validated_target_binding", lambda *args, **kwargs: binding)
    monkeypatch.setattr(service, "_planner_tool_arguments", lambda *args, **kwargs:
                        {"agent_id": "agent", "pid": 123, "duration_seconds": 15})
    def dispatch_transport(call_id):
        with new_session() as db:
            return db.get(DropInsightToolCallModel, call_id)
    monkeypatch.setattr(service, "_execute_approved_tool_call", dispatch_transport)
    with new_session() as db:
        db.add(AgentModel(id="agent", hostname="fixture", ip_addr="127.0.0.1",
                         capabilities=["sys_metrics", "pyspy"], status="ONLINE",
                         created_at=now, updated_at=now, last_heartbeat_at=now))
        db.add(DropInsightSessionModel(id="diag", query="Python CPU high", target_json={},
            mode="OBSERVE_ONLY", status="COLLECTING_EVIDENCE", budget_json={"max_risk_level": "R2"},
            time_range_json={}, requested_time_range_json={}, effective_time_range_json={},
            clarification_questions_json=[], version=1, created_at=now, updated_at=now))
        db.add(DropInsightHypothesisModel(id="hyp", diagnosis_id="diag", statement=contract["statement"],
            expected_observations_json=contract["expected_observations"],
            falsification_criteria_json=contract["falsification_criteria"],
            status="OPEN", source="MODEL", round_index=1, created_at=now, updated_at=now))
        db.add(DropInsightEvidenceModel(id="support", diagnosis_id="diag", hypothesis_id="hyp",
            role="SUPPORT", envelope_json={"source": {"tool_name": "pyspy"}}, classification_json={"decision": "ACCEPT_SUPPORT",
            "can_support_conclusion": True}, created_at=now))
        db.commit()
    yield now
    reset_engine()


def call():
    return service._request_cpu_control_before_report("diag", "hyp")


def test_same_hypothesis_immutable_criteria_and_real_policy_approved(seeded):
    result = call()
    assert result.hypothesis_id == "hyp"
    assert result.tool_name == "collect_sys_metrics"
    assert result.status == "APPROVED"
    assert result.budget_reservation_status == "RESERVED"
    assert result.arguments_json == {"agent_id": "agent", "pid": 123, "duration_seconds": 15}
    with new_session() as db:
        h = db.get(DropInsightHypothesisModel, "hyp")
        assert h.falsification_criteria_json == service.cpu_observation_plan("PYTHON")["falsification_criteria"]
        assert h.round_index == 1
        assert db.query(DropInsightHypothesisModel).count() == 1
        assert db.query(DropInsightReportModel).count() == 0


def test_repeated_pending_requests_reuse_one_effect_and_one_reservation(seeded):
    first = call()
    second = call()
    assert first.id == second.id
    with new_session() as db:
        assert db.query(DropInsightToolCallModel).count() == 1


@pytest.mark.parametrize("criteria", [[], ["CPU low"], ["IO wait above 50%"],
    ["Target process CPU below 50% and syscall waits dominate"],
    ["Target process CPU below 50%", "source_hot_function absent"],
    ["Target process CPU below 0%"]])
def test_vague_mixed_or_missing_criteria_do_not_dispatch(seeded, criteria):
    with new_session() as db:
        db.get(DropInsightHypothesisModel, "hyp").falsification_criteria_json = criteria
        db.commit()
    assert call() is None
    with new_session() as db:
        assert db.query(DropInsightToolCallModel).count() == 0


@pytest.mark.parametrize("change", ["missing", "limited", "false", "neutral", "wrong_hypothesis", "other_diagnosis"])
def test_only_persisted_accepted_same_hypothesis_support_counts(seeded, change):
    with new_session() as db:
        row = db.get(DropInsightEvidenceModel, "support")
        if change == "missing": db.delete(row)
        elif change == "limited": row.classification_json = {"decision": "ACCEPT_LIMITED", "can_support_conclusion": True}
        elif change == "false": row.classification_json = {"decision": "ACCEPT_SUPPORT", "can_support_conclusion": False}
        elif change == "neutral": row.role = "NEUTRAL"
        elif change == "wrong_hypothesis": row.hypothesis_id = "other"
        elif change == "other_diagnosis": row.diagnosis_id = "other"
        db.commit()
    assert call() is None


@pytest.mark.parametrize("role", ["COUNTER", "CONTROL"])
def test_existing_accepted_control_or_counter_does_not_probe_again(seeded, role):
    with new_session() as db:
        db.add(DropInsightEvidenceModel(id="control", diagnosis_id="diag", hypothesis_id="hyp", role=role,
            envelope_json={}, classification_json={"can_support_conclusion": True}, created_at=seeded))
        db.commit()
    assert call() is None


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "DENIED", "REJECTED", "COMPLETED"])
def test_terminal_attempt_not_retried(seeded, status):
    first = call()
    with new_session() as db:
        row = db.get(DropInsightToolCallModel, first.id)
        row.status = status
        db.commit()
    second = call()
    assert second.id == first.id and second.status == status
    with new_session() as db:
        assert db.query(DropInsightToolCallModel).count() == 1


def test_sibling_system_probe_does_not_steal_original_hypothesis_control(seeded):
    with new_session() as db:
        db.add(DropInsightToolCallModel(id="sibling-call", diagnosis_id="diag", hypothesis_id="sibling",
            tool_name="collect_sys_metrics", arguments_json={}, policy_decision="ALLOW", policy_reason="prior",
            status="COMPLETED", requested_by="fixture", created_at=seeded))
        db.commit()
    assert call().hypothesis_id == "hyp"


@pytest.mark.parametrize("change", ["offline", "no_capability"])
def test_missing_agent_capability_never_dispatches(seeded, change):
    with new_session() as db:
        agent = db.get(AgentModel, "agent")
        if change == "offline": agent.status = "OFFLINE"
        else: agent.capabilities = ["pyspy"]
        db.commit()
    assert call() is None


@pytest.mark.parametrize("budget", [{"max_tool_calls": 0}, {"max_duration_seconds": 1},
    {"max_artifact_bytes": 1}, {"max_concurrent_tasks": 0}, {"max_risk_level": "R0"}])
def test_actual_policy_and_resource_budgets_still_deny(seeded, budget):
    with new_session() as db:
        db.get(DropInsightSessionModel, "diag").budget_json = {"max_risk_level": "R2", **budget}
        db.commit()
    result = call()
    assert result.status == "DENIED"
    assert result.budget_reservation_status == "NONE"
    assert call().id == result.id


def test_elapsed_session_deadline_denies_without_reservation(seeded):
    with new_session() as db:
        db.get(DropInsightSessionModel, "diag").created_at = seeded - timedelta(seconds=400)
        db.get(DropInsightSessionModel, "diag").mode = "AUTONOMOUS"
        db.commit()
    assert call().status == "DENIED"


def test_existing_immutable_partial_report_never_gets_extra_probe(seeded):
    with new_session() as db:
        db.add(DropInsightReportModel(id="old-report", diagnosis_id="diag", hypothesis_id="hyp",
            conclusion="historical partial", confidence=600, evidence_refs_json=["support"],
            verification_json={"status": "PARTIAL_WITHOUT_COUNTER"}, created_at=seeded))
        db.commit()
    assert call() is None
    with new_session() as db:
        assert db.query(DropInsightToolCallModel).count() == 0
        assert db.get(DropInsightReportModel, "old-report").conclusion == "historical partial"


@pytest.mark.parametrize("change", ["other_claim", "other_source"])
def test_unrelated_claim_or_nonprofile_support_does_not_trigger_cpu_control(seeded, change):
    with new_session() as db:
        if change == "other_claim":
            db.get(DropInsightHypothesisModel, "hyp").statement = "Database waiting is dominant"
        else:
            db.get(DropInsightEvidenceModel, "support").envelope_json = {"source": {"tool_name": "memory_smaps"}}
        db.commit()
    assert call() is None


@pytest.mark.parametrize("query", ["Python CPU 占用率持续升高", "Go high CPU", "CPU utilization is high"])
def test_explicit_cpu_query_rule_fallback_declares_threshold_before_sampling(query):
    plan = {"category": "PYTHON_RUNTIME", "statement": "Python runtime issue",
            "expected": ["profile identifies function"], "falsification": ["uniform stack"]}
    bound = service._cpu_rule_plan_for_query(plan, query)
    contract = service.cpu_observation_plan("PYTHON")
    assert bound["falsification"] == contract["falsification_criteria"]
    assert bound["statement"] == contract["statement"]
    assert bound["expected"] == contract["expected_observations"]
    assert plan["falsification"] == ["uniform stack"]


@pytest.mark.parametrize("category,query", [("PYTHON_RUNTIME", "Python GIL contention"),
    ("GO_RUNTIME", "Go source function sampling"), ("DATABASE_LOCK", "CPU high during lock waits"),
    ("CPU_HOTSPOT", "inspect flamegraph")])
def test_rules_do_not_invent_cpu_high_for_other_symptoms(category, query):
    plan = {"category": category, "statement": "original", "falsification": ["original"]}
    assert service._cpu_rule_plan_for_query(plan, query) == plan
