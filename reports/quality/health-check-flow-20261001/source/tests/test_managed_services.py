from datetime import timedelta
import pytest
from server.app.database import init_db, reset_engine
from server.app.sql_repository import SqlRepository
from server.app.state_machine import now_utc
from server.app.drop_insight import managed_services as managed
from server.app.drop_insight.schemas import RunPlannerRequest


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    reset_engine()


def snapshot(pid=100, generation=1, age=0, complete=True, agent=None, hint=None, extra=False):
    entry = managed.catalog()[0]
    agent = agent or entry["agent_id"]
    repo = SqlRepository()
    repo.register_agent(agent, "host", "127.0.0.1", capabilities=["pyspy", "sys_metrics"])
    candidate = dict(pid=pid, process_start_ticks=pid * 10, pid_namespace_inode=200,
                     namespace_pid=pid, executable_identity="/opt/agi-office/venv/bin/python",
                     comm="python", cgroup="/system.slice/agi-office-backend.service",
                     service_hint=hint or entry["service_hint"], instance_hint="office",
                     collector_capabilities=["pyspy", "sys_metrics"])
    return repo.record_process_candidate_snapshot(agent, dict(
        generation=generation, boot_id="boot-office", observed_at_unix_ms=1,
        complete=complete, truncated=False, error="",
        candidates=[candidate, dict(candidate, pid=pid + 1, namespace_pid=pid + 1)] if extra else [candidate],
    ), received_at=now_utc() - timedelta(seconds=age))


def start():
    return managed.start_service_diagnosis("agi-office-backend", managed.StartServiceDiagnosis(
        query="上传文档后，后台知识库问答变慢，请检查运行时性能。"), principal="test:operator")


def test_service_entry_is_not_proof_of_running_process():
    assert managed.list_managed_services()["items"][0]["status"] == "UNAVAILABLE"
    with pytest.raises(ValueError, match="可信进程快照"):
        start()


@pytest.mark.parametrize("age,complete,status", [(0, True, "OBSERVED"), (3600, True, "STALE"), (0, False, "UNAVAILABLE")])
def test_current_snapshot_controls_availability(age, complete, status):
    snapshot(age=age, complete=complete)
    assert managed.list_managed_services()["items"][0]["status"] == status


def test_exact_service_match_not_substring():
    snapshot(hint="prefix-agi-office-backend.service")
    assert managed.list_managed_services()["items"][0]["status"] == "OFFLINE"


def test_restart_rebinds_new_pid_and_cannot_select_other_agent():
    snapshot(pid=100)
    snapshot(pid=999, agent="other-agent")
    first = start()
    assert first["target"]["pid"] == 100
    assert first["target"]["agent_id"] == managed.catalog()[0]["agent_id"]
    snapshot(pid=200, generation=2)
    second = start()
    assert second["target"]["pid"] == 200
    assert second["target"]["process_binding"]["process_start_ticks"] == 2000


def test_multiple_workers_require_selection_not_guessed_pid():
    snapshot(extra=True)
    diagnosis = start()
    assert diagnosis["status"] == "NEEDS_CLARIFICATION"
    assert not diagnosis["target"].get("process_binding")


def test_business_request_is_resolved_server_side_before_creating_case(monkeypatch):
    snapshot()
    monkeypatch.setattr(managed, "resolve_observation", lambda *args: (_ for _ in ()).throw(ValueError("请求不存在")))
    with pytest.raises(ValueError, match="请求不存在"):
        managed.start_service_diagnosis("agi-office-backend", managed.StartServiceDiagnosis(
            query="这次问答等待很久",request_id="a"*32),principal="test:operator")


def test_health_check_uses_one_real_system_baseline_without_fault_clarification(monkeypatch):
    snapshot()
    diagnosis = managed.start_service_diagnosis("agi-office-backend", managed.StartServiceDiagnosis(
        query="检查当前业务和进程是否存在可验证的性能故障", health_check=True), principal="test:operator")
    assert diagnosis["status"] == "UNDERSTANDING"
    assert diagnosis["target"]["pid"] == 100
    assert diagnosis["budget"]["max_tool_calls"] == 1
    assert diagnosis["budget"]["max_diagnosis_rounds"] == 1
    monkeypatch.setattr(managed.service, "propose_hypothesis_plan", lambda **kwargs: pytest.fail("health check must not wait for model planning"))
    result = managed.service.run_diagnosis_planner(diagnosis["diagnosis_id"], RunPlannerRequest())
    assert result["category"] == "SYSTEM_RESOURCE"
    assert result["tool_call"]["tool_name"] == "collect_sys_metrics"
    assert managed.service.get_diagnosis(diagnosis["diagnosis_id"]).status != "NEEDS_CLARIFICATION"


