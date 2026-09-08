from __future__ import annotations

import json

import pytest

from server.app.metric_analyzers import (
    analyze_ebpf_io_artifacts,
    analyze_memory_artifacts,
    analyze_sys_metrics_artifacts,
    summarize_application_metric_window,
)


def _write_artifact(tmp_path, name: str, document: dict) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path)


def _app_snapshot(host_pid: int, **metrics: object) -> str:
    return json.dumps(
        {
            "schema_version": "mini-drop.application-metrics.v1",
            "host_pid": host_pid,
            "fault_active": True,
            "scenario_id": "must-not-cross-the-analyzer-boundary",
            **metrics,
        }
    )


def test_sys_metrics_v2_derives_measured_signals_without_oracle_fields(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
    pid = 321
    document = {
        "schema_version": "sys_metrics.v2",
        "pid": pid,
        "clock_ticks_per_second": 100,
        "samples": [
            {
                "offset_sec": 0,
                "captured_at_unix_ms": 1_000,
                "rss_kb": 100_000,
                "threads": 3,
                "fd_count": 8,
                "process_cpu_ticks": 100,
                "process_start_ticks": 77,
                "process_read_bytes": 0,
                "process_write_bytes": 0,
                "nonvoluntary_ctxt_switches": 1,
                "host_cpu_user_ticks": 100,
                "host_cpu_nice_ticks": 0,
                "host_cpu_system_ticks": 20,
                "host_cpu_idle_ticks": 880,
                "host_cpu_iowait_ticks": 0,
                "host_cpu_irq_ticks": 0,
                "host_cpu_softirq_ticks": 0,
                "host_cpu_total_ticks": 1_000,
                "application_metrics_json": _app_snapshot(
                    pid,
                    queue_offered_rps=200,
                    queue_completed_rps=100,
                    queue_queue_depth=5,
                    lock_wait_ms=0,
                ),
            },
            {
                "offset_sec": 2,
                "captured_at_unix_ms": 3_000,
                "rss_kb": 101_024,
                "threads": 4,
                "fd_count": 9,
                "process_cpu_ticks": 300,
                "process_start_ticks": 77,
                "process_read_bytes": 0,
                "process_write_bytes": 262_144,
                "nonvoluntary_ctxt_switches": 5,
                "host_cpu_user_ticks": 200,
                "host_cpu_nice_ticks": 0,
                "host_cpu_system_ticks": 40,
                "host_cpu_idle_ticks": 960,
                "host_cpu_iowait_ticks": 0,
                "host_cpu_irq_ticks": 0,
                "host_cpu_softirq_ticks": 0,
                "host_cpu_total_ticks": 1_200,
                "application_metrics_json": _app_snapshot(
                    pid,
                    queue_offered_rps=200,
                    queue_completed_rps=100,
                    queue_queue_depth=50,
                    lock_wait_ms=40,
                ),
            },
        ],
    }
    path = _write_artifact(tmp_path, "sys_metrics.json", document)

    updates = analyze_sys_metrics_artifacts(
        [{"id": 11, "artifact_type": "sys_metrics", "local_path": path}]
    )
    metadata = updates[11]

    assert metadata["process_identity"] == {
        "pid": pid,
        "namespace_pid": pid,
        "start_ticks": 77,
        "verified": True,
    }
    assert metadata["summary"]["process_cpu_core_usage"] == 100.0
    assert metadata["summary"]["disk_write_kbps"] == 128.0
    assert set(metadata["signals"]) >= {
        "cpu_hotspot",
        "io_activity",
        "queue_backlog",
        "lock_contention",
    }
    encoded_application = json.dumps(metadata["application_metrics"])
    assert "fault_active" not in encoded_application
    assert "scenario_id" not in encoded_application


def test_sys_metrics_v2_rejects_pid_reuse(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
    document = {
        "schema_version": "sys_metrics.v2",
        "pid": 7,
        "clock_ticks_per_second": 100,
        "samples": [
            {"offset_sec": 0, "process_start_ticks": 100},
            {"offset_sec": 1, "process_start_ticks": 200},
        ],
    }
    path = _write_artifact(tmp_path, "pid-reuse.json", document)

    with pytest.raises(ValueError, match="identity changed"):
        analyze_sys_metrics_artifacts(
            [{"id": 12, "artifact_type": "sys_metrics", "local_path": path}]
        )


def test_sys_metrics_correlates_container_namespace_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
    document = {
        "schema_version": "sys_metrics.v2",
        "pid": 90_6287,
        "namespace_pid": 1,
        "clock_ticks_per_second": 100,
        "samples": [
            {
                "offset_sec": 0,
                "captured_at_unix_ms": 1_000,
                "process_start_ticks": 77,
                "application_metrics_json": _app_snapshot(
                    1,
                    pid=1,
                    queue_offered_rps=200,
                    queue_completed_rps=50,
                    queue_queue_depth=100,
                ),
            },
            {
                "offset_sec": 1,
                "captured_at_unix_ms": 2_000,
                "process_start_ticks": 77,
                "application_metrics_json": _app_snapshot(
                    1,
                    pid=1,
                    queue_offered_rps=220,
                    queue_completed_rps=55,
                    queue_queue_depth=120,
                ),
            },
        ],
    }
    path = _write_artifact(tmp_path, "container-sys-metrics.json", document)

    updates = analyze_sys_metrics_artifacts(
        [{"id": 14, "artifact_type": "sys_metrics", "local_path": path}]
    )

    metadata = updates[14]
    assert metadata["process_identity"] == {
        "pid": 90_6287,
        "namespace_pid": 1,
        "start_ticks": 77,
        "verified": True,
    }
    assert metadata["application_metrics"]["identity"] == {
        "host_pid": 90_6287,
        "identity_verified": True,
        "namespace_pid": 1,
        "pid": 1,
    }
    assert metadata["signals"]["queue_backlog"]["detected"] is True


def test_memory_v2_derives_memory_growth_from_same_target(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
    pid = 99
    document = {
        "schema_version": "memory.v2",
        "pid": pid,
        "samples": [
            {
                "offset_sec": 0,
                "rss_kb": 100 * 1024,
                "pss_kb": 90 * 1024,
                "swap_kb": 0,
                "process_start_ticks": 456,
                "application_metrics_json": _app_snapshot(
                    pid, retained_memory_mb=0
                ),
            },
            {
                "offset_sec": 2,
                "rss_kb": 132 * 1024,
                "pss_kb": 120 * 1024,
                "swap_kb": 0,
                "process_start_ticks": 456,
                "application_metrics_json": _app_snapshot(
                    pid, retained_memory_mb=32
                ),
            },
        ],
    }
    path = _write_artifact(tmp_path, "memory.json", document)

    metadata = analyze_memory_artifacts(
        [{"id": 13, "artifact_type": "memory_json", "local_path": path}]
    )[13]

    assert metadata["summary"]["vmrss_mb_delta"] == 32.0
    assert metadata["summary"]["pss_mb_delta"] == 30.0
    assert metadata["signals"]["memory_growth"]["detected"] is True
    assert metadata["process_identity"]["verified"] is True


def test_ebpf_latency_is_explicitly_host_scoped(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
    document = {
        "schema_version": "ebpf_io.v1",
        "total_samples": 20,
        "io_latency_us": {
            "[0, 100)": 10,
            "[100, 1000)": 5,
            "[1000, 5000)": 5,
        },
    }
    path = _write_artifact(tmp_path, "ebpf.json", document)

    metadata = analyze_ebpf_io_artifacts(
        [{"id": 14, "artifact_type": "ebpf_metrics", "local_path": path}]
    )[14]

    assert metadata["signals"]["io_latency"]["detected"] is True
    assert metadata["scope_semantics"] == "HOST_BLOCK_DEVICE"
    assert metadata["target_attributed"] is False
    assert "correlate" in metadata["analysis_notes"][0]


def test_application_window_strips_control_plane_oracle_fields():
    before = {
        "host_pid": 88,
        "fault_active": False,
        "scenario_id": "java-gc-pressure",
        "allocated_bytes": 1_000,
        "gc_count": 2,
        "gc_time_ms": 3,
    }
    after = {
        "host_pid": 88,
        "fault_active": True,
        "scenario_id": "java-gc-pressure",
        "allocated_bytes": 3_000_000,
        "gc_count": 4,
        "gc_time_ms": 40,
    }

    application, signals = summarize_application_metric_window(before, after)

    assert "fault_active" not in json.dumps(application)
    assert "scenario_id" not in json.dumps(application)
    assert signals["jvm_gc"]["detected"] is True
