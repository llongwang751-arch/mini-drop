"""New CPU plans use the same executable contract at persistence and dispatch."""
from copy import deepcopy

import pytest

from tests.test_cpu_control_continuation import seeded
from server.app.database import new_session
from server.app.drop_insight import service
from server.app.drop_insight.schemas import CreateHypothesisRequest
from server.app.models import DropInsightHypothesisModel, DropInsightToolCallModel, DropInsightEventModel

REAL_PLANNER_TOOL_ARGUMENTS = service._planner_tool_arguments


def payload(plan):
    return CreateHypothesisRequest(**plan)


@pytest.mark.parametrize("runtime", ["PYTHON", "GO"])
def test_registry_plan_is_persisted_verbatim_with_declared_rule_source(seeded, runtime):
    plan = service.cpu_observation_plan(runtime)
    before = deepcopy(plan)
    row = service.create_hypothesis("diag", payload(plan), source="DETERMINISTIC_RULE",
                                    generation_reason="Bounded sequential-window observation, not cause attribution")
    assert row.statement == before["statement"]
    assert row.expected_observations_json == before["expected_observations"]
    assert row.falsification_criteria_json == before["falsification_criteria"]
    assert row.source == "DETERMINISTIC_RULE" and plan == before
    with new_session() as db:
        event = db.query(DropInsightEventModel).filter_by(event_type="hypothesis.created").one()
        recorded = event.payload_json["observation_contract"]
        assert recorded["runtime"] == runtime and recorded["cpu_threshold"] == 50
        assert recorded["scope"] == "SEPARATELY_COLLECTED_OBSERVATIONS_NOT_CAUSATION"


@pytest.mark.parametrize("source", ["MODEL", "DETERMINISTIC_RULE", "SYSTEM_FALLBACK", "USER"])
@pytest.mark.parametrize("changed", ["expected_observations", "falsification_criteria", "statement"])
def test_unsupported_strong_or_mixed_claim_rejected_before_insert_for_all_sources(seeded, source, changed):
    plan = service.cpu_observation_plan("PYTHON")
    if changed == "statement":
        plan[changed] += "；这个函数解释所有业务延迟并证明修复有效"
    else:
        plan[changed] += ["Python source function remains stable across all windows"]
    before = deepcopy(plan)
    with pytest.raises(ValueError, match="CPU_OBSERVATION_CONTRACT_UNSUPPORTED"):
        service.create_hypothesis("diag", payload(plan), source=source)
    with new_session() as db:
        assert db.query(DropInsightHypothesisModel).count() == 1
        assert db.query(DropInsightToolCallModel).count() == 0
    assert plan == before


def test_existing_idempotent_hypothesis_never_rewritten_or_revalidated_into_new_contract(seeded):
    with new_session() as db:
        original = db.get(DropInsightHypothesisModel, "hyp")
        original.effect_key = "already-created"
        original.statement = "Historical Python CPU high claim"
        original.expected_observations_json = ["Historical stronger expected condition"]
        original.falsification_criteria_json = ["Historical generic distribution criterion"]
        db.commit()
    requested = service.cpu_observation_plan("PYTHON")
    existing = service.create_hypothesis("diag", payload(requested), source="MODEL", effect_key="already-created")
    assert existing.id == "hyp"
    assert existing.statement == "Historical Python CPU high claim"
    assert existing.expected_observations_json == ["Historical stronger expected condition"]
    assert existing.falsification_criteria_json == ["Historical generic distribution criterion"]
    assert service._request_cpu_control_before_report("diag", "hyp") is None


@pytest.mark.parametrize("changed", ["expected_observations_json", "falsification_criteria_json"])
def test_legacy_unexecutable_mixed_hypothesis_does_not_get_partial_dispatch(seeded, changed):
    with new_session() as db:
        row = db.get(DropInsightHypothesisModel, "hyp")
        setattr(row, changed, list(getattr(row, changed)) + ["Python profile must prove cross-window uniformity"])
        db.commit()
    assert service._request_cpu_control_before_report("diag", "hyp") is None


def test_bound_runtime_takes_precedence_for_explicit_rule_observation(seeded):
    original = {"category": "CPU_HOTSPOT", "statement": "original", "expected": ["original"], "falsification": ["original"]}
    changed = service._cpu_rule_plan_for_query(original, "CPU high", runtime="GO")
    expected = service.cpu_observation_plan("GO")
    assert changed["statement"] == expected["statement"]
    assert changed["expected"] == expected["expected_observations"]
    assert original["statement"] == "original"


