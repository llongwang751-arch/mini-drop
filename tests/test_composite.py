from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.composite_service import aggregate_status, child_outcome
from server.app.database import init_db, new_session, reset_engine
from server.app.models import TaskModel
from server.app.sql_repository import SqlRepository
from server.app.state_machine import now_utc


@pytest.fixture(autouse=True)
def _patch_db_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    reset_engine()
    init_db()
    yield
    from server.app.models import Base
    from server.app.database import _get_engine

    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture(name="repo")
def repo_fixture() -> SqlRepository:
    return SqlRepository()


@pytest.fixture()
def agent(repo):
    return repo.register_agent("agent-comp", "host-comp", "10.0.0.8")


def _template(name: str) -> dict:
    return {
        "name": name,
        "agent_id": "agent-comp",
        "target_pid": 1,
        "collector_type": "perf_cpu",
        "sample_rate": 99,
        "duration_sec": 5,
    }


def test_child_outcome_mapping():
    assert child_outcome("DONE") == "succeeded"
    assert child_outcome("FAILED") == "failed"
    assert child_outcome("CANCELLED") == "cancelled"
    assert child_outcome("RUNNING") == "running"
    assert child_outcome(None) == "running"


def test_aggregate_all_required():
    ok = {"status": "succeeded", "role": "required"}
    fail = {"status": "failed", "role": "required"}
    run = {"status": "running", "role": "required"}
    assert aggregate_status("ALL_REQUIRED", [ok, ok]) == "SUCCEEDED"
    assert aggregate_status("ALL_REQUIRED", [ok, fail]) == "FAILED"
    assert aggregate_status("ALL_REQUIRED", [ok, run]) == "RUNNING"
    # Optional failure is tolerated.
    assert aggregate_status("ALL_REQUIRED", [ok, {"status": "failed", "role": "optional"}]) == "SUCCEEDED"


def test_aggregate_best_effort_and_quorum():
    ok = {"status": "succeeded", "role": "required"}
    fail = {"status": "failed", "role": "required"}
    assert aggregate_status("BEST_EFFORT", [ok, fail]) == "PARTIAL"
    assert aggregate_status("BEST_EFFORT", [ok, ok]) == "SUCCEEDED"
    assert aggregate_status("BEST_EFFORT", [fail, fail]) == "FAILED"
    assert aggregate_status("QUORUM", [ok, ok, fail], required_success_count=2) == "SUCCEEDED"
    assert aggregate_status("QUORUM", [ok, fail, fail], required_success_count=2) == "FAILED"


def test_create_composite_creates_children(agent, repo):
    composite = repo.create_composite_task(
        name="综合巡检",
        strategy="ALL_REQUIRED",
        children=[
            {"task_template": _template("cpu 子任务"), "role": "required"},
            {"task_template": _template("io 子任务"), "role": "optional"},
        ],
    )
    assert composite.status == "PENDING"
    items = repo.list_composite_items(composite.id)
    assert len(items) == 2
    assert items[0].role == "required"
    assert items[1].role == "optional"
    # Child tasks are real tasks.
    assert all(item.task_id.startswith("task_") for item in items)


def test_aggregate_reflects_child_statuses(agent, repo):
    composite = repo.create_composite_task(
        name="聚合",
        strategy="ALL_REQUIRED",
        children=[
            {"task_template": _template("a")},
            {"task_template": _template("b")},
        ],
    )
    items = repo.list_composite_items(composite.id)
    assert repo.aggregate_composite(composite.id) == "RUNNING"
    # One succeeds, one fails -> ALL_REQUIRED fails.
    with new_session() as session:
        a = session.get(TaskModel, items[0].task_id)
        a.status = "DONE"
        b = session.get(TaskModel, items[1].task_id)
        b.status = "FAILED"
        session.commit()
    assert repo.aggregate_composite(composite.id) == "FAILED"
    # Both succeed -> SUCCEEDED.
    with new_session() as session:
        b = session.get(TaskModel, items[1].task_id)
        b.status = "DONE"
        session.commit()
    assert repo.aggregate_composite(composite.id) == "SUCCEEDED"


def test_cancel_propagates_to_children(agent, repo):
    composite = repo.create_composite_task(
        name="取消",
        strategy="ALL_REQUIRED",
        children=[
            {"task_template": _template("a")},
            {"task_template": _template("b")},
        ],
    )
    items = repo.list_composite_items(composite.id)
    cancelled = repo.cancel_composite_task(composite.id)
    assert cancelled.status == "CANCELLED"
    with new_session() as session:
        statuses = {
            task.id: task.status
            for task in session.query(TaskModel).filter(
                TaskModel.id.in_([item.task_id for item in items])
            ).all()
        }
    assert set(statuses.values()) == {"CANCELLED"}


def test_composite_api_smoke(agent, repo):
    client = TestClient(__import__("server.app.main", fromlist=["app"]).app)
    created = client.post("/api/composite-tasks", json={
        "name": "API 复合",
        "strategy": "BEST_EFFORT",
        "children": [{"task_template": _template("子1")}],
    })
    assert created.status_code == 200, created.text
    composite_id = created.json()["data"]["id"]
    assert created.json()["data"]["status"] in {"RUNNING", "SUCCEEDED"}

    detail = client.get(f"/api/composite-tasks/{composite_id}")
    assert detail.status_code == 200
    assert len(detail.json()["data"]["items"]) == 1

    aggregate = client.post(f"/api/composite-tasks/{composite_id}/aggregate")
    assert aggregate.status_code == 200
    assert aggregate.json()["data"]["status"] in {"RUNNING", "SUCCEEDED"}
