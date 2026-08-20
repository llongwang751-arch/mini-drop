"""Minimal 5-field cron parser and next-fire computation.

Supports the standard fields ``minute hour day-of-month month day-of-week``
with ``*``, lists (``a,b``), ranges (``a-b``) and steps (``*/n`` / ``a-b/n``).
Day-of-week uses 0=Sunday..6=Saturday. Kept dependency-free so the schedule
worker and the web API never need ``croniter``.

Semantics simplification: when BOTH day-of-month and day-of-week are
restricted, this parser requires BOTH to match (some cron dialects OR them).
For the common ``*``-in-one-field cases the behaviour is identical.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _parse_field(text: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"empty cron field part in {text!r}")
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
            if step <= 0:
                raise ValueError(f"invalid step in cron field {text!r}")
        if part in {"", "*"}:
            base_low, base_high = low, high
        elif "-" in part:
            left, right = part.split("-", 1)
            base_low, base_high = int(left), int(right)
        else:
            base_low = base_high = int(part)
        for value in range(base_low, base_high + 1, step):
            if low <= value <= high:
                values.add(value)
    if not values:
        raise ValueError(f"invalid cron field {text!r}")
    return values


def _next_in_set(values: set[int], current: int) -> tuple[int, bool]:
    """Return (smallest value >= current, wrapped) from a non-empty set."""
    ordered = sorted(values)
    for value in ordered:
        if value >= current:
            return value, False
    return ordered[0], True


class CronSchedule:
    def __init__(self, expression: str):
        fields = expression.split()
        if len(fields) != 5:
            raise ValueError(
                f"cron expression must have 5 fields, got {len(fields)}: {expression!r}"
            )
        self.minutes = _parse_field(fields[0], 0, 59)
        self.hours = _parse_field(fields[1], 0, 23)
        self.days = _parse_field(fields[2], 1, 31)
        self.months = _parse_field(fields[3], 1, 12)
        # Cron day-of-week uses 0=Sunday..6=Saturday; Python weekday() uses
        # 0=Monday..6=Sunday. Convert to Python's convention at parse time.
        self.dows = {
            (value - 1) % 7 for value in _parse_field(fields[4], 0, 6)
        }

    def next_after(self, moment: datetime) -> datetime:
        """Return the first matching datetime strictly after ``moment``."""
        candidate = moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(1_000_000):
            if candidate.month not in self.months:
                candidate = _jump_month(candidate)
                continue
            if not (candidate.day in self.days and candidate.weekday() in self.dows):
                candidate = _jump_day(candidate)
                continue
            if candidate.hour not in self.hours:
                hour, wrapped = _next_in_set(self.hours, candidate.hour)
                candidate = candidate.replace(hour=hour, minute=min(self.minutes))
                if wrapped:
                    candidate = _jump_day(candidate).replace(
                        hour=hour, minute=min(self.minutes)
                    )
                continue
            if candidate.minute not in self.minutes:
                minute, wrapped = _next_in_set(self.minutes, candidate.minute)
                candidate = candidate.replace(minute=minute)
                if wrapped:
                    candidate += timedelta(hours=1)
                    candidate = candidate.replace(minute=minute)
                continue
            return candidate
        raise ValueError("no matching cron fire within search cap")


def _jump_day(moment: datetime) -> datetime:
    return (moment + timedelta(days=1)).replace(hour=0, minute=0)


def _jump_month(moment: datetime) -> datetime:
    if moment.month == 12:
        return datetime(moment.year + 1, 1, 1, 0, 0)
    return datetime(moment.year, moment.month + 1, 1, 0, 0)


def next_after(expression: str, moment: datetime) -> datetime:
    return CronSchedule(expression).next_after(moment)


def next_schedule_fire(
    expression: str,
    timezone_name: str,
    after: datetime,
) -> datetime:
    """Compute the next cron fire time in the schedule's timezone.

    The cron expression is interpreted in ``timezone_name`` and the returned
    datetime is normalized to UTC for storage.
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(timezone_name)
    local_after = after.astimezone(tz)
    next_local = CronSchedule(expression).next_after(local_after)
    return next_local.astimezone(timezone.utc)
