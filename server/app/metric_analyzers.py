"""Parse bounded metric artifacts into evidence-ready analyzer metadata.

The native collectors upload immutable JSON artifacts.  This module turns
those bytes into compact, allow-listed observations.  Fault switches and
scenario identifiers are intentionally never copied into Analyzer metadata:
the evidence layer may observe measurements, but it must not read the answer
from the controlled fault lab.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Callable

from server.app import storage


_MAX_JSON_BYTES = 32 * 1024 * 1024
_MAX_SAMPLES = 86_400

_APPLICATION_IDENTITY_FIELDS = {
    "runtime",
    "pid",
    "host_pid",
    "namespace_pid",
    "identity_verified",
    "boot_id",
    "peer_pid",
    "peer_boot_id",
    "target_boot_id",
    "same_host_verified",
}

# Only measurements may cross the Analyzer boundary.  In particular, fields
# such as fault_active, scenario_id and auto_stop_remaining_seconds are not
# accepted because they would leak the controlled experiment's oracle.
_APPLICATION_METRIC_FIELDS = {
    "process_cpu_percent",
    "operation_count",
    "cpu_operations",
    "source_profile_samples",
    "hot_function_samples",
    "process_rss_mb",
    "rss_mb",
    "retained_memory_mb",
    "retained_memory_bytes",
    "heap_alloc_bytes",
    "heap_used_bytes",
    "offheap_retained_bytes",
    "allocated_bytes",
    "gc_count",
    "gc_time_ms",
    "lock_acquisitions",
    "lock_contentions",
    "lock_wait_ms",
    "io_workload_bytes",
    "process_write_bytes",
    "io_bytes_written",
    "io_operations",
    "io_failures",
    "peer_cpu_ticks",
    "load_offered_rps",
    "load_completed_rps",
    "load_rejected_requests",
    "load_queue_depth",
    "load_latency_ms",
    "queue_offered_rps",
    "queue_completed_rps",
    "queue_rejected_requests",
    "queue_queue_depth",
    "queue_latency_ms",
    "producer_rate",
    "consumer_rate",
    "queue_lag",
    "network_delay_ms",
    "network_requests",
    "network_failures",
    "network_average_latency_ms",
    "downstream_delay_ms",
    "downstream_requests",
    "downstream_failures",
    "downstream_average_latency_ms",
    "downstream_mean_latency_ms",
}


def analyze_sys_metrics_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    allow_remote: bool = False,
) -> dict[int, dict[str, Any]]:
    return _analyze_artifact_type(
        artifacts,
        artifact_type="sys_metrics",
        parser=_parse_sys_metrics_document,
        allow_remote=allow_remote,
    )


def analyze_memory_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    allow_remote: bool = False,
) -> dict[int, dict[str, Any]]:
    return _analyze_artifact_type(
        artifacts,
        artifact_type="memory_json",
        parser=_parse_memory_document,
        allow_remote=allow_remote,
    )


def analyze_ebpf_io_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    allow_remote: bool = False,
) -> dict[int, dict[str, Any]]:
    return _analyze_artifact_type(
        artifacts,
        artifact_type="ebpf_metrics",
        parser=_parse_ebpf_io_document,
        allow_remote=allow_remote,
    )


def _analyze_artifact_type(
    artifacts: list[dict[str, Any]],
    *,
    artifact_type: str,
    parser: Callable[[dict[str, Any]], dict[str, Any]],
    allow_remote: bool,
) -> dict[int, dict[str, Any]]:
    updates: dict[int, dict[str, Any]] = {}
    for artifact in artifacts:
        if artifact.get("artifact_type") != artifact_type:
            continue
        payload = _read_artifact_bytes(artifact, allow_remote=allow_remote)
        # Contract-only unit tests and legacy in-process callers may not carry
        # a storage locator.  Production artifacts are integrity-verified and
        # always have either a bounded local path or an object-store key.
        if payload is None:
            continue
        document = _decode_json_document(payload, artifact_type)
        artifact_id = artifact.get("id")
        if artifact_id is None:
            raise ValueError(f"{artifact_type} artifact has no persistent id")
        updates[int(artifact_id)] = {
            **dict(artifact.get("metadata") or {}),
            **parser(document),
        }
    return updates


def _artifact_root() -> Path:
    return Path(
        os.getenv("MINI_DROP_ARTIFACT_ROOT", "/tmp/mini-drop")
    ).expanduser().resolve()


def _read_artifact_bytes(
    artifact: dict[str, Any],
    *,
    allow_remote: bool,
) -> bytes | None:
    local_path = artifact.get("local_path")
    if local_path:
        root = _artifact_root()
        candidate = Path(str(local_path)).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            candidate = candidate.resolve()
            candidate.relative_to(root)
        except (OSError, ValueError):
            candidate = None
        if candidate is not None and candidate.is_file():
            if candidate.stat().st_size > _MAX_JSON_BYTES:
                raise ValueError("structured metric artifact exceeds analyzer limit")
            return candidate.read_bytes()

    bucket = artifact.get("bucket")
    object_key = artifact.get("object_key") or artifact.get("cos_key")
    if not (allow_remote and bucket and object_key):
        return None
    payload = storage.read_object_bytes(str(bucket), str(object_key))
    if len(payload) > _MAX_JSON_BYTES:
        raise ValueError("structured metric artifact exceeds analyzer limit")
    return payload


def _decode_json_document(payload: bytes, artifact_type: str) -> dict[str, Any]:
    if not payload:
        raise ValueError(f"{artifact_type} artifact is empty")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{artifact_type} artifact is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{artifact_type} artifact root must be an object")
    return document


def _number(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"metric field {field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"metric field {field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"metric field {field} must be finite")
    return result


def _optional_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _rounded(value: float | int, digits: int = 3) -> float:
    return round(float(value), digits)


def _validated_samples(document: dict[str, Any], schema: str) -> list[dict[str, Any]]:
    samples = document.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError(f"{schema} requires at least one sample")
    if len(samples) > _MAX_SAMPLES:
        raise ValueError(f"{schema} exceeds the sample limit")
    if not all(isinstance(item, dict) for item in samples):
        raise ValueError(f"{schema} samples must be objects")
    offsets = [_number(item.get("offset_sec"), field="offset_sec") for item in samples]
    if any(right <= left for left, right in zip(offsets, offsets[1:])):
        raise ValueError(f"{schema} sample offsets must be strictly increasing")
    return samples


def _series(samples: list[dict[str, Any]], field: str) -> list[float]:
    values: list[float] = []
    for sample in samples:
        value = _optional_number(sample.get(field))
        if value is not None:
            values.append(value)
    return values


def _duration_seconds(samples: list[dict[str, Any]]) -> float:
    timestamps = _series(samples, "captured_at_unix_ms")
    if len(timestamps) >= 2 and timestamps[-1] > timestamps[0]:
        return (timestamps[-1] - timestamps[0]) / 1000.0
    offsets = _series(samples, "offset_sec")
    if len(offsets) >= 2 and offsets[-1] > offsets[0]:
        return offsets[-1] - offsets[0]
    return max(float(len(samples) - 1), 1.0)


def _trend_summary(
    samples: list[dict[str, Any]],
    source_field: str,
    *,
    output_prefix: str,
    scale: float = 1.0,
) -> dict[str, float]:
    values = _series(samples, source_field)
    if not values:
        return {}
    first = values[0] / scale
    last = values[-1] / scale
    return {
        output_prefix: _rounded(last),
        f"{output_prefix}_first": _rounded(first),
        f"{output_prefix}_max": _rounded(max(values) / scale),
        f"{output_prefix}_delta": _rounded(last - first),
    }


def _parse_application_samples(
    samples: list[dict[str, Any]],
    *,
    target_pid: int,
    target_namespace_pid: int | None = None,
) -> dict[str, Any] | None:
    snapshots: list[dict[str, Any]] = []
    for sample in samples:
        raw = sample.get("application_metrics_json")
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("application metric snapshot is invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("application metric snapshot must be an object")
        snapshots.append(
            _sanitize_application_snapshot(
                parsed,
                target_pid=target_pid,
                target_namespace_pid=target_namespace_pid,
            )
        )
    if not snapshots:
        return None

    return _summarize_application_snapshots(snapshots)


def summarize_application_metric_window(
    before: dict[str, Any],
    after: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Normalize an instrumented before/after window without oracle fields."""

    before_host_pid = int(_number(before.get("host_pid"), field="host_pid"))
    after_host_pid = int(_number(after.get("host_pid"), field="host_pid"))
    if before_host_pid <= 0 or before_host_pid != after_host_pid:
        raise ValueError("application metric window target identity mismatch")
    snapshots = [
        _sanitize_application_snapshot(before, target_pid=before_host_pid),
        _sanitize_application_snapshot(after, target_pid=before_host_pid),
    ]
    application = _summarize_application_snapshots(snapshots)
    return application, _derive_signals({}, application)


