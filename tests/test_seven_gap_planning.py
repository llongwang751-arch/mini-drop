"""Persisted planner routes under hostile model priors, without remote probes.

The planner, hypothesis validation and LATS event persistence run normally.
Only model/knowledge transports, process discovery and task dispatch are
substituted; these tests are not real-world diagnostic acceptance.
"""
from copy import deepcopy

import pytest

from tests.test_cpu_control_continuation import seeded
from server.app.database import new_session
from server.app.drop_insight import service
from server.app.drop_insight.schemas import RunPlannerRequest
from server.app.models import (
    AgentModel, DropInsightEvidenceModel, DropInsightEventModel,
    DropInsightHypothesisModel, DropInsightSessionModel, DropInsightToolCallModel,
)
from server.app.process_attestation import ProcessIdentityBinding

REAL_TOOL_ARGUMENTS = service._planner_tool_arguments


@pytest.fixture
def planning(seeded, monkeypatch):
    monkeypatch.setattr(service, "_apply_active_planner_skill", lambda *a, **k: None)
    monkeypatch.setattr(service, "_record_planner_knowledge_retrieval", lambda *a, **k: {})
    monkeypatch.setattr(service, "_successful_tool_route_priors", lambda: [])
    monkeypatch.setattr(service, "_planner_tool_arguments", REAL_TOOL_ARGUMENTS)
    dispatches = []

    def dispatch(diagnosis_id, payload, **kwargs):
        dispatches.append(payload.model_dump())
        with new_session() as db:
            row = DropInsightToolCallModel(id=f"test-call-{len(dispatches)}", diagnosis_id=diagnosis_id,
                hypothesis_id=payload.hypothesis_id, tool_name=payload.tool_name,
                arguments_json=payload.arguments, policy_decision="REQUIRE_APPROVAL",
                policy_checks_json=[], policy_reason="Isolated test transport",
                requested_by="test", status="PENDING_APPROVAL", created_at=seeded,
                effect_key=kwargs.get("effect_key"))
            db.add(row)
            db.commit()
            db.expunge(row)
            return row
    monkeypatch.setattr(service, "request_tool_call", dispatch)

    def configure(query, runtime, proposal=None, *, selection_policy="UCT", skill_tool=None):
        executable = {"PYTHON": "/usr/local/bin/python3", "CPP": "/usr/local/bin/cpp-hotspot", "JAVA": "/opt/java/bin/java"}[runtime]
        binding = ProcessIdentityBinding("agent", 123, "test-boot", 42, 99, 123, executable,
                                         "test-snapshot", 1, seeded)
        tools = {"PYTHON": ["start_pyspy_profile", "collect_sys_metrics", "start_perf_profile", "start_ebpf_io_profile"],
                 "CPP": ["start_perf_profile", "collect_sys_metrics", "start_ebpf_io_profile"],
                 "JAVA": ["start_jvm_profile", "collect_sys_metrics", "start_ebpf_io_profile"]}[runtime]
        monkeypatch.setattr(service, "_current_target_binding", lambda *a: binding)
        monkeypatch.setattr(service, "_validated_target_binding", lambda *a, **k: binding)
        monkeypatch.setattr(service, "_available_planner_tools", lambda *a: list(tools))
        monkeypatch.setattr(service, "propose_hypothesis_plan", lambda **kwargs: deepcopy(proposal))
        if skill_tool:
            monkeypatch.setattr(service, "_apply_active_planner_skill", lambda *a, **k:
                {"applied": True, "selected_tool": skill_tool, "first_tool_name": skill_tool,
                 "route": [{"tool_name": skill_tool}]})
        with new_session() as db:
            db.query(DropInsightEvidenceModel).delete()
            db.query(DropInsightHypothesisModel).delete()
            diagnosis = db.get(DropInsightSessionModel, "diag")
            diagnosis.query = query
            diagnosis.target_json = {"agent_id": "agent", "pid": 123, "process_binding": binding.to_dict()}
            diagnosis.budget_json = {"lats_top_k": 3, "lats_selection_policy": selection_policy,
                                     "max_risk_level": "R2", "max_duration_seconds": 300}
            db.get(AgentModel, "agent").capabilities = ["sys_metrics", "pyspy", "perf_cpu", "continuous_perf", "java_async", "ebpf_io"]
            db.commit()
        return dispatches
    return configure


