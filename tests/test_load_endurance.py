"""Independent load accounting and verdict regressions; HTTP smoke runs in CI."""
from dataclasses import replace
import json
from urllib.error import HTTPError

import pytest

from scripts import run_load_endurance as load


def request_row(index=0, **changes):
    return {"index": index, "scheduled_offset_seconds": index / 5, "sent": True,
            "latency_ms": 20, "dispatch_lag_ms": 0, "success": True,
            "quality_passed": True, **changes}


def stage(rate=5, status="PASSED", buckets=None):
    return {"rate": rate, "summary": {"status": status}, "buckets": buckets or []}


@pytest.mark.parametrize("change", [
    {"rates": ()}, {"rates": (10, 5)}, {"rates": (5, 5)}, {"rates": (501,)},
    {"soak_seconds": 3601}, {"soak_seconds": True}, {"concurrency": 129},
    {"timeout_seconds": 11}, {"timeout_seconds": float("nan")},
    {"minimum_success_rate": float("nan")}, {"minimum_quality_rate": 0},
    {"p95_limit_ms": -1}, {"dependency_latency_ms": 101},
    {"soak_rate": 500, "soak_seconds": 3600},
])
def test_plan_refuses_unbounded_or_nonfinite_load_before_creating_evidence(tmp_path, change):
    destination = tmp_path / "never-created"
    with pytest.raises(ValueError):
        load.run(destination, replace(load.Plan(), **change))
    assert not destination.exists()


def test_unsent_work_stays_in_quality_denominator_and_has_no_fake_latency():
    rows = [request_row(i) for i in range(99)]
    rows.append(request_row(99, sent=False, latency_ms=None, success=False, quality_passed=False))
    summary = load.summarize(rows, load.Plan(), 10)
    assert summary["status"] == "INVALID"
    assert summary["reasons"] == ["CLIENT_INFLIGHT_LIMIT"]
    assert summary["success_rate"] == .99
    assert summary["quality_rate"] == .99
    assert summary["offered"] == 100 and summary["sent"] == 99
    assert summary["p95_ms"] == 20 and summary["p99_ms"] is None


def test_failed_attempts_remain_in_tail_latency_and_success_denominator():
    rows = [request_row(i) for i in range(90)]
    rows += [request_row(i, success=False, quality_passed=False, latency_ms=5000) for i in range(90, 100)]
    summary = load.summarize(rows, load.Plan(), 10)
    assert summary["status"] == "SLO_FAILED"
    assert summary["p95_ms"] == 5000 and summary["p99_ms"] == 5000
    assert summary["success_rate"] == .9
    assert set(summary["reasons"]) == {"SUCCESS_RATE", "QUALITY_RATE", "P95_LIMIT"}


def test_http_success_cannot_hide_wrong_answers():
    rows = [request_row(i, quality_passed=False) for i in range(30)]
    summary = load.summarize(rows, load.Plan(), 6)
    assert summary["status"] == "SLO_FAILED" and summary["reasons"] == ["QUALITY_RATE"]


def test_exact_latency_floor_passes_but_late_dispatch_invalidates_capacity():
    rows = [request_row(i, latency_ms=200, dispatch_lag_ms=250) for i in range(30)]
    assert load.summarize(rows, load.Plan(), 6)["status"] == "PASSED"
    rows[-1]["dispatch_lag_ms"] = 250.001
    assert load.summarize(rows, load.Plan(), 6)["status"] == "INVALID"


@pytest.mark.parametrize("count", [0, 29])
def test_insufficient_samples_never_pass(count):
    summary = load.summarize([request_row(i) for i in range(count)], load.Plan(), 6)
    assert summary["status"] == "INVALID"
    assert "INSUFFICIENT_SAMPLES" in summary["reasons"]


def test_nearest_rank_percentile_has_independent_small_sample_oracle():
    assert load.percentile([], .95) is None
    assert load.percentile(list(range(1, 101)), .95) == 95
    assert load.percentile(list(range(1, 101)), .99) == 99


def test_capacity_uses_contiguous_passing_prefix_not_best_result():
    stages = [stage(5), stage(20, "SLO_FAILED"), stage(40)]
    result = load.capacity_summary(stages)
    assert result["highest_contiguous_passing_rps"] == 5
    assert result["first_nonpassing_rps"] == 20
    assert result["boundary"] == "SLO_FAILED_AT_TESTED_RATE"
    stages[1]["summary"]["status"] = "INVALID"
    assert load.capacity_summary(stages)["boundary"] == "GENERATOR_OR_SAMPLE_LIMIT"
    assert load.capacity_summary([stage()])["boundary"] == "NOT_REACHED"


