"""Lease-based dispatcher for the shared transactional outbox."""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable

from sqlalchemy import text

from server.app.database import new_session
from server.app.logging_utils import log_event
from server.app.sql_repository import SqlRepository


OutboxSink = Callable[[dict], None]


def _postgres_notify(message: dict) -> None:
    envelope = {
        "message_id": message["id"],
        "aggregate_type": message["aggregate_type"],
        "aggregate_id": message["aggregate_id"],
        "event_type": message["event_type"],
        "payload": message.get("payload") or {},
    }
    payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > 7000:
        envelope["payload"] = {"truncated": True, "read_from_outbox": True}
        payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    with new_session() as session:
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            session.execute(
                text("SELECT pg_notify('mini_drop_events', :payload)"),
                {"payload": payload},
            )
            session.commit()
        else:
            # SQLite is a local/test deployment with no LISTEN/NOTIFY. Logging
            # preserves a visible sink while the row still follows the same
            # lease and acknowledgement protocol.
            log_event("info", "outbox_event_published", **envelope)


class OutboxDispatcher:
    def __init__(
        self,
        *,
        repository: SqlRepository | None = None,
        sink: OutboxSink | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.repository = repository or SqlRepository()
        self.sink = sink or _postgres_notify
        self.worker_id = worker_id or (
            f"outbox-{socket.gethostname()}-{os.getpid()}"
        )

    def process_once(self, limit: int = 50) -> int:
        claimed = self.repository.claim_outbox_messages(
            self.worker_id,
            limit=limit,
            lease_seconds=60,
        )
        published = 0
        for model in claimed:
            message = {
                "id": model.id,
                "aggregate_type": model.aggregate_type,
                "aggregate_id": model.aggregate_id,
                "event_type": model.event_type,
                "payload": model.payload_json or {},
            }
            try:
                self.sink(message)
                self.repository.mark_outbox_published(model.id, self.worker_id)
                published += 1
            except Exception as exc:
                status = self.repository.fail_outbox_message(
                    model.id,
                    self.worker_id,
                    f"{type(exc).__name__}: {exc}",
                )
                log_event(
                    "error",
                    "outbox_dispatch_failed",
                    message_id=model.id,
                    status=status,
                    error=type(exc).__name__,
                    detail=str(exc),
                )
        return published
