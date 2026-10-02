"""Independent worker-state integration: real predicate, gate and report persistence.

Only remote task dispatch/import is simulated; this suite sends no requests.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight import service
from server.app.drop_insight.cpu_criteria import cpu_observation_plan
from server.app.drop_insight.evidence import EvidenceEnvelope, classify_evidence
from server.app.drop_insight.hypothesis_predicate import _compute_hypothesis_predicate
from server.app.drop_insight.schemas import GenerateReportRequest
from server.app.metric_analyzers import _parse_sys_metrics_document
from server.app.models import (AgentModel, TaskModel, DropInsightSessionModel,
    DropInsightHypothesisModel, DropInsightToolCallModel, DropInsightEvidenceModel,
    DropInsightReportModel)

NOW = datetime(2026, 9, 28, 14, tzinfo=timezone.utc)
DID, HID = "cpu-worker-flow", "cpu-original-hypothesis"
PLAN = cpu_observation_plan("PYTHON")


@pytest.fixture
def flow(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    monkeypatch.setattr(service, "now_utc", lambda: NOW + timedelta(seconds=10))
    monkeypatch.setattr(service, "_current_target_binding", lambda _: SimpleNamespace(agent_id="agent", pid=123))
    monkeypatch.setattr(service, "_available_planner_tools", lambda *_: ["start_pyspy_profile", "collect_sys_metrics"])
    monkeypatch.setattr(service, "_planner_tool_arguments", lambda *a, **k: {"agent_id": "agent", "pid": 123, "duration_seconds": 15})
    monkeypatch.setattr(service, "_record_lats_action_dispatched", lambda *a, **k: None)
    monkeypatch.setattr(service, "_record_lats_tool_failure", lambda *a, **k: None)
    monkeypatch.setattr(service, "_replan_after_insufficient_evidence", lambda *a, **k: None)
    monkeypatch.setattr(service, "map_hot_functions", lambda *a, **k: {})
    # Effects schedule later rounds; their separate regressions run alongside this
    # suite. Keep report generation, verification and uniqueness entirely real.
    def effects(report_id):
        with new_session() as s:
            report = s.get(DropInsightReportModel, report_id)
            report.effects_status = "APPLIED"
            s.commit()
            s.expunge(report)
            return report
    monkeypatch.setattr(service, "_apply_report_effects", effects)
    with new_session() as s:
        s.add(AgentModel(id="agent", hostname="test", ip_addr="127.0.0.1", capabilities=["pyspy", "sys_metrics"], status="ONLINE", last_heartbeat_at=NOW, created_at=NOW, updated_at=NOW))
        s.add(DropInsightSessionModel(id=DID, query="Python CPU high", target_json={"agent_id": "agent", "pid": 123}, time_range_json={}, mode="AUTONOMOUS", skill_policy="AUTO", budget_json={"min_diagnosis_rounds": 3}, status="COLLECTING_EVIDENCE", version=1, created_at=NOW, updated_at=NOW))
        s.add(DropInsightHypothesisModel(id=HID, diagnosis_id=DID, statement=PLAN["statement"], expected_observations_json=PLAN["expected_observations"], falsification_criteria_json=PLAN["falsification_criteria"], status="OPEN", source="MODEL", round_index=1, created_at=NOW, updated_at=NOW))
        s.commit()
    dispatches = []
    def dispatch(diagnosis_id, payload, **kwargs):
        dispatches.append(kwargs["effect_key"])
        with new_session() as s:
            call = DropInsightToolCallModel(id="control-call", diagnosis_id=DID, hypothesis_id=HID, tool_name="collect_sys_metrics", arguments_json=payload.arguments, policy_decision="REQUIRE_APPROVAL", policy_checks_json=[], policy_reason="test policy", requested_by="test", status="PENDING_APPROVAL", effect_key=kwargs["effect_key"], created_at=NOW + timedelta(seconds=1))
            s.add(call)
            s.commit()
            s.expunge(call)
            return call
    monkeypatch.setattr(service, "request_tool_call", dispatch)
    def imported(diagnosis_id, payload, *, terminal_tool_call_id):
        with new_session() as s:
            s.get(DropInsightToolCallModel, terminal_tool_call_id).terminal_processing_status = "EVIDENCE_IMPORTED"
            s.commit()
        return []
    monkeypatch.setattr(service, "import_task_evidence", imported)
    add_task("profile-task", "profile-call", "DONE", "start_pyspy_profile", "pyspy")
    add_evidence("profile", {"schema_version": "pyspy_analysis.v1", "sample_count": 100,
        "top_functions": [{"name": "source_hot_function", "percent": 90, "file": "/app/app.py", "line": 170}]}, "pyspy", 100)
    yield dispatches
    reset_engine()


def add_task(task_id, call_id, status, tool, collector):
    with new_session() as s:
        s.add(TaskModel(id=task_id, name=task_id, agent_id="agent", target_pid=123,
                        collector_type=collector, status=status, created_at=NOW, duration_sec=5))
        call = s.get(DropInsightToolCallModel, call_id)
        if call is None:
            call = DropInsightToolCallModel(id=call_id, diagnosis_id=DID, hypothesis_id=HID, tool_name=tool, arguments_json={}, policy_decision="ALLOW", policy_checks_json=[], policy_reason="test policy", requested_by="test", status="TASK_CREATED", created_at=NOW)
            s.add(call)
        call.task_id = task_id
        call.status = "TASK_CREATED"
        s.commit()


def add_evidence(eid, metadata, tool, count):
    with new_session() as s:
        h = s.get(DropInsightHypothesisModel, HID)
        predicate = _compute_hypothesis_predicate(h, metadata)
        assert predicate is not None
        metadata["hypothesis_predicate"] = predicate
        env = EvidenceEnvelope(evidence_id=eid, diagnosis_id=DID, evidence_type="PROFILE",
            source={"tool_name": tool, "task_id": eid, "task_attempt_id": eid, "artifact_id": eid,
                    "artifact_sha256": "a" * 64, "analysis_job_id": eid, "analyzer_version": "test",
                    "analyzer_output_schema_version": metadata["schema_version"]},
            scope={"agent_id": "agent", "pid": 123},
            time_range={"start": NOW, "end": NOW + timedelta(seconds=10)},
            observation={"metadata": metadata}, quality={"level": "HIGH", "sample_count": count,
                "degraded": False, "target_match": True, "time_overlap": True})
        classification = classify_evidence(env)
        assert classification["can_support_conclusion"], classification
        s.add(DropInsightEvidenceModel(id=eid, diagnosis_id=DID, hypothesis_id=HID,
             role=predicate["outcome"], envelope_json=env.model_dump(mode="json"),
             classification_json=classification, created_at=NOW))
        s.commit()
        return predicate["outcome"]


def add_cpu(usage):
    base = int(NOW.timestamp() * 1000)
    metadata = _parse_sys_metrics_document({"schema_version": "sys_metrics.v2", "pid": 123,
        "clock_ticks_per_second": 100, "samples": [
            {"offset_sec": i, "captured_at_unix_ms": base + 1000 * i,
             "process_start_ticks": 42, "process_cpu_ticks": 100 + usage * i}
            for i in range(5)]})
    return add_evidence("cpu", metadata, "sys_metrics", 5)


def reports():
    with new_session() as s:
        result = s.query(DropInsightReportModel).filter_by(diagnosis_id=DID).all()
        for r in result: s.expunge(r)
        return result


def test_same_hypothesis_waits_for_control_then_freezes_one_report(flow):
    first = service.advance_diagnosis(DID)
    assert reports() == []
    assert any(x["action"] == "WAIT_FOR_ROUND_EVIDENCE" for x in first["actions"])
    service.advance_diagnosis(DID)
    assert len(flow) == 1 and reports() == []
    assert add_cpu(90) == "CONTROL"
    add_task("control-task", "control-call", "DONE", "collect_sys_metrics", "sys_metrics")
    service.advance_diagnosis(DID)
    saved = reports()
    assert len(saved) == 1
    assert saved[0].verification_json["status"] == "VERIFIED"
    assert saved[0].verification_json["claim_scope"] == "BOUNDED_OBSERVATION"
    assert saved[0].verification_json["causal_root_cause_verified"] is False
    assert saved[0].to_dict()["verification"]["observation_contract"]["temporal_relationship"] == "SEPARATE_COLLECTION_WINDOWS"
    assert saved[0].conclusion.startswith("已验证观测")
    assert "根因" not in saved[0].conclusion
    assert "不证明同窗一致" in saved[0].conclusion
    assert "因果贡献" in saved[0].conclusion
    assert set(saved[0].evidence_refs_json) == {"profile"}
    service.advance_diagnosis(DID)
    assert len(reports()) == 1 and len(flow) == 1
    assert service._report_round_contract_snapshot(DID)["observed"] == 1
    assert service._report_round_contract_snapshot(DID)["satisfied"] is False


@pytest.mark.parametrize("ending", ["DENIED", "EXPIRED", "FAILED", "CANCELLED"])
def test_denied_expired_or_failed_control_does_not_retry_or_freeze_worker(flow, ending):
    service.advance_diagnosis(DID)
    if ending in {"FAILED", "CANCELLED"}:
        add_task("control-task", "control-call", ending, "collect_sys_metrics", "sys_metrics")
    else:
        with new_session() as s:
            s.get(DropInsightToolCallModel, "control-call").status = ending
            s.commit()
    for _ in range(3): service.advance_diagnosis(DID)
    assert len(flow) == 1
    assert len(reports()) == 1
    assert reports()[0].verification_json["status"] == "PARTIAL_WITHOUT_COUNTER"


def test_low_cpu_is_counter_and_strict_root_remains_rejected(flow):
    service.advance_diagnosis(DID)
    assert add_cpu(0) == "COUNTER"
    add_task("control-task", "control-call", "DONE", "collect_sys_metrics", "sys_metrics")
    service.advance_diagnosis(DID)
    report = reports()[0]
    assert report.counter_evidence_refs_json == ["cpu"]
    assert report.conclusion.startswith("阶段性观测")
    assert "根因" not in report.conclusion
    from scripts.run_fault_plaza_strict_acceptance import evaluate_reports
    assert not evaluate_reports("source-hotspot", [report.to_dict()])["root_cause_accepted"]


def test_existing_immutable_partial_report_is_never_upgraded(flow):
    old = service.generate_report(DID, GenerateReportRequest(hypothesis_id=HID))
    before = old.to_dict()
    assert service._request_cpu_control_before_report(DID, HID) is None
    assert flow == []
    add_cpu(90)
    again = service.generate_report(DID, GenerateReportRequest(hypothesis_id=HID))
    assert again.to_dict() == before


def test_legacy_stronger_hypothesis_is_not_rewritten_to_new_observation_contract(flow):
    old_statement = "Python CPU high causes all slow requests in the same window"
    old_expected = ["source_hot_function accounts for more than 99% of every slow request"]
    with new_session() as s:
        h = s.get(DropInsightHypothesisModel, HID)
        h.statement = old_statement
        h.expected_observations_json = old_expected
        s.commit()
    assert service._request_cpu_control_before_report(DID, HID) is None
    assert flow == []
    with new_session() as s:
        h = s.get(DropInsightHypothesisModel, HID)
        assert h.statement == old_statement
        assert h.expected_observations_json == old_expected
