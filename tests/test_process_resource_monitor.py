from types import SimpleNamespace

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