def _sanitize_application_snapshot(
    parsed: dict[str, Any],
    *,
    target_pid: int,
    target_namespace_pid: int | None = None,
) -> dict[str, Any]:
    schema = parsed.get("schema_version")
    # Java snapshots from the first counter-contract release did not yet carry
    # their own schema marker.  They are still safe to replay because this
    # function copies an explicit allow-list and validates the stable host PID.
    if schema not in {None, "", "mini-drop.application-metrics.v1"}:
        raise ValueError("unsupported application metric schema")
    expected_pids = {target_pid}
    if target_namespace_pid is not None and target_namespace_pid > 0:
        expected_pids.add(target_namespace_pid)
    reported_pids: set[int] = set()
    for field in ("host_pid", "namespace_pid", "pid"):
        value = parsed.get(field)
        if value is None:
            continue
        try:
            parsed_pid = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("application metric snapshot PID is invalid") from exc
        if parsed_pid > 0:
            reported_pids.add(parsed_pid)
    if not reported_pids or reported_pids.isdisjoint(expected_pids):
        raise ValueError("application metric snapshot target identity mismatch")
    if target_namespace_pid is not None:
        local_pid = parsed.get("namespace_pid", parsed.get("pid"))
        if local_pid is not None and int(local_pid) != target_namespace_pid:
            raise ValueError("application metric snapshot namespace PID mismatch")
    safe: dict[str, Any] = {}
    for field in _APPLICATION_IDENTITY_FIELDS:
        value = parsed.get(field)
        if isinstance(value, (str, int, float, bool)) and not (
            isinstance(value, float) and not math.isfinite(value)
        ):
            safe[field] = value
    # The Agent owns the host/namespace mapping.  Application processes inside
    # a container cannot reliably discover their outer PID from a namespace-
    # local /proc mount, so normalize identity using the Agent observation.
    safe["host_pid"] = target_pid
    if target_namespace_pid is not None:
        safe["namespace_pid"] = target_namespace_pid
    safe["identity_verified"] = True
    for field in _APPLICATION_METRIC_FIELDS:
        value = _optional_number(parsed.get(field))
        if value is not None:
            safe[field] = value
    return safe


