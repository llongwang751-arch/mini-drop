from datetime import datetime, timezone

from server.app.database import init_db, new_session, reset_engine
from server.app.models import OutboxMessageModel
from server.app.outbox_dispatcher import OutboxDispatcher
from server.app.sql_repository import SqlRepository


def test_dispatcher_publishes_and_acknowledges(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    now = datetime.now(timezone.utc)
    with new_session() as session:
        session.add(
            OutboxMessageModel(
                id="outbox-test",
                aggregate_type="task",
                aggregate_id="task-test",
                event_type="task.created",
                payload_json={"task_id": "task-test"},
                status="PENDING",
                attempts=0,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    published = []
    dispatcher = OutboxDispatcher(
        repository=SqlRepository(),
        sink=published.append,
        worker_id="test-worker",
    )
    assert dispatcher.process_once() == 1
    assert published[0]["id"] == "outbox-test"
    with new_session() as session:
        row = session.get(OutboxMessageModel, "outbox-test")
        assert row.status == "PUBLISHED"
        assert row.published_at is not None
    reset_engine()
