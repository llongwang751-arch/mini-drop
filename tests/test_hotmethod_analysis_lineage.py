"""Collector callback lineage tests against the durable SQL repository."""

from __future__ import annotations

from unittest import mock

import grpc
import pytest

from server.app.database import init_db, reset_engine
from server.app.generated import hotmethod_pb2
from server.app.grpc_services.hotmethod_service import HotmethodService
from server.app.models import Base
from server.app.schemas import CreateTaskRequest
from server.app.sql_repository import SqlRepository
from server.app.state_machine import TaskStatus


@pytest.fixture(autouse=True)
def _database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    from server.app.database import _get_engine

    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


def _dispatched_task(repo: SqlRepository):
    repo.register_agent(
        "lineage-agent",
        "lineage-host",
        "10.0.0.12",
        capabilities=["sys_metrics"],
    )
    task = repo.create_task(CreateTaskRequest(
        name="lineage-test",
        agent_id="lineage-agent",
        target_pid=1,
        collector_type="sys_metrics",
        duration_sec=5,
    ))
    dispatch = repo.heartbeat("lineage-agent", "10.0.0.12")
    assert dispatch is not None
    return task, dispatch


def _result(task_id: str, authority: str):
    return hotmethod_pb2.TaskResult(
        task_id=task_id,
        task_attempt_authority=authority,
        artifact_metadata_json=(
            '[{"artifact_type":"sys_metrics",'
            '"object_key":"tasks/lineage/sys_metrics.json",'
            '"content_type":"application/json"}]'
        ),
    )


def test_callback_binds_artifact_and_analysis_job_to_same_attempt():
    repo = SqlRepository()
    task, dispatch = _dispatched_task(repo)

    HotmethodService(repo).NotifyResult(
        _result(task.id, dispatch.task_attempt_authority),
        mock.Mock(),
    )

    refreshed = repo.get_task(task.id)
    artifacts = repo.get_artifacts(task.id)
    jobs = repo.list_analysis_jobs(task_id=task.id)

    assert refreshed.status == TaskStatus.ANALYZING.value
    assert len(artifacts) == 1
    assert artifacts[0]["task_attempt_id"] == dispatch.task_attempt_id
    assert len(jobs) == 1
    assert jobs[0].task_attempt_id == dispatch.task_attempt_id
    inputs = repo.get_analysis_job_input_artifacts(jobs[0].id)
    assert [item["id"] for item in inputs] == [artifacts[0]["id"]]


def test_callback_rejects_wrong_attempt_authority():
    repo = SqlRepository()
    task, _dispatch = _dispatched_task(repo)
    context = mock.Mock()
    context.abort.side_effect = grpc.RpcError("aborted")

    with pytest.raises(grpc.RpcError):
        HotmethodService(repo).NotifyResult(
            _result(task.id, "0" * 64),
            context,
        )

    context.abort.assert_called_once_with(
        grpc.StatusCode.UNAUTHENTICATED,
        "invalid or expired task attempt authority",
    )
    assert repo.get_task(task.id).status == TaskStatus.RUNNING.value
    assert repo.get_artifacts(task.id) == []
    assert repo.list_analysis_jobs(task_id=task.id) == []


def test_enqueue_failure_marks_task_failed_instead_of_leaving_analyzing():
    repo = SqlRepository()
    task, dispatch = _dispatched_task(repo)

    with mock.patch(
        "server.app.grpc_services.hotmethod_service.enqueue_artifact_analysis",
        side_effect=RuntimeError("queue unavailable"),
    ):
        with pytest.raises(RuntimeError, match="queue unavailable"):
            HotmethodService(repo).NotifyResult(
                _result(task.id, dispatch.task_attempt_authority),
                mock.Mock(),
            )

    refreshed = repo.get_task(task.id)
    assert refreshed.status == TaskStatus.FAILED.value
    assert refreshed.collection_status == "SUCCEEDED"
    assert refreshed.analysis_status == "FAILED"
    assert "RuntimeError" in refreshed.status_reason
