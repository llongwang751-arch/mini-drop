"""Counter-window regressions; fixture bytes are not live fault acceptance."""

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from server.app.metric_analyzers import (
    _parse_sys_metrics_document,
    summarize_application_metric_window,
)


if os.getenv("MINI_DROP_TEST_METRIC_SOURCE"):
    _legacy_spec = spec_from_file_location("seven_gap_legacy_metrics", os.environ["MINI_DROP_TEST_METRIC_SOURCE"])
    _legacy = module_from_spec(_legacy_spec)
    _legacy_spec.loader.exec_module(_legacy)
    _parse_sys_metrics_document = _legacy._parse_sys_metrics_document
    summarize_application_metric_window = _legacy.summarize_application_metric_window


def _document(throttled=True):
    samples = []
    for index in range(3):
        observation = {
            "schema_version": "mini-drop.cgroup-cpu-observation.v1",
            "source": "linux_proc_and_cgroup_v2",
            "target_pid": 42, "target_start_ticks": 77,
            "target_cpu_ticks": 100 + index * 30,
            "boot_id": "test-boot", "cgroup_path": "/isolated/test",
            "cpu_quota_us": 65_000, "cpu_period_us": 100_000,
            "nr_periods": 100 + index * 10,
            "nr_throttled": 10 + (index * 8 if throttled else 0),
            "throttled_usec": 1000 + (index * 500_000 if throttled else 0),
            "peers": [{"pid": 43, "start_ticks": 80,
                       "cpu_ticks": 20 + index * 20, "cgroup_path": "/isolated/test"}],
        }
        samples.append({
            "offset_sec": index, "captured_at_unix_ms": 1000 + index * 1000,
            "process_start_ticks": 77, "process_cpu_ticks": observation["target_cpu_ticks"],
            "resource_competition_json": json.dumps(observation),
        })
    return {"schema_version": "sys_metrics.v2", "pid": 42,
            "clock_ticks_per_second": 100, "samples": samples}


def test_native_shared_quota_window_observes_two_consumers_and_actual_throttling():
    result = _parse_sys_metrics_document(_document())
    signal = result["signals"]["noisy_neighbor"]
    metrics = signal["metrics"]
    assert signal["measurement_scope"] == "TARGET_SHARED_CGROUP_CPU"
    assert metrics["peer_cpu_ticks_delta"] == 40
    assert metrics["target_cpu_ticks_delta"] == 60
    assert metrics["throttled_periods_delta"] == 16
    assert metrics["throttled_usec_delta"] == 1_000_000
    assert signal["detected"] is True
    assert result["resource_competition_window"]["before"]["peers"][0]["start_ticks"] == 80


def test_complete_zero_throttling_is_a_measurement_and_not_missing():
    result = _parse_sys_metrics_document(_document(False))
    assert result["signals"]["noisy_neighbor"]["detected"] is False
    assert result["signals"]["noisy_neighbor"]["metrics"]["throttled_usec_delta"] == 0


@pytest.mark.parametrize("mutation", [
    lambda row: row.update(cpu_quota_us=None),
    lambda row: row.update(cpu_quota_us=True),
    lambda row: row.update(cpu_period_us=0),
    lambda row: row.update(target_start_ticks=999),
    lambda row: row.update(target_cpu_ticks=999),
    lambda row: row.update(cgroup_path="/different"),
    lambda row: row.update(nr_throttled=999),
    lambda row: row.update(throttled_usec=0),
    lambda row: row.update(peers=[]),
    lambda row: row["peers"][0].update(start_ticks=999),
    lambda row: row["peers"][0].update(cpu_ticks=0),
    lambda row: row["peers"][0].update(cgroup_path="/different"),
])
def test_missing_changed_reset_or_different_cgroup_is_unknown(mutation):
    document = _document()
    row = json.loads(document["samples"][1]["resource_competition_json"])
    mutation(row)
    document["samples"][1]["resource_competition_json"] = json.dumps(row)
    result = _parse_sys_metrics_document(document)
    assert result.get("resource_competition_window") is None
    assert "noisy_neighbor" not in result["signals"]
    assert result["process_cpu_window"]["process_cpu_core_usage"] == 30


def test_application_peer_active_cpu_ticks_cannot_claim_resource_competition():
    before = {"schema_version": "mini-drop.application-metrics.v1", "host_pid": 42,
              "peer_pid": 43, "peer_cpu_ticks": 10, "same_host_verified": True,
              "noisy_neighbor_active": True}
    _, signals = summarize_application_metric_window(before, {**before, "peer_cpu_ticks": 1000})
    assert "noisy_neighbor" not in signals


def test_acquisition_wait_mean_uses_new_counter_window_and_keeps_zero_known():
    before = {"schema_version": "mini-drop.application-metrics.v1", "host_pid": 42,
              "lock_acquisitions": 10, "lock_wait_ms": 50, "lock_contentions": 3}
    _, signals = summarize_application_metric_window(before, {
        **before, "lock_acquisitions": 20, "lock_wait_ms": 80, "lock_contentions": 10})
    assert signals["lock_contention"]["metrics"]["average_wait_ms"] == 3
    assert signals["lock_contention"]["metrics"]["lock_contentions_delta"] == 7
    _, zero = summarize_application_metric_window(before, {**before, "lock_acquisitions": 20})
    assert zero["lock_contention"]["metrics"]["average_wait_ms"] == 0
    assert zero["lock_contention"]["detected"] is False


def test_python_sync_io_counts_close_duration_but_excludes_pacing_and_rotates(tmp_path, monkeypatch):
    monkeypatch.setenv("CPU_HOTSPOT_ACTIVE", "0")
    root = Path(__file__).parents[1]
    path = Path(os.getenv("MINI_DROP_TEST_PYTHON_HOTSPOT_SOURCE", root / "demo/python-hotspot/app.py"))
    spec = spec_from_file_location("seven_gap_python_demo", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    # Windows lacks fdatasync; fsync still performs an actual file flush.
    monkeypatch.setattr(module.os, "fdatasync", os.fsync, raising=False)
    ticks = iter(range(0, 10**12, 5_000_000))
    monkeypatch.setattr(module, "time", SimpleNamespace(
        monotonic=time.monotonic, time=time.time, sleep=time.sleep, process_time=time.process_time,
        perf_counter_ns=lambda: next(ticks),
    ))
    fault = module.IoFault()
    fault._path = str(tmp_path / "isolated-sync-io.bin")
    Path(fault._path).write_bytes(b"x" * (8 * 1024 * 1024 - 1))
    try:
        fault.start(15)
        deadline = time.monotonic() + 5
        while fault.snapshot().get("io_operations", 0) < 3 and time.monotonic() < deadline:
            time.sleep(.01)
        snapshot = fault.snapshot()
        assert snapshot["io_operations"] >= 3
        assert snapshot["io_operation_duration_ms_total"] == snapshot["io_operations"] * 5
        assert snapshot["io_bytes_written"] == snapshot["io_operations"] * len(b"mini-drop-io" * 8192)
        assert snapshot["io_failures"] == 0
        assert Path(fault._path).stat().st_size < 1024 * 1024
        app = module._application_metrics_snapshot()
        assert "io_operation_duration_ms_total" in app
        assert "io_fault_active" not in app
    finally:
        fault.stop()
        module.NOISY_FAULT.stop()
    assert not Path(fault._path).exists()