def _invalid_cpu_proposal(runtime):
    plan = service.cpu_observation_plan(runtime)
    plan["statement"] += "; this function caused every slow request"
    plan["expected_observations"] += ["Every slow request proves exactly this causal chain"]
    plan.update(prior_probability=1.0, estimated_value=1.0)
    return {"tool_name": "start_perf_profile" if runtime == "CPP" else "start_pyspy_profile",
            "reasoning_summary": "An intentionally unsupported model claim", "hypotheses": [plan]}


@pytest.mark.parametrize("runtime", ["PYTHON", "CPP"])
@pytest.mark.parametrize("policy", ["UCT", "PUCT"])
def test_model_strong_claim_is_rejected_original_preserved_and_distinct_rule_plan_persisted(planning, runtime, policy):
    proposal = _invalid_cpu_proposal(runtime)
    before = deepcopy(proposal)
    planning(f"{runtime} CPU 持续升高，定位 CPU 热点", runtime, proposal, selection_policy=policy)
    result = service.run_diagnosis_planner("diag", RunPlannerRequest())
    canonical = service.cpu_observation_plan(runtime)
    selected = result["hypothesis"]
    assert selected["statement"] == canonical["statement"]
    assert selected["expected_observations"] == canonical["expected_observations"]
    assert selected["source"] == "REGISTERED_OBSERVATION_RULE"
    assert result["decision_source"] == "REGISTERED_OBSERVATION_RULE"
    assert proposal == before
    with new_session() as db:
        rejected = db.query(DropInsightEventModel).filter_by(event_type="planner.cpu_contract_rejected").one()
        assert rejected.payload_json["candidate"]["statement"] == before["hypotheses"][0]["statement"]
        assert rejected.payload_json["candidate"]["expected_observations"] == before["hypotheses"][0]["expected_observations"]
        assert rejected.payload_json["criteria_rewritten"] is False
        assert rejected.payload_json["dispatched"] is False
        assert not db.query(DropInsightHypothesisModel).filter_by(statement=before["hypotheses"][0]["statement"]).all()
        events = db.query(DropInsightEventModel).filter_by(event_type="hypothesis.created").all()
        registered = next(event for event in events if event.payload_json.get("hypothesis_id") == selected["hypothesis_id"])
        assert registered.payload_json["observation_contract"]["runtime"] == runtime
    again = service.run_diagnosis_planner("diag", RunPlannerRequest())
    assert again["planner_kind"] == "IDEMPOTENT_REPLAY"
    assert again["hypothesis"]["hypothesis_id"] == selected["hypothesis_id"]


def test_cpp_registered_profile_requests_one_independent_os_probe_for_same_persisted_hypothesis(planning):
    dispatches = planning("C++ CPU 持续升高，定位 CPU 热点", "CPP", _invalid_cpu_proposal("CPP"))
    result = service.run_diagnosis_planner("diag", RunPlannerRequest())
    hid = result["hypothesis"]["hypothesis_id"]
    with new_session() as db:
        db.add(DropInsightEvidenceModel(id="cpp-supported-profile", diagnosis_id="diag", hypothesis_id=hid,
            role="SUPPORT", envelope_json={"source": {"tool_name": "perf_cpu"}},
            classification_json={"decision": "ACCEPT_SUPPORT", "can_support_conclusion": True},
            created_at=db.get(DropInsightSessionModel, "diag").created_at))
        db.commit()
    first = service._request_cpu_control_before_report("diag", hid)
    second = service._request_cpu_control_before_report("diag", hid)
    assert first.id == second.id and first.hypothesis_id == hid
    assert first.tool_name == "collect_sys_metrics"
    assert [call["tool_name"] for call in dispatches] == ["start_perf_profile", "collect_sys_metrics"]
    with new_session() as db:
        original = db.get(DropInsightHypothesisModel, hid)
        assert original.falsification_criteria_json == service.cpu_observation_plan("CPP")["falsification_criteria"]


