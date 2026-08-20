from __future__ import annotations

from datetime import datetime, timezone

import pytest

from server.app.cron import CronSchedule, next_after


def _utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_every_minute_fires_next_minute():
    result = next_after("* * * * *", _utc(2026, 8, 6, 10, 0, 30))
    assert result == _utc(2026, 8, 6, 10, 1, 0)


def test_hourly_top_of_hour():
    result = next_after("0 * * * *", _utc(2026, 8, 6, 10, 15))
    assert result == _utc(2026, 8, 6, 11, 0)


def test_step_minutes():
    result = next_after("*/15 * * * *", _utc(2026, 8, 6, 10, 10))
    assert result == _utc(2026, 8, 6, 10, 15)


def test_daily_at_0300():
    result = next_after("0 3 * * *", _utc(2026, 8, 6, 10, 0))
    assert result == _utc(2026, 8, 7, 3, 0)


def test_weekly_monday_0900():
    # 2026-08-06 is a Thursday. Next Monday is 2026-08-10.
    result = next_after("0 9 * * 1", _utc(2026, 8, 6, 12, 0))
    assert result == _utc(2026, 8, 10, 9, 0)


def test_monthly_range_and_list():
    result = next_after("0 0 1,15 * *", _utc(2026, 8, 2))
    assert result == _utc(2026, 8, 15, 0, 0)


def test_invalid_expression_rejected():
    with pytest.raises(ValueError):
        CronSchedule("0 3 * *")  # only 4 fields
    with pytest.raises(ValueError):
        CronSchedule("61 * * * *")  # minute out of range
    with pytest.raises(ValueError):
        CronSchedule("bad * * * *")
