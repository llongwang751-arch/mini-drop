from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest

from tests.conftest import NOW
from server.app import sql_repository
from server.app.models import OutboxMessageModel
from server.app.sql_repository import SqlRepository


@pytest.mark.parametrize("operation", ["published", "failed"])
def test_outbox_finalize_locks_out_expired_lease_takeover(postgres_sessions, monkeypatch, operation):
    monkeypatch.setattr(sql_repository, "new_session", postgres_sessions)
    with postgres_sessions.begin() as session:
        session.add(OutboxMessageModel(
            id="outbox-race", aggregate_type="task", aggregate_id="task-race",
            event_type="task.created", payload_json={}, status="DISPATCHING",
            attempts=0, next_attempt_at=NOW, created_at=NOW, updated_at=NOW,
            worker_lease_owner="a", worker_lease_expires_at=NOW + timedelta(seconds=60),
        ))
    first, second = SqlRepository(), SqlRepository()
    checked, resume = Event(), Event()
    original = first._owned_outbox_claim

    def pause_after_read(message, owner, now):
        result = original(message, owner, now)
        checked.set()
        assert resume.wait(5)
        return result

    monkeypatch.setattr(first, "_owned_outbox_claim", pause_after_read)
    def finalize():
        if operation == "published":
            first.mark_outbox_published("outbox-race", "a", now=NOW)
        else:
            first.fail_outbox_message("outbox-race", "a", "retry", now=NOW)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(finalize)
        try:
            assert checked.wait(5)
            # A separate repository/connection tries to take over the now-expired lease.
            claimed = second.claim_outbox_messages("b", now=NOW + timedelta(seconds=120))
        finally:
            resume.set()
        future.result(timeout=5)
    assert claimed == [], "finalization must serialize with SKIP LOCKED lease takeover"
    with postgres_sessions() as session:
        row = session.get(OutboxMessageModel, "outbox-race")
        assert row.status == ("PUBLISHED" if operation == "published" else "FAILED")
        assert row.worker_lease_owner is None