@pytest.mark.parametrize("query,runtime,category", [
    ("C++ mutex 锁竞争使请求等待", "CPP", "LOCK_CONTENTION"),
    ("Python fsync 同步写 IO 耗时升高", "PYTHON", "IO_LATENCY"),
    ("Java fsync 同步写 IO 耗时升高", "JAVA", "IO_LATENCY"),
    ("C++ fsync 同步写 IO 耗时升高", "CPP", "IO_LATENCY"),
    ("Python 噪声邻居 CPU 配额共享时资源争抢", "PYTHON", "NOISY_NEIGHBOR"),
])
def test_measured_domains_collect_target_system_metrics_before_optional_deep_tools(planning, query, runtime, category):
    planning(query, runtime)
    result = service.run_diagnosis_planner("diag", RunPlannerRequest())
    assert result["category"] == category
    assert result["tool_call"]["tool_name"] == "collect_sys_metrics"
    assert result["hypothesis"]["source"] == "REGISTERED_OBSERVATION_RULE"


def test_java_lock_keeps_async_lock_event_and_does_not_impose_cpp_application_counter_contract(planning):
    planning("Java mutex 锁竞争使请求等待", "JAVA")
    result = service.run_diagnosis_planner("diag", RunPlannerRequest())
    assert result["category"] == "LOCK_CONTENTION"
    assert result["tool_call"]["tool_name"] == "start_jvm_profile"
    assert result["tool_call"]["arguments"]["event"] == "lock"
    assert not any("lock_contention.average_wait_ms" in x for x in result["hypothesis"]["expected_observations"])


@pytest.mark.parametrize("policy", ["UCT", "PUCT"])
def test_foreign_numeric_domain_with_high_model_value_does_not_replace_io_observation(planning, policy):
    proposal = {"tool_name": "start_ebpf_io_profile", "hypotheses": [{
        "statement": "RSS 增长表明内存异常", "expected_observations": ["memory_growth.rss_delta_mb >= 8"],
        "falsification_criteria": ["memory_growth.rss_delta_mb < 8"],
        "prior_probability": 1.0, "estimated_value": 1.0}]}
    planning("C++ fsync 同步写 IO 耗时升高", "CPP", proposal, selection_policy=policy)
    result = service.run_diagnosis_planner("diag", RunPlannerRequest())
    assert result["hypothesis"]["source"] == "REGISTERED_OBSERVATION_RULE"
    assert result["tool_call"]["tool_name"] == "collect_sys_metrics"
    with new_session() as db:
        rejected = db.query(DropInsightEventModel).filter_by(event_type="planner.observation_contract_rejected").all()
        assert any(event.payload_json["candidate"]["statement"] == proposal["hypotheses"][0]["statement"] for event in rejected)


def test_puct_high_value_unknown_cannot_spend_first_probe_budget_ahead_of_registered_observation(planning):
    proposal = {"tool_name": "start_perf_profile", "hypotheses": [{
        "statement": "其他未知原因（OTHER/UNKNOWN）", "expected_observations": ["Unknown explanation"],
        "falsification_criteria": ["Known explanation"], "prior_probability": 1.0, "estimated_value": 1.0}]}
    planning("C++ CPU 持续升高，定位 CPU 热点", "CPP", proposal, selection_policy="PUCT")
    result = service.run_diagnosis_planner("diag", RunPlannerRequest())
    assert result["hypothesis"]["source"] == "REGISTERED_OBSERVATION_RULE"


@pytest.mark.parametrize("query,skill_tool", [("C++ mutex 锁竞争使请求等待", "start_perf_profile"),
                                               ("C++ fsync 同步写 IO 耗时升高", "start_ebpf_io_profile")])
def test_skill_deep_probe_does_not_discard_first_registered_target_counter_window(planning, query, skill_tool):
    planning(query, "CPP", skill_tool=skill_tool)
    result = service.run_diagnosis_planner("diag", RunPlannerRequest())
    assert result["tool_call"]["tool_name"] == "collect_sys_metrics"