def test_generic_gil_hypothesis_is_not_silently_replaced_by_cpu_observation(seeded):
    plan = {"statement": "Python GIL lock contention", "expected_observations": ["Lock waiting stacks"],
            "falsification_criteria": ["No observed lock waits"]}
    row = service.create_hypothesis("diag", payload(plan), source="MODEL")
    assert row.statement == plan["statement"]
    assert row.falsification_criteria_json == plan["falsification_criteria"]

def invalid_proposal():
    plan = service.cpu_observation_plan("PYTHON")
    plan["falsification_criteria"] += ["Python samples prove uniformity across all windows"]
    plan["statement"] += "；这个函数解释全部业务延迟"
    return {"tool_name": "start_pyspy_profile", "reasoning_summary": "Unsupported model proposal",
            "hypotheses": [plan]}


def mock_planning_boundary(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "_available_planner_tools", lambda *a: ["start_pyspy_profile", "collect_sys_metrics"])
    monkeypatch.setattr(service, "_apply_active_planner_skill", lambda *a, **k: None)
    monkeypatch.setattr(service, "_record_planner_knowledge_retrieval", lambda *a, **k: {})
    monkeypatch.setattr(service, "_successful_tool_route_priors", lambda: [])
    monkeypatch.setattr(service, "_deterministic_exploration_candidates", lambda *a, **k: [])
    def proposal(**kwargs):
        calls.append(kwargs)
        return invalid_proposal()
    monkeypatch.setattr(service, "propose_hypothesis_plan", proposal)
    return calls


def test_initial_unsupported_model_preserves_original_and_uses_distinct_registered_rule_once(seeded, monkeypatch):
    from server.app.models import DropInsightEvidenceModel, DropInsightSessionModel
    from server.app.drop_insight.schemas import RunPlannerRequest
    from server.app.process_attestation import ProcessIdentityBinding
    attempts = mock_planning_boundary(monkeypatch)
    binding = ProcessIdentityBinding("agent", 123, "fixture-boot", 42, 99, 123,
        "/usr/local/bin/python3", "fixture-snapshot", 1, seeded)
    monkeypatch.setattr(service, "_validated_target_binding", lambda *args, **kwargs: binding)
    monkeypatch.setattr(service, "_current_target_binding", lambda *args: binding)
    monkeypatch.setattr(service, "_planner_tool_arguments", REAL_PLANNER_TOOL_ARGUMENTS)
    with new_session() as db:
        db.query(DropInsightEvidenceModel).delete()
        db.query(DropInsightHypothesisModel).delete()
        db.get(DropInsightSessionModel, "diag").target_json = {
            "agent_id": "agent", "pid": 123, "process_binding": binding.to_dict()}
        db.commit()
    first = service.run_diagnosis_planner("diag", RunPlannerRequest())
    expected = service.cpu_observation_plan("PYTHON")
    assert first["hypothesis"]["statement"] == expected["statement"]
    assert first["hypothesis"]["expected_observations"] == expected["expected_observations"]
    assert first["hypothesis"]["falsification_criteria"] == expected["falsification_criteria"]
    assert first["hypothesis"]["source"] == "REGISTERED_OBSERVATION_RULE"
    assert first["decision_source"] == "REGISTERED_OBSERVATION_RULE"
    assert first["candidate_generation_source"] == "MODEL"
    assert first["tool_call"]["tool_name"] == "start_pyspy_profile"
    second = service.run_diagnosis_planner("diag", RunPlannerRequest())
    assert first["tool_call"]["status"] != "DENIED"
    assert second["planner_kind"] == "IDEMPOTENT_REPLAY"
    assert second["hypothesis"]["hypothesis_id"] == first["hypothesis"]["hypothesis_id"]
    assert second["tool_call"]["tool_call_id"] == first["tool_call"]["tool_call_id"]
    assert len(attempts) == 1
    with new_session() as db:
        events = db.query(DropInsightEventModel).filter_by(event_type="planner.cpu_contract_rejected").all()
        assert len(events) == 1
        assert events[0].payload_json["candidate"]["falsification_criteria"] == invalid_proposal()["hypotheses"][0]["falsification_criteria"]
        assert events[0].payload_json["candidate"]["statement"] == invalid_proposal()["hypotheses"][0]["statement"]
        assert events[0].payload_json["candidate"]["expected_observations"] == invalid_proposal()["hypotheses"][0]["expected_observations"]
        assert events[0].payload_json["criteria_rewritten"] is False
        assert events[0].payload_json["dispatched"] is False
        assert db.get(DropInsightSessionModel, "diag").status != "INSUFFICIENT_EVIDENCE"
        assert db.query(DropInsightToolCallModel).count() == 1
        assert db.query(DropInsightHypothesisModel).filter_by(statement=invalid_proposal()["hypotheses"][0]["statement"]).count() == 0
        assert db.query(DropInsightHypothesisModel).filter_by(source="REGISTERED_OBSERVATION_RULE").count() == 1


