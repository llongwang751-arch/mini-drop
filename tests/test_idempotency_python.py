from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock, get_ident

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from server.app.database import _get_engine, init_db, new_session, reset_engine
from server.app.models import TaskModel
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


def test_repo_recovers_deterministic_concurrent_idempotency_race(
    monkeypatch, tmp_path
):
    # Use separate connections and repository locks, as independent requests do.
    # The production model gets this index from Alembic; the lightweight test
    # schema is built from metadata, so add it explicitly here.
    database = tmp_path / "idempotency-race.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database.as_posix()}")
    reset_engine()
    init_db()
    repositories = [SqlRepository(), SqlRepository()]
    repositories[0].register_agent("agent-idem", "host-idem", "10.0.0.6")
    with _get_engine().begin() as connection:
        connection.execute(text(
            "CREATE UNIQUE INDEX uq_tasks_creator_idempotency "
            "ON tasks (creator_id, idempotency_key)"
        ))

    pre_read_barrier = Barrier(2)
    first_flush_barrier = Barrier(2)
    release_loser = Barrier(2)
    calls_lock = Lock()
    pre_reads: set[int] = set()
    recovery_reads: list[int] = []
    flush_order: list[int] = []

    def install_race_hooks(repository):
        original_find = repository._find_idempotent_task
        original_write_session = repository._write_session

        def synchronized_find(creator_id, idempotency_key, payload):
            thread_id = get_ident()
            with calls_lock:
                is_pre_read = thread_id not in pre_reads
                if is_pre_read:
                    pre_reads.add(thread_id)
            if is_pre_read:
                existing = original_find(creator_id, idempotency_key, payload)
                assert existing is None
                pre_read_barrier.wait()
                return None
            recovery_reads.append(thread_id)
            return original_find(creator_id, idempotency_key, payload)

        def synchronized_write_session():
            context = original_write_session()

            class FlushGate:
                def __enter__(self):
                    session = context.__enter__()
                    original_flush = session.flush

                    def flush(*args, **kwargs):
                        thread_id = get_ident()
                        gate_flush = False
                        with calls_lock:
                            if thread_id not in flush_order:
                                flush_order.append(thread_id)
                                position = len(flush_order)
                                gate_flush = True
                        if gate_flush and position == 1:
                            first_flush_barrier.wait()
                        elif gate_flush and position == 2:
                            first_flush_barrier.wait()
                            release_loser.wait()
                        return original_flush(*args, **kwargs)

                    session.flush = flush
                    return session

                def __exit__(self, exc_type, exc_value, traceback):
                    try:
                        return context.__exit__(exc_type, exc_value, traceback)
                    finally:
                        if flush_order and get_ident() == flush_order[0]:
                            release_loser.wait()

            return FlushGate()

        monkeypatch.setattr(repository, "_find_idempotent_task", synchronized_find)
        monkeypatch.setattr(repository, "_write_session", synchronized_write_session)

    for repository in repositories:
        install_race_hooks(repository)

    payload = _payload(name="concurrent")
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                repository.create_task,
                payload,
                idempotency_key="key-race",
                creator_id="alice",
            )
            for repository in repositories
        ]
        tasks = [future.result(timeout=5) for future in futures]

    assert tasks[0].id == tasks[1].id
    assert len(pre_reads) == 2
    assert len(flush_order) == 2
    assert recovery_reads == [flush_order[1]]

    session = new_session()
    try:
        stored = session.query(TaskModel).filter(
            TaskModel.creator_id == "alice",
            TaskModel.idempotency_key == "key-race",
        ).all()
    finally:
        session.close()
    assert [task.id for task in stored] == [tasks[0].id]

    with pytest.raises(ValueError, match="Idempotency-Key 已用于不同参数"):
        repositories[0].create_task(
            _payload(name="different"),
            idempotency_key="key-race",
            creator_id="alice",
        )


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
