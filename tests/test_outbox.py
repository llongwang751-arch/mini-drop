from __future__ import annotations

from datetime import timedelta
import threading
from types import SimpleNamespace

import pytest

from server.app.database import new_session, init_db, reset_engine
from server.app.models import OutboxMessageModel
from server.app.outbox_dispatcher import (
    dispatch_artifact_once,
    dispatch_once,
    run_artifact_worker,
    run_worker,
)
from server.app.schemas import CreateTaskRequest
from server.app.sql_repository import SqlRepository
from server.app.state_machine import now_utc


@pytest.fixture(autouse=True)
def _patch_db_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
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


def _naive_now():
    # SQLite stores timestamps without tzinfo; now_utc() is tz-aware.
    return now_utc().replace(tzinfo=None)


def _reopen_retry_window(message_id: str) -> None:
    with new_session() as session:
        row = session.get(OutboxMessageModel, message_id)
        row.next_attempt_at = now_utc() - timedelta(seconds=1)
        session.commit()


def test_enqueue_then_claim_marks_dispatching_with_lease(repo):
    message = repo.enqueue_outbox("task", "task-1", "task.created", {"name": "x"})
    claimed = repo.claim_outbox_messages("worker-1", limit=10)
    assert len(claimed) == 1
    assert claimed[0].id == message.id
    assert claimed[0].status == "DISPATCHING"
    assert claimed[0].worker_lease_owner == "worker-1"
    assert claimed[0].worker_lease_expires_at is not None


def test_publish_acks_and_releases_lease(repo):
    message = repo.enqueue_outbox("task", "task-1", "task.created", {})
    claimed = repo.claim_outbox_messages("worker-1")[0]
    repo.mark_outbox_published(claimed.id)
    published = repo.list_outbox_messages(status="PUBLISHED")
    assert len(published) == 1
    assert published[0].published_at is not None
    assert published[0].worker_lease_owner is None


def test_failure_backoffs_then_dead_letters(repo):
    message = repo.enqueue_outbox("task", "task-1", "task.created", {})
    claimed = repo.claim_outbox_messages("worker-1")[0]

    assert repo.fail_outbox_message(claimed.id, "boom", max_attempts=3) == "FAILED"
    failed = repo.list_outbox_messages(status="FAILED")[0]
    assert failed.attempts == 1
    assert failed.next_attempt_at > _naive_now()
    assert failed.last_error == "boom"

    _reopen_retry_window(message.id)
    assert len(repo.claim_outbox_messages("worker-2")) == 1
    assert repo.fail_outbox_message(message.id, "boom again", max_attempts=3) == "FAILED"

    _reopen_retry_window(message.id)
    assert len(repo.claim_outbox_messages("worker-3")) == 1
    assert repo.fail_outbox_message(message.id, "boom x3", max_attempts=3) == "DEAD_LETTER"

    dead = repo.list_outbox_messages(status="DEAD_LETTER")
    assert len(dead) == 1 and dead[0].attempts == 3


def test_claim_recovers_expired_lease(repo):
    message = repo.enqueue_outbox("task", "task-1", "task.created", {})
    repo.claim_outbox_messages("worker-1")
    # Live lease is not re-claimable by another worker.
    assert repo.claim_outbox_messages("worker-2") == []

    with new_session() as session:
        row = session.get(OutboxMessageModel, message.id)
        row.worker_lease_expires_at = now_utc() - timedelta(seconds=1)
        session.commit()

    reclaimed = repo.claim_outbox_messages("worker-2")
    assert len(reclaimed) == 1
    assert reclaimed[0].worker_lease_owner == "worker-2"


def test_create_task_enqueues_outbox_in_same_transaction(repo):
    repo.register_agent("agent-a", "host-a", "10.0.0.1")
    task = repo.create_task(
        CreateTaskRequest(
            name="t",
            agent_id="agent-a",
            target_pid=1,
            collector_type="perf_cpu",
        )
    )
    messages = repo.list_outbox_messages()
    assert len(messages) == 1
    assert messages[0].aggregate_type == "task"
    assert messages[0].aggregate_id == task.id
    assert messages[0].event_type == "task.created"
    assert messages[0].payload_json["collector_type"] == "perf_cpu"


def test_dispatch_once_delivers_successes_and_fails_others(repo):
    ok = repo.enqueue_outbox("task", "task-ok", "task.created", {})
    bad = repo.enqueue_outbox("task", "task-bad", "task.created", {})
    delivered: list[str] = []

    def deliver(message):
        if message.id == bad.id:
            raise RuntimeError("downstream rejected")
        delivered.append(message.id)

    assert dispatch_once(repo, "w1", deliver, max_attempts=3) == 2
    assert delivered == [ok.id]
    published = repo.list_outbox_messages(status="PUBLISHED")
    failed = repo.list_outbox_messages(status="FAILED")
    assert [item.id for item in published] == [ok.id]
    assert [item.id for item in failed] == [bad.id]
    assert failed[0].attempts == 1


def _message(message_id: str):
    return SimpleNamespace(
        id=message_id,
        aggregate_type="task",
        aggregate_id=f"task-{message_id}",
        event_type="task.created",
        attempts=0,
    )


def test_dispatch_once_claim_failure_isolated():
    class Repo:
        def claim_outbox_messages(self, worker_id, limit):
            raise RuntimeError("database unavailable")

    delivered = []
    assert dispatch_once(Repo(), "worker", delivered.append) == 0
    assert delivered == []