def test_report_effect_unsupported_replan_completes_once_instead_of_resetting_pending(seeded, monkeypatch):
    from server.app.models import DropInsightReportModel
    attempts = mock_planning_boundary(monkeypatch)
    with new_session() as db:
        db.add(DropInsightReportModel(id="partial", diagnosis_id="diag", hypothesis_id="hyp",
            conclusion="Original immutable partial", confidence=600, evidence_refs_json=["support"],
            counter_evidence_refs_json=[], verification_json={"status": "PARTIAL_WITHOUT_COUNTER"},
            effects_status="PENDING", created_at=seeded))
        db.commit()
    first = service._apply_report_effects("partial")
    assert first.effects_status == "APPLIED"
    second = service._apply_report_effects("partial")
    assert second.effects_status == "APPLIED"
    assert first.effects_fencing_token == second.effects_fencing_token
    assert len(attempts) == 1
    with new_session() as db:
        report = db.get(DropInsightReportModel, "partial")
        assert report.conclusion == "Original immutable partial"
        assert report.verification_json["status"] == "PARTIAL_WITHOUT_COUNTER"
        assert db.query(DropInsightToolCallModel).count() == 0
        assert db.query(DropInsightHypothesisModel).count() == 1
        assert db.query(DropInsightEventModel).filter_by(event_type="planner.cpu_contract_rejected").count() == 1


@pytest.mark.parametrize("source", ["MODEL", "DETERMINISTIC_RULE", "SKILL_ROUTE"])
def test_candidate_rejection_is_idempotent_and_keeps_other_valid_candidates(seeded, source):
    from server.app.drop_insight.lats import prepare_candidates
    invalid = invalid_proposal()["hypotheses"][0]
    valid = service.cpu_observation_plan("GO")
    prepared = prepare_candidates([invalid, valid])
    diagnosis = service.get_diagnosis("diag")
    for _ in range(2):
        kept = service._admit_cpu_observation_candidates(diagnosis, prepared, source=source, effect_prefix="test-admission")
        assert len(kept) == 1 and kept[0]["statement"] == valid["statement"]
    assert invalid["falsification_criteria"][-1] == "Python samples prove uniformity across all windows"
    with new_session() as db:
        assert db.query(DropInsightEventModel).filter_by(event_type="planner.cpu_contract_rejected").count() == 1

from tests.test_cpu_control_worker_flow import flow, DID, HID, add_cpu
from server.app.drop_insight.schemas import GenerateReportRequest
from server.app.models import DropInsightReportModel


def test_registered_observation_report_serializes_noncausal_scope(flow):
    add_cpu(90)
    report = service.generate_report(DID, GenerateReportRequest(hypothesis_id=HID))
    verification = report.to_dict()['verification']
    assert verification['status'] == 'VERIFIED'
    assert verification['claim_scope'] == 'BOUNDED_OBSERVATION'
    assert verification['causal_root_cause_verified'] is False
    assert verification['observation_contract']['runtime'] == 'PYTHON'
    assert verification['observation_contract']['cpu_threshold'] == 50
    assert verification['observation_contract']['temporal_relationship'] == 'SEPARATE_COLLECTION_WINDOWS'


def test_existing_report_is_not_retrofitted_with_new_scope(flow):
    report = service.generate_report(DID, GenerateReportRequest(hypothesis_id=HID))
    with new_session() as session:
        stored = session.get(DropInsightReportModel, report.id)
        historical = dict(stored.verification_json)
        for key in ('claim_scope', 'causal_root_cause_verified', 'observation_contract'):
            historical.pop(key, None)
        stored.verification_json = historical
        session.commit()
        before = stored.to_dict()
    add_cpu(90)
    again = service.generate_report(DID, GenerateReportRequest(hypothesis_id=HID))
    assert again.to_dict() == before
