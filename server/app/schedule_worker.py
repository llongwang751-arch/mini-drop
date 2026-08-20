"""Schedule/cron worker: materializes tasks from due schedule templates.

Claims due schedules (FOR UPDATE SKIP LOCKED), fires each in a single
transaction (task + schedule_record slot + next_run advance), and records
failures. A unique (schedule_id, scheduled_at) slot plus the row lock keeps
concurrent workers from firing the same minute twice.
"""

from __future__ import annotations

import argparse
import time
from datetime import timedelta

from sqlalchemy.exc import IntegrityError

from server.app.cron import next_schedule_fire
from server.app.logging_utils import log_event
from server.app.prometheus_metrics import record_schedule_fire
from server.app.schemas import CreateTaskRequest
from server.app.state_machine import now_utc


def _fire(repo, schedule, now) -> None:
    scheduled_at = schedule.next_run_at
    next_run = next_schedule_fire(
        schedule.cron_expression, schedule.timezone, scheduled_at
    )
    template = schedule.task_template_json or {}
    payload = CreateTaskRequest(**template)
    repo.fire_schedule(
        schedule,
        scheduled_at=scheduled_at,
        next_run_at=next_run,
        payload=payload,
    )
    record_schedule_fire("fired")
    log_event(
        "info",
        "schedule_fired",
        schedule_id=schedule.id,
        scheduled_at=scheduled_at.isoformat(),
        task_id=payload.name,
    )


def run_due_schedules(repo, *, limit: int = 5, now=None) -> int:
    now = now or now_utc()
    schedules = repo.claim_due_schedules(limit=limit, now=now)
    fired = 0
    for schedule in schedules:
        try:
            _fire(repo, schedule, now)
            fired += 1
        except IntegrityError:
            # Another worker won the same slot; nothing to roll back (the
            # transaction already aborted), just move past this fire time.
            log_event(
                "info",
                "schedule_slot_already_fired",
                schedule_id=schedule.id,
            )
            repo.advance_schedule_next_run(schedule.id, now=now)
        except Exception as exc:
            record_schedule_fire("failed")
            log_event(
                "warning",
                "schedule_fire_failed",
                schedule_id=schedule.id,
                error=str(exc)[:200],
            )
            repo.record_schedule_run(
                schedule.id,
                schedule.next_run_at,
                status="failed",
                error=str(exc)[:500],
            )
            # Advance so a permanent failure does not hot-loop every poll.
            repo.advance_schedule_next_run(schedule.id, now=now)
    return fired


def run_worker(repo, *, poll_seconds: float = 10.0, once: bool = False) -> None:
    while True:
        count = run_due_schedules(repo)
        if once:
            return
        if count == 0:
            time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Schedule/cron worker")
    parser.add_argument("--poll-seconds", type=float, default=10.0)
    parser.add_argument("--once", action="store_true", help="fire due schedules once")
    args = parser.parse_args()

    from server.app.database import init_db
    from server.app.sql_repository import SqlRepository

    init_db()
    repo = SqlRepository()
    run_worker(repo, poll_seconds=args.poll_seconds, once=args.once)


if __name__ == "__main__":
    main()
