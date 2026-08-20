from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.schemas import CreateTaskRequest
from server.app.sql_repository import SqlRepository


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
    return repo.register_agent("agent-idem", "host-idem", "10.0.0.6")


def _payload(name="t"):
    return CreateTaskRequest(
        name=name,
        agent_id="agent-idem",
        target_pid=1,
        collector_type="perf_cpu",
    )


def test_repo_replays_identical_key_and_rejects_conflict(agent, repo):
    first = repo.create_task(_payload(), idempotency_key="key-0001", creator_id="alice")
    second = repo.create_task(_payload(), idempotency_key="key-0001", creator_id="alice")
    assert first.id == second.id

    with pytest.raises(ValueError, match="Idempotency-Key 已用于不同参数"):
        repo.create_task(
            _payload(name="different"),
            idempotency_key="key-0001",
            creator_id="alice",
        )

    # Different creator does not collide.
    other = repo.create_task(_payload(), idempotency_key="key-0001", creator_id="bob")
    assert other.id != first.id


def test_api_idempotency_header_replays_same_task(agent, repo):
    client = TestClient(__import__("server.app.main", fromlist=["app"]).app)
    body = {
        "name": "幂等",
        "agent_id": "agent-idem",
        "target_pid": 1,
        "collector_type": "perf_cpu",
    }
    headers = {"Idempotency-Key": "api-key-0001"}
    first = client.post("/api/tasks", json=body, headers=headers)
    second = client.post("/api/tasks", json=body, headers=headers)
    assert first.status_code == 200, first.text
    assert second.status_code == 200
    assert first.json()["data"]["task_id"] == second.json()["data"]["task_id"]

    conflict = client.post(
        "/api/tasks",
        json={**body, "name": "不同参数"},
        headers=headers,
    )
    assert conflict.status_code == 409
    assert "Idempotency-Key" in conflict.json()["detail"]