def test_worker_role_does_not_select_router_or_background_processor():
    entry={"process_name":"uwsgi","exclude_container_init":True}
    assert managed._process_matches(entry,{"process":"uwsgi","namespace_pid":31})
    assert not managed._process_matches(entry,{"process":"uwsgi","namespace_pid":1})
    assert not managed._process_matches(entry,{"process":"python","namespace_pid":32})
    assert not managed._process_matches(entry,{"process":"uwsgi"})


def completed_check(code="NORMAL_OBSERVED"):
    from server.app.models import DropInsightSessionModel
    snapshot()
    result = managed.start_service_diagnosis("agi-office-backend", managed.StartServiceDiagnosis(
        query="检查当前状态", health_check=True), principal="test:operator")
    with managed.new_session() as session:
        session.get(DropInsightSessionModel, result["diagnosis_id"]).status = "COMPLETED"
        managed.service._append_event(session, result["diagnosis_id"], "health_check.completed", "SYSTEM",
            {"service_id": "agi-office-backend", "code": code}, now_utc())
        session.commit()
    return result


def test_resampling_rebinds_after_restart_and_preserves_the_completed_parent():
    parent = completed_check()
    snapshot(pid=200, generation=2)
    child = managed.start_service_diagnosis("agi-office-backend", managed.StartServiceDiagnosis(
        query="检查当前状态", health_check=True, follow_up_diagnosis_id=parent["diagnosis_id"]), principal="test:operator")
    assert child["diagnosis_id"] != parent["diagnosis_id"]
    assert child["target"]["pid"] == 200
    assert child["target"]["process_binding"]["process_start_ticks"] == 2000
    previous = managed.service.get_diagnosis(parent["diagnosis_id"])
    assert previous.status == "COMPLETED" and previous.target_json["pid"] == 100
    created = next(e for e in managed.service.list_events(child["diagnosis_id"]) if e.event_type == "diagnosis.created")
    assert created.payload_json["follow_up_diagnosis_id"] == parent["diagnosis_id"]


@pytest.mark.parametrize("code", ["NORMAL_OBSERVED", "INSUFFICIENT_OBSERVABILITY"])
def test_deep_follow_up_requires_an_observed_anomaly(code):
    parent = completed_check(code)
    with pytest.raises(ValueError, match="只有已观测异常"):
        managed.start_service_diagnosis("agi-office-backend", managed.StartServiceDiagnosis(
            query="调查根因", follow_up_diagnosis_id=parent["diagnosis_id"]), principal="test:operator")


def test_anomalous_check_opens_a_separate_full_diagnosis():
    parent = completed_check("ANOMALY_OBSERVED")
    child = managed.start_service_diagnosis("agi-office-backend", managed.StartServiceDiagnosis(
        query="调查 CPU 性能异常", follow_up_diagnosis_id=parent["diagnosis_id"]), principal="test:operator")
    assert child["diagnosis_id"] != parent["diagnosis_id"] and child["budget"]["max_tool_calls"] > 1
    created = next(e for e in managed.service.list_events(child["diagnosis_id"]) if e.event_type == "diagnosis.created")
    assert created.payload_json["health_check"] is False


def test_follow_up_cannot_use_another_users_check():
    parent = completed_check()
    with pytest.raises(ValueError, match="当前用户和同一服务"):
        managed.start_service_diagnosis("agi-office-backend", managed.StartServiceDiagnosis(
            query="检查当前状态", health_check=True, follow_up_diagnosis_id=parent["diagnosis_id"]), principal="test:other")


def test_current_check_cannot_reuse_a_historical_business_request():
    with pytest.raises(ValueError, match="不关联历史请求"):
        managed.start_service_diagnosis("agi-office-backend", managed.StartServiceDiagnosis(
            query="检查当前状态", health_check=True, request_id="a"*32), principal="test:operator")
