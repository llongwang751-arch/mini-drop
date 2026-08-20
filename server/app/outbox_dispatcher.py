"""General transactional outbox dispatcher.

Claims unpublished outbox messages with a worker lease, delivers each to an
idempotent ``deliver`` callback, then acks. Failures are recorded with
exponential backoff and dead-letter after a bounded attempt count, so a
crashing worker cannot lose events (guide §9.6).
"""

from __future__ import annotations

import argparse
import logging
import time

from server.app.event_bus import notify_task_changed
from server.app.logging_utils import log_event

logger = logging.getLogger(__name__)


def dispatch_once(
    repo,
    worker_id: str,
    deliver,
    *,
    limit: int = 10,
    max_attempts: int = 5,
) -> int:
    """Deliver one batch of outbox messages.

    ``deliver(message)`` must be idempotent and raise on failure. Returns the
    number of messages processed. Each delivery either acks the message or
    records a failure (backoff / dead-letter) — never drops it silently.
    """
    messages = repo.claim_outbox_messages(worker_id, limit=limit)
    processed = 0
    for message in messages:
        try:
            deliver(message)
        except Exception as exc:
            outcome = repo.fail_outbox_message(
                message.id, str(exc)[:500], max_attempts=max_attempts
            )
            log_event(
                "warning",
                "outbox_delivery_failed",
                message_id=message.id,
                aggregate_type=message.aggregate_type,
                aggregate_id=message.aggregate_id,
                event_type=message.event_type,
                attempts=message.attempts,
                outcome=outcome,
                error=str(exc)[:200],
            )
        else:
            repo.mark_outbox_published(message.id)
            log_event(
                "info",
                "outbox_delivered",
                message_id=message.id,
                aggregate_type=message.aggregate_type,
                aggregate_id=message.aggregate_id,
                event_type=message.event_type,
            )
        processed += 1
    return processed


def event_bus_deliver(message) -> None:
    """Deliver outbox events to the in-process SSE event bus.

    This is the concrete downstream for guide §9.6: a ``task.created`` message
    is fanned out as a ``task_changed`` SSE event. Run the dispatcher inside
    the server process (``MINI_DROP_OUTBOX_DISPATCH_ENABLED=1``) so Web SSE
    subscribers receive it; a standalone worker simply has no subscribers, so
    delivery is a durable audit point. Unknown events are logged, not dropped.
    """
    if message.aggregate_type == "task" and message.event_type == "task.created":
        notify_task_changed(
            message.aggregate_id,
            None,
            "PENDING",
            "outbox:task.created",
        )
        return
    log_event(
        "info",
        "outbox_deliver_unknown",
        message_id=message.id,
        aggregate_type=message.aggregate_type,
        aggregate_id=message.aggregate_id,
        event_type=message.event_type,
    )


def _default_deliver(message) -> None:
    """Default publisher: fan task-created events out to the SSE event bus."""
    event_bus_deliver(message)


def run_worker(repo, worker_id: str, *, poll_seconds: float = 5.0, once: bool = False) -> None:
    while True:
        count = dispatch_once(repo, worker_id, _default_deliver)
        if once:
            return
        if count == 0:
            time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Transactional outbox dispatcher")
    parser.add_argument("--worker-id", default="outbox-worker-1")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--once", action="store_true", help="dispatch one batch then exit")
    args = parser.parse_args()

    from server.app.database import init_db
    from server.app.sql_repository import SqlRepository

    init_db()
    repo = SqlRepository()
    run_worker(repo, args.worker_id, poll_seconds=args.poll_seconds, once=args.once)


if __name__ == "__main__":
    main()
