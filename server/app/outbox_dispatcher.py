"""General transactional outbox dispatcher.

Claims unpublished outbox messages with a worker lease, delivers each to an
idempotent ``deliver`` callback, then acks. Failures are recorded with
exponential backoff and dead-letter after a bounded attempt count, so a
crashing worker cannot lose events (guide §9.6).
"""

from __future__ import annotations

import argparse
import logging
import threading
import time

from server.app.event_bus import notify_diagnosis_artifact_published, notify_task_changed
from server.app.logging_utils import log_event

logger = logging.getLogger(__name__)


def _safe_log(level: str, event: str, **fields) -> None:
    """Keep telemetry failures from interrupting durable outbox progress."""
    try:
        log_event(level, event, **fields)
    except Exception:
        logger.exception("outbox telemetry failed: %s", event)


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
    try:
        messages = repo.claim_outbox_messages(worker_id, limit=limit)
    except Exception:
        logger.exception("task outbox claim failed for worker %s", worker_id)
        return 0
    processed = 0
    for message in messages:
        try:
            deliver(message)
        except Exception as exc:
            try:
                outcome = repo.fail_outbox_message(
                    message.id, str(exc)[:500], max_attempts=max_attempts
                )
            except Exception:
                logger.exception(
                    "task outbox failure persistence failed: %s", message.id
                )
                continue
            _safe_log(
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
            try:
                repo.mark_outbox_published(message.id)
            except Exception:
                logger.exception("task outbox acknowledgement failed: %s", message.id)
                continue
            _safe_log(
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


def dispatch_artifact_once(
    store,
    worker_id: str,
    deliver,
    *,
    limit: int = 10,
    max_attempts: int = 5,
) -> int:
    """Deliver one batch of immutable diagnosis-artifact notifications."""
    try:
        messages = store.claim_artifact_outbox(worker_id, limit=limit)
    except Exception:
        logger.exception("artifact outbox claim failed for worker %s", worker_id)
        return 0
    processed = 0
    for message in messages:
        try:
            deliver(message)
        except Exception as exc:
            try:
                outcome = store.fail_artifact_outbox(
                    message["outbox_id"],
                    worker_id,
                    str(exc)[:500],
                    max_attempts=max_attempts,
                )
            except Exception:
                logger.exception(
                    "artifact outbox failure persistence failed: %s",
                    message["outbox_id"],
                )
                continue
            _safe_log(
                "warning",
                "diagnosis_artifact_delivery_failed",
                outbox_id=message["outbox_id"],
                diagnosis_id=message["diagnosis_id"],
                artifact_id=message["artifact_id"],
                attempts=message["attempts"],
                outcome=outcome,
                error=str(exc)[:200],
            )
        else:
            try:
                store.mark_artifact_outbox_published(
                    message["outbox_id"], worker_id
                )
            except Exception:
                logger.exception(
                    "artifact outbox acknowledgement failed: %s",
                    message["outbox_id"],
                )
                continue
            _safe_log(
                "info",
                "diagnosis_artifact_delivered",
                outbox_id=message["outbox_id"],
                diagnosis_id=message["diagnosis_id"],
                artifact_id=message["artifact_id"],
                artifact_hash=message["artifact_hash"],
            )
        processed += 1
    return processed


def artifact_event_bus_deliver(message: dict) -> None:
    """Publish artifact readiness without exposing its canonical payload."""
    notify_diagnosis_artifact_published(
        message["diagnosis_id"],
        message["artifact_id"],
        message["artifact_hash"],
    )


def run_artifact_worker(
    store,
    worker_id: str,
    *,
    poll_seconds: float = 5.0,
    once: bool = False,
    stop_event: threading.Event | None = None,
) -> None:
    while stop_event is None or not stop_event.is_set():
        count = dispatch_artifact_once(
            store, worker_id, artifact_event_bus_deliver
        )
        if once:
            return
        if count == 0:
            if stop_event is not None:
                stop_event.wait(poll_seconds)
            else:
                time.sleep(poll_seconds)


def run_worker(
    repo,
    worker_id: str,
    *,
    poll_seconds: float = 5.0,
    once: bool = False,
    stop_event: threading.Event | None = None,
) -> None:
    while stop_event is None or not stop_event.is_set():
        count = dispatch_once(repo, worker_id, _default_deliver)
        if once:
            return
        if count == 0:
            if stop_event is not None:
                stop_event.wait(poll_seconds)
            else:
                time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Transactional outbox dispatcher")
    parser.add_argument("--worker-id", default="outbox-worker-1")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--once", action="store_true", help="dispatch one batch then exit")
    parser.add_argument(
        "--artifact-outbox",
        action="store_true",
        help="dispatch frozen diagnosis artifact notifications",
    )
    args = parser.parse_args()

    from server.app.database import init_db
    from server.app.sql_repository import SqlRepository

    init_db()
    if args.artifact_outbox:
        from server.app.diagnosis.store import DiagnosisStore

        run_artifact_worker(
            DiagnosisStore(),
            args.worker_id,
            poll_seconds=args.poll_seconds,
            once=args.once,
        )
    else:
        repo = SqlRepository()
        run_worker(repo, args.worker_id, poll_seconds=args.poll_seconds, once=args.once)


if __name__ == "__main__":
    main()