def _summarize_application_snapshots(
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:

    numeric_fields = sorted(
        {
            field
            for snapshot in snapshots
            for field, value in snapshot.items()
            if field in _APPLICATION_METRIC_FIELDS
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        }
    )
    before: dict[str, float] = {}
    after: dict[str, float] = {}
    maximum: dict[str, float] = {}
    delta: dict[str, float] = {}
    for field in numeric_fields:
        values = [
            float(snapshot[field])
            for snapshot in snapshots
            if field in snapshot
        ]
        if not values:
            continue
        before[field] = _rounded(values[0])
        after[field] = _rounded(values[-1])
        maximum[field] = _rounded(max(values))
        delta[field] = _rounded(values[-1] - values[0])

    identity = {
        field: snapshots[-1][field]
        for field in _APPLICATION_IDENTITY_FIELDS
        if field in snapshots[-1]
    }
    return {
        "schema_version": "application_metrics_analysis.v1",
        "sample_count": len(snapshots),
        "identity": identity,
        "before": before,
        "after": after,
        "max": maximum,
        "delta": delta,
    }


def _metric(
    application: dict[str, Any] | None,
    section: str,
    *fields: str,
) -> float | None:
    if not application:
        return None
    values = application.get(section)
    if not isinstance(values, dict):
        return None
    for field in fields:
        value = _optional_number(values.get(field))
        if value is not None:
            return value
    return None


def _signal(reason: str, **metrics: Any) -> dict[str, Any]:
    return {
        "detected": True,
        "reason": reason,
        "metrics": {
            key: _rounded(value) if isinstance(value, (int, float)) else value
            for key, value in metrics.items()
            if value is not None
        },
    }


def _derive_signals(
    summary: dict[str, Any],
    application: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    signals: dict[str, dict[str, Any]] = {}

    process_cpu = _optional_number(summary.get("process_cpu_core_usage"))
    app_cpu = _metric(application, "max", "process_cpu_percent")
    cpu_operations = _metric(application, "delta", "cpu_operations", "operation_count")
    if max(process_cpu or 0.0, app_cpu or 0.0) >= 50.0 or (cpu_operations or 0) > 0:
        signals["cpu_hotspot"] = _signal(
            "target process consumed sustained CPU or advanced its measured compute loop",
            process_cpu_core_usage=process_cpu,
            application_cpu_percent=app_cpu,
            cpu_operations_delta=cpu_operations,
        )

    retained_mb = _metric(application, "max", "retained_memory_mb")
    retained_bytes = _metric(
        application,
        "max",
        "retained_memory_bytes",
        "offheap_retained_bytes",
    )
    retained_from_bytes_mb = (
        retained_bytes / (1024.0 * 1024.0) if retained_bytes is not None else None
    )
    rss_delta = _optional_number(summary.get("vmrss_mb_delta"))
    pss_delta = _optional_number(summary.get("pss_mb_delta"))
    if (
        max(retained_mb or 0.0, retained_from_bytes_mb or 0.0) >= 16.0
        or max(rss_delta or 0.0, pss_delta or 0.0) >= 8.0
    ):
        signals["memory_growth"] = _signal(
            "process memory footprint or instrumented retained memory increased materially",
            retained_memory_mb=max(retained_mb or 0.0, retained_from_bytes_mb or 0.0),
            rss_delta_mb=rss_delta,
            pss_delta_mb=pss_delta,
        )

    process_write_rate = _optional_number(summary.get("disk_write_kbps"))
    io_bytes_delta = _metric(
        application,
        "delta",
        "io_bytes_written",
        "io_workload_bytes",
        "process_write_bytes",
    )
    io_operations = _metric(application, "delta", "io_operations")
    if (
        (process_write_rate or 0.0) >= 64.0
        or (io_bytes_delta or 0.0) >= 64 * 1024
        or (io_operations or 0.0) > 0
    ):
        signals["io_activity"] = _signal(
            "target process produced measurable write activity in the observation window",
            disk_write_kbps=process_write_rate,
            io_bytes_written_delta=io_bytes_delta,
            io_operations_delta=io_operations,
        )

    network_latency = _metric(application, "max", "network_average_latency_ms")
    network_requests = _metric(application, "delta", "network_requests")
    if (network_latency or 0.0) >= 100.0 and (network_requests or 0.0) > 0:
        signals["network_latency"] = _signal(
            "instrumented requests observed sustained network-path latency",
            average_latency_ms=network_latency,
            request_count_delta=network_requests,
            failure_count_delta=_metric(application, "delta", "network_failures"),
        )

    downstream_latency = _metric(
        application,
        "max",
        "downstream_average_latency_ms",
        "downstream_mean_latency_ms",
    )
    downstream_requests = _metric(application, "delta", "downstream_requests")
    if (downstream_latency or 0.0) >= 100.0 and (downstream_requests or 0.0) > 0:
        signals["downstream_latency"] = _signal(
            "instrumented downstream calls observed sustained response latency",
            average_latency_ms=downstream_latency,
            request_count_delta=downstream_requests,
            failure_count_delta=_metric(application, "delta", "downstream_failures"),
        )

    lock_wait_delta = _metric(application, "delta", "lock_wait_ms")
    lock_contentions = _metric(application, "delta", "lock_contentions")
    if (lock_wait_delta or 0.0) >= 10.0 or (lock_contentions or 0.0) > 0:
        signals["lock_contention"] = _signal(
            "instrumented lock wait or contention counters increased in-window",
            lock_wait_ms_delta=lock_wait_delta,
            lock_contentions_delta=lock_contentions,
            lock_acquisitions_delta=_metric(application, "delta", "lock_acquisitions"),
        )

    allocated_delta = _metric(application, "delta", "allocated_bytes")
    gc_count_delta = _metric(application, "delta", "gc_count")
    gc_time_delta = _metric(application, "delta", "gc_time_ms")
    if (allocated_delta or 0.0) >= 1024 * 1024 and (
        (gc_count_delta or 0.0) > 0 or (gc_time_delta or 0.0) > 0
    ):
        signals["jvm_gc"] = _signal(
            "JVM allocation and garbage-collection counters advanced in the same window",
            allocated_bytes_delta=allocated_delta,
            gc_count_delta=gc_count_delta,
            gc_time_ms_delta=gc_time_delta,
        )

    load_offered = _metric(application, "max", "load_offered_rps")
    load_completed = _metric(application, "max", "load_completed_rps")
    load_rejected = _metric(application, "max", "load_rejected_requests")
    if (
        load_offered is not None
        and load_completed is not None
        and load_offered > load_completed * 1.05
        and (load_rejected or 0.0) > 0
    ):
        signals["load_saturation"] = _signal(
            "request arrival rate exceeded completion rate and rejections were observed",
            offered_rps=load_offered,
            completed_rps=load_completed,
            rejected_requests=load_rejected,
            queue_depth=_metric(application, "max", "load_queue_depth"),
        )

    producer = _metric(application, "max", "producer_rate", "queue_offered_rps")
    consumer = _metric(application, "max", "consumer_rate", "queue_completed_rps")
    queue_lag = _metric(application, "max", "queue_lag", "queue_queue_depth")
    if (
        producer is not None
        and consumer is not None
        and producer > consumer * 1.05
        and (queue_lag or 0.0) > 0
    ):
        signals["queue_backlog"] = _signal(
            "producer rate exceeded consumer rate while queue lag was non-zero",
            producer_rate=producer,
            consumer_rate=consumer,
            queue_lag=queue_lag,
        )

    peer_ticks = _metric(application, "delta", "peer_cpu_ticks")
    same_host = bool(
        (application or {}).get("identity", {}).get("same_host_verified")
        if isinstance((application or {}).get("identity"), dict)
        else False
    )
    if same_host and (peer_ticks or 0.0) > 0:
        signals["noisy_neighbor"] = _signal(
            "a separately identified peer on the same host consumed CPU in-window",
            peer_cpu_ticks_delta=peer_ticks,
            same_host_verified=True,
        )

    return signals


def _parse_sys_metrics_document(document: dict[str, Any]) -> dict[str, Any]:
    source_schema = str(document.get("schema_version") or "")
    if source_schema not in {"sys_metrics.v1", "sys_metrics.v2"}:
        raise ValueError("unsupported sys_metrics schema")
    target_pid = int(_number(document.get("pid"), field="pid"))
    if target_pid <= 0:
        raise ValueError("sys_metrics pid must be positive")
    target_namespace_pid = int(
        _optional_number(document.get("namespace_pid")) or target_pid
    )
    if target_namespace_pid <= 0:
        raise ValueError("sys_metrics namespace pid must be positive")
    samples = _validated_samples(document, source_schema)
    duration = _duration_seconds(samples)

    start_ticks = {
        int(value)
        for value in _series(samples, "process_start_ticks")
        if value > 0
    }
    if len(start_ticks) > 1:
        raise ValueError("target process identity changed during sys_metrics collection")
    identity_verified = source_schema == "sys_metrics.v2" and len(start_ticks) == 1
    if source_schema == "sys_metrics.v2" and not identity_verified:
        raise ValueError("sys_metrics.v2 is missing a stable process start time")

    summary: dict[str, Any] = {}
    summary.update(
        _trend_summary(samples, "rss_kb", output_prefix="vmrss_mb", scale=1024.0)
    )
    summary.update(_trend_summary(samples, "fd_count", output_prefix="fd_count"))
    summary.update(_trend_summary(samples, "threads", output_prefix="thread_count"))
    summary["fd_trend"] = summary.pop("fd_count_delta", 0.0)
    summary["thread_trend"] = summary.pop("thread_count_delta", 0.0)

    load = _series(samples, "load1m")
    if load:
        summary["load1m"] = _rounded(sum(load) / len(load))
        summary["load1m_max"] = _rounded(max(load))

    clock_ticks = int(_optional_number(document.get("clock_ticks_per_second")) or 0)
    process_ticks = _series(samples, "process_cpu_ticks")
    if clock_ticks > 0 and len(process_ticks) >= 2 and duration > 0:
        delta = process_ticks[-1] - process_ticks[0]
        if delta < 0:
            raise ValueError("target process CPU counter moved backwards")
        summary["process_cpu_core_usage"] = _rounded(
            delta / clock_ticks / duration * 100.0
        )

    host_total = _series(samples, "host_cpu_total_ticks")
    if len(host_total) >= 2 and host_total[-1] > host_total[0]:
        total_delta = host_total[-1] - host_total[0]

        def host_delta(field: str) -> float:
            values = _series(samples, field)
            return max(0.0, values[-1] - values[0]) if len(values) >= 2 else 0.0

        user_delta = host_delta("host_cpu_user_ticks") + host_delta(
            "host_cpu_nice_ticks"
        )
        system_delta = (
            host_delta("host_cpu_system_ticks")
            + host_delta("host_cpu_irq_ticks")
            + host_delta("host_cpu_softirq_ticks")
        )
        iowait_delta = host_delta("host_cpu_iowait_ticks")
        idle_delta = host_delta("host_cpu_idle_ticks")
        summary["avg_cpu_user_pct"] = _rounded(user_delta / total_delta * 100.0)
        summary["avg_cpu_sys_pct"] = _rounded(system_delta / total_delta * 100.0)
        summary["avg_cpu_iowait_pct"] = _rounded(
            iowait_delta / total_delta * 100.0
        )
        summary["host_cpu_busy_pct"] = _rounded(
            max(0.0, total_delta - idle_delta - iowait_delta)
            / total_delta
            * 100.0
        )

    for source_field, output_field in (
        ("process_read_bytes", "disk_read_kbps"),
        ("process_write_bytes", "disk_write_kbps"),
        ("nonvoluntary_ctxt_switches", "ctx_nonvoluntary_rate"),
    ):
        values = _series(samples, source_field)
        if len(values) < 2 or duration <= 0:
            continue
        delta = values[-1] - values[0]
        if delta < 0:
            raise ValueError(f"cumulative metric moved backwards: {source_field}")
        divisor = 1024.0 if source_field.endswith("_bytes") else 1.0
        summary[output_field] = _rounded(delta / duration / divisor)

    application = _parse_application_samples(
        samples,
        target_pid=target_pid,
        target_namespace_pid=target_namespace_pid,
    )
    signals = _derive_signals(summary, application)
    limitations = []
    if not identity_verified:
        limitations.append(
            "legacy sys_metrics.v1 has no process start time; PID reuse cannot be independently checked"
        )
    if application is None:
        limitations.append(
            "target application did not expose the optional bounded metrics snapshot"
        )
    return {
        "schema_version": "sys_metrics_analysis.v2",
        "source_schema_version": source_schema,
        "sample_count": len(samples),
        "window_duration_seconds": _rounded(duration),
        "process_identity": {
            "pid": target_pid,
            "namespace_pid": target_namespace_pid,
            "start_ticks": next(iter(start_ticks), None),
            "verified": identity_verified,
        },
        "summary": summary,
        "application_metrics": application,
        "signals": signals,
        "analysis_limitations": limitations,
    }


def _parse_memory_document(document: dict[str, Any]) -> dict[str, Any]:
    source_schema = str(document.get("schema_version") or "")
    if source_schema not in {"memory.v1", "memory.v2"}:
        raise ValueError("unsupported memory metrics schema")
    target_pid = int(_number(document.get("pid"), field="pid"))
    if target_pid <= 0:
        raise ValueError("memory metrics pid must be positive")
    target_namespace_pid = int(
        _optional_number(document.get("namespace_pid")) or target_pid
    )
    if target_namespace_pid <= 0:
        raise ValueError("memory metrics namespace pid must be positive")
    samples = _validated_samples(document, source_schema)
    summary: dict[str, Any] = {}
    summary.update(
        _trend_summary(samples, "rss_kb", output_prefix="vmrss_mb", scale=1024.0)
    )
    summary.update(
        _trend_summary(samples, "pss_kb", output_prefix="pss_mb", scale=1024.0)
    )
    summary.update(
        _trend_summary(samples, "swap_kb", output_prefix="swap_used_mb", scale=1024.0)
    )
    start_ticks = {
        int(value)
        for value in _series(samples, "process_start_ticks")
        if value > 0
    }
    if len(start_ticks) > 1:
        raise ValueError("target process identity changed during memory collection")
    identity_verified = source_schema == "memory.v2" and len(start_ticks) == 1
    if source_schema == "memory.v2" and not identity_verified:
        raise ValueError("memory.v2 is missing a stable process start time")
    application = _parse_application_samples(
        samples,
        target_pid=target_pid,
        target_namespace_pid=target_namespace_pid,
    )
    limitations = []
    if not identity_verified:
        limitations.append(
            "legacy memory.v1 has no process start time; PID reuse cannot be independently checked"
        )
    if application is None:
        limitations.append(
            "target application did not expose the optional bounded metrics snapshot"
        )
    return {
        "schema_version": "memory_analysis.v2",
        "source_schema_version": source_schema,
        "sample_count": len(samples),
        "window_duration_seconds": _rounded(_duration_seconds(samples)),
        "process_identity": {
            "pid": target_pid,
            "namespace_pid": target_namespace_pid,
            "start_ticks": next(iter(start_ticks), None),
            "verified": identity_verified,
        },
        "summary": summary,
        "application_metrics": application,
        "signals": _derive_signals(summary, application),
        "analysis_limitations": limitations,
    }


_HISTOGRAM_BUCKET = re.compile(r"^\[\s*(\d+)\s*,\s*(\d+)\s*\)$")


def _parse_ebpf_io_document(document: dict[str, Any]) -> dict[str, Any]:
    if document.get("schema_version") != "ebpf_io.v1":
        raise ValueError("unsupported eBPF I/O schema")
    raw_histogram = document.get("io_latency_us")
    if not isinstance(raw_histogram, dict) or not raw_histogram:
        raise ValueError("eBPF I/O histogram is empty")
    histogram: list[tuple[int, int, int]] = []
    for bucket, raw_count in raw_histogram.items():
        match = _HISTOGRAM_BUCKET.fullmatch(str(bucket))
        if match is None:
            raise ValueError(f"invalid eBPF histogram bucket: {bucket}")
        lower, upper = int(match.group(1)), int(match.group(2))
        count = int(_number(raw_count, field=f"io_latency_us.{bucket}"))
        if lower < 0 or upper <= lower or count < 0:
            raise ValueError("invalid eBPF histogram bounds or count")
        histogram.append((lower, upper, count))
    histogram.sort()
    observed_total = sum(count for _, _, count in histogram)
    declared_total = int(_number(document.get("total_samples"), field="total_samples"))
    if declared_total <= 0 or declared_total != observed_total:
        raise ValueError("eBPF histogram count does not match total_samples")

    def percentile(percent: float) -> int:
        threshold = max(1, math.ceil(declared_total * percent))
        cumulative = 0
        for _lower, upper, count in histogram:
            cumulative += count
            if cumulative >= threshold:
                return upper
        return histogram[-1][1]

    high_latency_samples = sum(
        count for lower, _upper, count in histogram if lower >= 1000
    )
    p95 = percentile(0.95)
    summary = {
        "io_latency_us": p95,
        "block_latency_us": p95,
        "latency_p50_us": percentile(0.50),
        "latency_p95_us": p95,
        "latency_p99_us": percentile(0.99),
        "high_latency_samples": high_latency_samples,
        "high_latency_ratio": _rounded(high_latency_samples / declared_total),
    }
    signals: dict[str, dict[str, Any]] = {}
    if p95 >= 1000 or high_latency_samples / declared_total >= 0.05:
        signals["io_latency"] = _signal(
            "host block-device tracepoints observed a measurable high-latency tail",
            latency_p95_us=p95,
            high_latency_samples=high_latency_samples,
            total_samples=declared_total,
        )
    return {
        "schema_version": "ebpf_io_analysis.v1",
        "source_schema_version": "ebpf_io.v1",
        "sample_count": declared_total,
        "total_samples": declared_total,
        "summary": summary,
        "signals": signals,
        "scope_semantics": "HOST_BLOCK_DEVICE",
        "target_attributed": False,
        "analysis_notes": [
            "block tracepoints are host-level; correlate with target process write counters before root-cause attribution"
        ],
    }