def test_dispatch_once_failure_persistence_error_does_not_starve_batch():
    first = _message("first")
    second = _message("second")

    class Repo:
        def claim_outbox_messages(self, worker_id, limit):
            return [first, second]

        def fail_outbox_message(self, *args, **kwargs):
            raise RuntimeError("write failed")

        def mark_outbox_published(self, message_id):
            assert message_id == second.id

    delivered = []

    def deliver(message):
        if message.id == first.id:
            raise RuntimeError("downstream failed")
        delivered.append(message.id)

    assert dispatch_once(Repo(), "worker", deliver) == 1
    assert delivered == [second.id]


def test_dispatch_once_ack_error_leaves_retryable_and_continues():
    first = _message("first")
    second = _message("second")
    failed = []

    class Repo:
        def claim_outbox_messages(self, worker_id, limit):
            return [first, second]

        def fail_outbox_message(self, *args, **kwargs):
            failed.append(args[0])

        def mark_outbox_published(self, message_id):
            if message_id == first.id:
                raise RuntimeError("ack failed")

    delivered = []
    assert dispatch_once(Repo(), "worker", lambda item: delivered.append(item.id)) == 1
    assert delivered == [first.id, second.id]
    assert failed == []


def _artifact_message(message_id: str):
    return {
        "outbox_id": message_id,
        "diagnosis_id": f"diagnosis-{message_id}",
        "artifact_id": f"artifact-{message_id}",
        "artifact_hash": "sha256:" + "a" * 64,
        "attempts": 0,
    }


def test_dispatch_artifact_once_claim_failure_isolated():
    class Store:
        def claim_artifact_outbox(self, worker_id, limit):
            raise RuntimeError("database unavailable")

    delivered = []
    assert dispatch_artifact_once(Store(), "worker", delivered.append) == 0
    assert delivered == []


def test_dispatch_artifact_once_failure_persistence_error_does_not_starve_batch():
    first = _artifact_message("first")
    second = _artifact_message("second")

    class Store:
        def claim_artifact_outbox(self, worker_id, limit):
            return [first, second]

        def fail_artifact_outbox(self, *args, **kwargs):
            raise RuntimeError("write failed")

        def mark_artifact_outbox_published(self, message_id, worker_id):
            assert message_id == second["outbox_id"]

    delivered = []

    def deliver(message):
        if message is first:
            raise RuntimeError("downstream failed")
        delivered.append(message["outbox_id"])

    assert dispatch_artifact_once(Store(), "worker", deliver) == 1
    assert delivered == [second["outbox_id"]]


def test_dispatch_artifact_once_ack_error_leaves_retryable_and_continues():
    first = _artifact_message("first")
    second = _artifact_message("second")
    failed = []

    class Store:
        def claim_artifact_outbox(self, worker_id, limit):
            return [first, second]

        def fail_artifact_outbox(self, *args, **kwargs):
            failed.append(args[0])

        def mark_artifact_outbox_published(self, message_id, worker_id):
            if message_id == first["outbox_id"]:
                raise RuntimeError("ack failed")

    delivered = []
    assert dispatch_artifact_once(
        Store(), "worker", lambda item: delivered.append(item["outbox_id"])
    ) == 1
    assert delivered == [first["outbox_id"], second["outbox_id"]]
    assert failed == []


def test_task_worker_stops_while_idle(monkeypatch):
    stop_event = threading.Event()
    calls = []

    def idle_dispatch(*args, **kwargs):
        calls.append("dispatch")
        stop_event.set()
        return 0

    monkeypatch.setattr(
        "server.app.outbox_dispatcher.dispatch_once",
        idle_dispatch,
    )

    run_worker(
        object(),
        "worker-stop",
        poll_seconds=60,
        stop_event=stop_event,
    )

    assert calls == ["dispatch"]


def test_artifact_worker_stops_while_idle(monkeypatch):
    stop_event = threading.Event()
    calls = []

    def idle_dispatch(*args, **kwargs):
        calls.append("dispatch")
        stop_event.set()
        return 0

    monkeypatch.setattr(
        "server.app.outbox_dispatcher.dispatch_artifact_once",
        idle_dispatch,
    )

    run_artifact_worker(
        object(),
        "artifact-worker-stop",
        poll_seconds=60,
        stop_event=stop_event,
    )

    assert calls == ["dispatch"]


def test_event_bus_deliver_fans_task_created_to_sse():
    from types import SimpleNamespace

    from server.app.event_bus import BUS
    from server.app.outbox_dispatcher import event_bus_deliver

    message = SimpleNamespace(
        id="outbox_1",
        aggregate_type="task",
        aggregate_id="task-sse",
        event_type="task.created",
    )
    event_bus_deliver(message)
    # The bus is a capped module-level singleton; assert the last event is ours
    # rather than an exact history length.
    latest = BUS.get_history()[-1]
    assert latest["event"] == "task_changed"
    assert latest["data"]["task_id"] == "task-sse"
    assert latest["data"]["to_status"] == "PENDING"


def test_dispatch_once_delivers_task_created_to_real_downstream(repo):
    from server.app.event_bus import BUS
    from server.app.outbox_dispatcher import dispatch_once, event_bus_deliver

    repo.register_agent("agent-ob", "host-ob", "10.0.0.7")
    task = repo.create_task(
        CreateTaskRequest(
            name="outbox 下游",
            agent_id="agent-ob",
            target_pid=1,
            collector_type="perf_cpu",
        )
    )
    assert dispatch_once(repo, "w-sse", event_bus_deliver) == 1
    published = repo.list_outbox_messages(status="PUBLISHED")
    assert len(published) == 1
    assert published[0].aggregate_id == task.id
    assert any(
        entry["event"] == "task_changed" and entry["data"]["task_id"] == task.id
        for entry in BUS.get_history()
    )
