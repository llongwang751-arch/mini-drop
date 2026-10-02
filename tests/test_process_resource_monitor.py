from types import SimpleNamespace
import json
import threading

import pytest

from scripts import process_resource_monitor as resources


def rows(count=30, **changes):
    return [{"status": "OK", "phase": "soak", "elapsed_seconds": i,
             "rss_bytes": 10 * 1024 * 1024, "threads": 4, "handles_or_fds": 12,
             "handle_kind": "posix_file_descriptors", "cpu_percent": None if i == 0 else 10,
             **changes} for i in range(count)]


def test_stable_resources_pass_and_first_cpu_sample_is_not_fabricated():
    result = resources.summarize_resources(rows(), 30)
    assert result["status"] == "PASSED"
    assert result["cpu_peak_percent"] == 10
    assert result["soak_trends"]["rss_bytes"]["growth"] == 0


@pytest.mark.parametrize("field,delta,reason", [
    ("rss_bytes", 33 * 1024 * 1024, "RSS_BYTES_GROWTH"),
    ("threads", 9, "THREADS_GROWTH"), ("handles_or_fds", 33, "HANDLES_OR_FDS_GROWTH"),
])
def test_sustained_growth_cannot_pass(field, delta, reason):
    samples = rows()
    for row in samples[-10:]:
        row[field] += delta
    result = resources.summarize_resources(samples, 30)
    assert result["status"] == "GROWTH_DETECTED"
    assert reason in result["reasons"]


def test_threshold_is_inclusive_and_transient_peak_is_not_leak_proof():
    samples = rows()
    samples[15]["rss_bytes"] += 100 * 1024 * 1024
    for row in samples[-10:]:
        row["rss_bytes"] += 32 * 1024 * 1024
    result = resources.summarize_resources(samples, 30)
    assert result["status"] == "PASSED"
    assert result["soak_trends"]["rss_bytes"]["peak"] == 110 * 1024 * 1024


@pytest.mark.parametrize("samples", [[], rows(4), rows(23), rows() + [{"status": "MISSING"}]])
def test_missing_or_sparse_resources_are_invalid(samples):
    assert resources.summarize_resources(samples, 30)["status"] == "INVALID"


def test_sampler_gap_is_invalid_even_with_enough_samples():
    samples = rows()
    for row in samples[15:]:
        row["elapsed_seconds"] += 10
    assert "RESOURCE_SAMPLING_GAP" in resources.summarize_resources(samples, 30)["reasons"]


def test_sampler_gap_across_phase_boundary_is_not_hidden_by_full_soak_count():
    samples = rows()
    for row in samples:
        row["elapsed_seconds"] += 60
    samples.insert(0, {**rows(1)[0], "phase": "recovery"})
    result = resources.summarize_resources(samples, 30)
    assert result["status"] == "INVALID"
    assert "RESOURCE_SAMPLING_GAP" in result["reasons"]


def test_delayed_first_resource_sample_cannot_hide_sampler_startup_gap():
    samples = rows()
    for row in samples:
        row["elapsed_seconds"] += 20
    assert resources.summarize_resources(samples, 30)["status"] == "INVALID"


class Process:
    def __init__(self, pid):
        self.pid = pid
        self.created = 100
        self.cpu = 1

    def create_time(self): return self.created
    def is_running(self): return True
    def oneshot(self): return self
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def cpu_times(self): return SimpleNamespace(user=self.cpu, system=0)
    def memory_info(self): return SimpleNamespace(rss=1024)
    def num_threads(self): return 2
    def num_fds(self): return 3


def test_cpu_delta_uses_target_clock_and_pid_reuse_is_missing(tmp_path, monkeypatch):
    process = Process(42)
    now = [10.0]
    monkeypatch.setattr(resources.psutil, "Process", lambda pid: process)
    monkeypatch.setattr(resources.time, "perf_counter", lambda: now[0])
    monitor = resources.ResourceMonitor(42, tmp_path / "resources.jsonl")
    first = monitor.sample()
    assert first["cpu_percent"] is None
    assert first["handle_kind"] == "posix_file_descriptors"
    process.cpu = 3
    now[0] += 1
    assert monitor.sample()["cpu_percent"] == 200
    process.created += 1
    sample = monitor.sample()
    assert sample["status"] == "MISSING" and "rss_bytes" not in sample


def test_access_denial_is_missing_not_zero(tmp_path, monkeypatch):
    process = Process(42)
    monkeypatch.setattr(resources.psutil, "Process", lambda pid: process)
    monitor = resources.ResourceMonitor(42, tmp_path / "resources.jsonl")

    def denied():
        raise resources.psutil.AccessDenied(42)

    process.memory_info = denied
    sample = monitor.sample()
    assert sample["status"] == "MISSING" and sample["error"] == "AccessDenied"
    assert "rss_bytes" not in sample and "cpu_percent" not in sample


def test_windows_handles_are_named_separately(tmp_path, monkeypatch):
    process = Process(42)
    process.num_handles = lambda: 23
    monkeypatch.setattr(resources.psutil, "Process", lambda pid: process)
    sample = resources.ResourceMonitor(42, tmp_path / "resources.jsonl").sample()
    assert sample["handle_kind"] == "windows_handles" and sample["handles_or_fds"] == 23


def test_monitor_flushes_evidence_and_joins_sampler_on_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(resources.psutil, "Process", Process)
    destination = tmp_path / "resources.jsonl"
    monitor = resources.ResourceMonitor(42, destination, interval=60)
    observed = threading.Event()
    original = monitor.sample

    def sample():
        value = original()
        observed.set()
        return value

    monitor.sample = sample
    with monitor:
        assert observed.wait(timeout=2)
    assert not monitor.thread.is_alive() and monitor.stream.closed
    saved = [json.loads(line) for line in destination.read_text().splitlines()]
    assert saved and saved[0]["pid"] == 42 and saved[0]["status"] == "OK"


def test_resource_write_failure_is_not_silently_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(resources.psutil, "Process", Process)
    monitor = resources.ResourceMonitor(42, tmp_path / "unused")
    attempted = threading.Event()

    class BrokenStream:
        closed = False

        def write(self, value):
            attempted.set()
            raise OSError("disk unavailable")

        def close(self):
            self.closed = True

    stream = BrokenStream()
    monitor.output = SimpleNamespace(open=lambda *args, **kwargs: stream)
    with pytest.raises(RuntimeError, match="evidence write failed"):
        with monitor:
            assert attempted.wait(timeout=2)
    assert not monitor.thread.is_alive() and stream.closed