def test_overload_is_expected_but_recovery_and_every_soak_bucket_must_pass():
    stages = [stage(5), stage(80, "SLO_FAILED"), stage(), stage(buckets=[stage()])]
    assert load.campaign_status(stages) == "PASSED"
    stages[-1]["buckets"].append(stage(status="SLO_FAILED"))
    assert load.campaign_status(stages) == "FAILED"
    stages[-1]["buckets"][-1]["summary"]["status"] = "INVALID"
    assert load.campaign_status(stages) == "INVALID"
    stages[-1]["buckets"] = [stage()]
    stages[-2]["summary"]["status"] = "SLO_FAILED"
    assert load.campaign_status(stages) == "FAILED"


def test_generator_saturation_cannot_be_green_even_if_recovery_passes():
    assert load.campaign_status([stage(), stage(80, "INVALID"), stage(), stage()]) == "INVALID"


def test_bounded_scheduler_records_offered_slots_without_unbounded_future_queue(tmp_path, monkeypatch):
    class Clock:
        now = 100.0

        def sleep(self, seconds):
            self.now += seconds

    class Future:
        def __init__(self, index):
            self.index = index

        def done(self):
            return False

        def result(self):
            return request_row(self.index)

    class Pool:
        def __init__(self, max_workers):
            assert max_workers == 2

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def submit(self, fn, endpoint, index, *args):
            return Future(index)

    clock = Clock()
    monkeypatch.setattr(load.time, "perf_counter", lambda: clock.now)
    monkeypatch.setattr(load.time, "sleep", clock.sleep)
    monkeypatch.setattr(load, "ThreadPoolExecutor", Pool)
    result = load.measure("unused", "bounded", 4, 1, replace(load.Plan(), concurrency=2), tmp_path)
    rows = [json.loads(line) for line in (tmp_path / "bounded.jsonl").read_text().splitlines()]
    assert len(rows) == 4 and len({r["index"] for r in rows}) == 4
    assert result["summary"]["sent"] == 2 and result["summary"]["unsent"] == 2
    assert result["summary"]["status"] == "INVALID"


def test_http_error_is_preserved_without_retry(monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise HTTPError("http://127.0.0.1/query", 503, "unavailable", {}, None)

    monkeypatch.setattr(load, "urlopen", fail)
    now = load.time.perf_counter()
    row = load.request("http://127.0.0.1/query", 0, now, now, 1)
    assert calls == [1] and row["http_status"] == 503
    assert row["success"] is False and row["error"] == "HTTP_ERROR"


def test_campaign_error_preserves_failed_manifest(tmp_path, monkeypatch):
    def broken_fixture(*args):
        raise RuntimeError("fixture failed")

    monkeypatch.setattr(load, "fixture", broken_fixture)
    out = tmp_path / "failed"
    with pytest.raises(RuntimeError):
        load.run(out, load.Plan())
    report = json.loads((out / "report.json").read_text())
    assert report["status"] == "FAILED" and report["error"] == "RuntimeError: fixture failed"
    assert report["finished_at"] and report["sha256"]
    with pytest.raises(FileExistsError):
        load.run(out, load.Plan())


@pytest.mark.parametrize("boundary_offset", [5.999999999999993, 6.0, 6.000000000000007])
def test_bucket_membership_uses_planned_slot_not_clock_subtraction(tmp_path, monkeypatch, boundary_offset):
    class Clock:
        now = 58.0
        def sleep(self, seconds):
            self.now += seconds

    class Future:
        def __init__(self, index):
            self.index = index
        def done(self):
            return True
        def result(self):
            return request_row(self.index, scheduled_offset_seconds=(
                boundary_offset if self.index == 30 else self.index / 5))

    class Pool:
        def __init__(self, max_workers):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def submit(self, fn, endpoint, index, *args):
            return Future(index)

    clock = Clock()
    monkeypatch.setattr(load.time, "perf_counter", lambda: clock.now)
    monkeypatch.setattr(load.time, "sleep", clock.sleep)
    monkeypatch.setattr(load, "ThreadPoolExecutor", Pool)
    result = load.measure("unused", "soak", 5, 12, replace(load.Plan(), bucket_seconds=6), tmp_path)
    rows = [json.loads(line) for line in (tmp_path / "soak.jsonl").read_text().splitlines()]
    assert [bucket["summary"]["offered"] for bucket in result["buckets"]] == [30, 30]
    assert all(bucket["summary"]["status"] == "PASSED" for bucket in result["buckets"])
    assert all(row["scheduled_offset_seconds"] == row["index"] / 5 for row in rows)
