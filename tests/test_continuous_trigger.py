from datetime import datetime, timezone

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.diagnosis.continuous_trigger import ContinuousDiagnosisTrigger
from server.app.main import diagnosis_orchestrator, repo
from server.app.models import (
    ArtifactModel,
    Base,
    ContinuousDiagnosisTriggerModel,
    DiagnosisSessionModel,
)
from server.app.schemas import CreateTaskRequest


@pytest.fixture(autouse=True)
def _database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_AI_ENABLED", "none")
    monkeypatch.delenv("MINI_DROP_ALLOWED_SERVICES", raising=False)
    reset_engine()
    init_db()
    repo._task_queues.clear()
    repo.register_agent(
        "continuous-agent",
        "worker-1",
        "10.0.0.2",
        capabilities=["continuous_perf", "sys_metrics", "perf_cpu"],
    )
    yield
    from server.app.database import _get_engine

    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


def _completed_continuous_task(triggered: bool) -> str:
    task = repo.create_task(
        CreateTaskRequest(
            name="checkout-service",
            agent_id="continuous-agent",
            target_pid=2345,
            collector_type="continuous_perf",
            duration_sec=180,
            options={
                "service_id": "checkout-service",
                "instance_id": "checkout-1",
                "environment": "staging",
            },
        )
    )
    session = new_session()
    try:
        model = session.get(type(task), task.id)
        model.status = "DONE"
        model.collection_status = "SUCCEEDED"
        model.analysis_status = "SUCCEEDED"
        model.finished_at = datetime.now(timezone.utc)
        session.add(ArtifactModel(
            task_id=task.id,
            artifact_type="continuous_summary",
            bucket="mini-drop",
            object_key=f"tasks/{task.id}/windows.json",
            filename="windows.json",
            content_type="application/json",
            size_bytes=200,
            meta_json={
                "anomaly_detection": {
                    "triggered": triggered,
                    "detector_version": "test-v1",
                    "reason": "cpu_sample_surge" if triggered else "within_baseline",
                }
            },
            created_at=datetime.now(timezone.utc),
        ))
        session.commit()
    finally:
        session.close()
    return task.id


def test_trigger_store_lists_newest_promotions():
    task_id = _completed_continuous_task(triggered=True)
    trigger = ContinuousDiagnosisTrigger(diagnosis_orchestrator)
    assert trigger.scan_once() == 1

    rows = diagnosis_orchestrator.store.list_continuous_triggers()
    assert len(rows) == 1
    assert rows[0]["task_id"] == task_id
    assert rows[0]["status"] == "PROMOTED"
    assert rows[0]["diagnosis_id"].startswith("diag_session_")
    assert diagnosis_orchestrator.store.count_continuous_triggers() == 1


def test_anomaly_is_promoted_exactly_once():
    task_id = _completed_continuous_task(True)
    trigger = ContinuousDiagnosisTrigger(diagnosis_orchestrator)

    assert trigger.scan_once() == 1
    assert trigger.scan_once() == 0

    session = new_session()
    try:
        row = (
            session.query(ContinuousDiagnosisTriggerModel)
            .filter(ContinuousDiagnosisTriggerModel.task_id == task_id)
            .one()
        )
        diagnosis = session.get(DiagnosisSessionModel, row.diagnosis_id)
        assert row.status == "PROMOTED"
        assert diagnosis.creator_id == "continuous-profiler"
        assert diagnosis.target_scope_json["instances"][0]["pid"] == 2345
    finally:
        session.close()


def test_normal_window_does_not_create_diagnosis():
    _completed_continuous_task(False)

    assert ContinuousDiagnosisTrigger(diagnosis_orchestrator).scan_once() == 0
