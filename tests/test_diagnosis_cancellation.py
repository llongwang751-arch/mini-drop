"""User cancellation must fence queued work and preserve immutable evidence."""
import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight import service
from server.app.drop_insight.schemas import CreateDiagnosisRequestV2, CreateHypothesisRequest, CreateToolCallRequest
from server.app.models import DropInsightSessionModel, DropInsightToolCallModel, TaskModel, AgentModel, AnalysisJobModel
from server.app.state_machine import now_utc


@pytest.fixture(autouse=True)
def database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    reset_engine()


def seed(status="NEEDS_CLARIFICATION", task_status=None):
    diagnosis = service.create_diagnosis(CreateDiagnosisRequestV2(query="检查服务运行情况", auto_scope=False))
    with new_session() as session:
        row = session.get(DropInsightSessionModel, diagnosis.id)
        row.status = status
        if task_status:
            timestamp = now_utc()
            session.add(AgentModel(id="cancel-agent", hostname="test", ip_addr="127.0.0.1", status="ONLINE",
                                   last_heartbeat_at=timestamp, created_at=timestamp, updated_at=timestamp))
            session.flush()
            session.add(TaskModel(id="cancel-task", name="cancel test", agent_id="cancel-agent", target_pid=123,
                                  collector_type="sys_metrics", status=task_status, created_at=timestamp, updated_at=timestamp))
            session.flush()
            session.add(DropInsightToolCallModel(
                id="cancel-call", diagnosis_id=diagnosis.id, tool_name="collect_sys_metrics", arguments_json={},
                policy_decision="ALLOW", policy_reason="test", status="RUNNING", task_id="cancel-task",
                budget_reservation_status="RESERVED", budget_reservation_json={"duration_seconds": 30},
                requested_by="test", created_at=timestamp))
        session.commit()
    return service.get_diagnosis(diagnosis.id)


def test_cancel_is_idempotent_even_with_original_version_and_preserves_actor():
    diagnosis = seed()
    first = service.cancel_diagnosis(diagnosis.id, reason="停止本次检查", cancelled_by="operator:alice", expected_version=diagnosis.version)
    repeated = service.cancel_diagnosis(diagnosis.id, reason="重复请求", cancelled_by="operator:bob", expected_version=diagnosis.version)
    assert first.status == repeated.status == "CANCELLED"
    assert first.version == repeated.version == diagnosis.version + 1
    events = [e for e in service.list_events(diagnosis.id) if e.event_type == "diagnosis.cancelled"]
    assert len(events) == 1
    assert events[0].payload_json["cancelled_by"] == "operator:alice"
    assert events[0].payload_json["reason"] == "停止本次检查"


@pytest.mark.parametrize("task_status", ["PENDING", "RUNNING", "UPLOADING", "ANALYZING"])
def test_cancel_stops_owned_active_task_and_releases_budget(task_status):
    diagnosis = seed("COLLECTING_EVIDENCE", task_status)
    service.cancel_diagnosis(diagnosis.id)
    with new_session() as session:
        task = session.get(TaskModel, "cancel-task")
        call = session.get(DropInsightToolCallModel, "cancel-call")
        assert task.status == call.status == "CANCELLED"
        assert task.finished_at is not None
        assert call.budget_reservation_status == "RELEASED"
        assert call.terminal_processing_status == "REPORT_EFFECTS_DONE"
    service.advance_diagnosis(diagnosis.id)
    assert service.get_diagnosis(diagnosis.id).status == "CANCELLED"


@pytest.mark.parametrize("status", ["COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED"])
def test_cancel_does_not_rewrite_completed_history(status):
    diagnosis = seed(status)
    with pytest.raises(ValueError, match="terminal"):
        service.cancel_diagnosis(diagnosis.id)
    assert service.get_diagnosis(diagnosis.id).status == status


def test_cancel_rejects_stale_version_without_side_effects():
    diagnosis = seed()
    with pytest.raises(ValueError, match="version conflict"):
        service.cancel_diagnosis(diagnosis.id, expected_version=diagnosis.version + 1)
    assert service.get_diagnosis(diagnosis.id).status == diagnosis.status


def test_late_planner_cannot_create_hypothesis_or_tool_after_cancel():
    diagnosis = seed()
    service.cancel_diagnosis(diagnosis.id)
    with pytest.raises(ValueError, match="cancelled"):
        service.create_hypothesis(diagnosis.id, CreateHypothesisRequest(statement="等待采集以核验现象",
                                  expected_observations=["采集结果"], falsification_criteria=["未发现现象"]))
    with pytest.raises(ValueError, match="cancelled"):
        service.request_tool_call(diagnosis.id, CreateToolCallRequest(tool_name="collect_sys_metrics", arguments={}))
    assert service.list_hypotheses(diagnosis.id) == []
    assert service.list_tool_calls(diagnosis.id) == []


def test_cancel_preserves_completed_tool_result_and_missing_or_archived_is_not_found():
    diagnosis = seed("COLLECTING_EVIDENCE", "DONE")
    with new_session() as session:
        call = session.get(DropInsightToolCallModel, "cancel-call")
        call.status = "COMPLETED"
        call.result_json = {"immutable_artifact": "sha256:original"}
        session.commit()
    service.cancel_diagnosis(diagnosis.id)
    with new_session() as session:
        assert session.get(TaskModel, "cancel-task").status == "DONE"
        call = session.get(DropInsightToolCallModel, "cancel-call")
        assert call.status == "COMPLETED"
        assert call.result_json == {"immutable_artifact": "sha256:original"}
        row = session.get(DropInsightSessionModel, diagnosis.id)
        row.deleted_at = now_utc()
        session.commit()
    assert service.cancel_diagnosis(diagnosis.id) is None
    assert service.cancel_diagnosis("missing-diagnosis") is None


def test_cancel_revokes_analyzer_lease_and_discards_late_output():
    diagnosis = seed("COLLECTING_EVIDENCE", "ANALYZING")
    timestamp = now_utc()
    with new_session() as session:
        session.add(AnalysisJobModel(id="cancel-job", task_id="cancel-task", analyzer_type="sys_metrics",
                    analyzer_version="1", input_checksum="a" * 64, idempotency_key="cancel-job",
                    status="RUNNING", lease_owner="late-worker", next_run_at=timestamp,
                    created_at=timestamp, updated_at=timestamp))
        session.commit()
    service.cancel_diagnosis(diagnosis.id)
    repository = service.SqlRepository()
    assert repository.renew_analysis_job_lease("cancel-job", "late-worker") is False
    result = repository.complete_analysis_job("cancel-job", "late-worker", output_artifacts=[{"filename": "late.json"}])
    assert result.status == "CANCELLED"
    assert repository.get_artifacts("cancel-task") == []
    assert repository.fail_analysis_job("cancel-job", "late-worker", error_code="LATE", error_message="late").status == "CANCELLED"
    assert repository.claim_analysis_job("next-worker") is None
