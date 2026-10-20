"""在资源限制内解析事件与固定模板结果，统计值只来自实际输入事件。"""

import json
import math
from collections import Counter, defaultdict

from .errors import AnalysisError
from .stacks import stack_result

MAX_EVENTS = 200_000


def fail(message):
    raise AnalysisError("INVALID_INPUT", message)


def integer(row, key, default=0):
    value = row.get(key, default)
    maximum = 2**63 - 1 if key in {"time_ns", "timestamp_ns"} else 2**53 - 1
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= maximum
    ):
        fail(f"Event field {key} must be a bounded non-negative integer")
    return value


def document(raw):
    if len(raw) > 32 * 1024 * 1024:
        raise AnalysisError("INPUT_LIMIT_EXCEEDED", "Event input exceeds byte limit")
    try:
        return json.loads(raw, parse_constant=lambda _: fail("Non-finite JSON number"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        fail("Malformed JSON event artifact")


def events(raw):
    if len(raw) > 32 * 1024 * 1024:
        raise AnalysisError("INPUT_LIMIT_EXCEEDED", "Event input exceeds byte limit")
    try:
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    except (ValueError, UnicodeDecodeError, RecursionError):
        fail("Malformed JSONL event artifact")
    if len(rows) > MAX_EVENTS:
        raise AnalysisError("INPUT_LIMIT_EXCEEDED", "Event count exceeds limit")
    if any(not isinstance(row, dict) for row in rows):
        fail("Every event must be an object")
    return rows


def percentiles(values):
    values = sorted(values)
    return {
        name: values[max(0, math.ceil(len(values) * quantile) - 1)] if values else None
        for name, quantile in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))
    }


def address(row, key="address"):
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        fail("Memory address must be a numeric pointer or a hex string")
    try:
        result = (
            int(value, 16 if value.lower().startswith("0x") else 10)
            if isinstance(value, str)
            else value
        )
    except ValueError:
        fail("Memory address is malformed")
    if not 0 <= result <= 2**64 - 1:
        fail("Memory address exceeds pointer width")
    return result


def analyze_io(raw):
    rows = events(raw)
    durations, stacks, intervals = [], [], []
    by_device, by_process, by_file, series = (
        defaultdict(Counter),
        defaultdict(Counter),
        defaultdict(Counter),
        defaultdict(Counter),
    )
    start, end, reads, writes, total_bytes = None, None, 0, 0, 0
    lost, error_operations = 0, 0
    for row in rows:
        if row.get("event") == "lost":
            lost += integer(row, "count")
            continue
        if row.get("event") not in {
            "io",
            "block",
            "file",
            "read",
            "write",
            "block_complete",
            "file_io",
        }:
            fail("Unsupported I/O event type")
        timestamp = integer(row, "time_ns", row.get("timestamp_ns", 0))
        duration = integer(row, "duration_ns", row.get("latency_ns", 0))
        raw_size = row.get("bytes", row.get("size", 0))
        if (
            isinstance(raw_size, int)
            and not isinstance(raw_size, bool)
            and raw_size < 0
        ):
            if raw_size < -4095:
                fail("Invalid negative syscall result")
            error_operations += 1
            size = 0
        else:
            size = integer(row, "bytes", row.get("size", 0))
        pid = integer(row, "pid")
        operation = str(row.get("operation", row.get("event", "unknown"))).lower()
        operation = (
            operation.rsplit(":", 1)[-1]
            .removeprefix("sys_enter_")
            .removeprefix("sys_exit_")
        )
        begin = max(0, timestamp - duration)
        start = begin if start is None else min(start, begin)
        end = timestamp if end is None else max(end, timestamp)
        if "duration_ns" in row or "latency_ns" in row:
            durations.append(duration)
            if duration:
                intervals.extend(((begin, 1), (timestamp, -1)))
        total_bytes += size
        reads += operation.startswith("r")
        writes += operation.startswith("w")
        for target, key in (
            (by_device, str(row.get("device", "unknown"))),
            (by_process, f"{pid}:{str(row.get('comm', ''))[:128]}"),
            (by_file, str(row.get("path", "[unavailable]"))[:1024]),
        ):
            target[key].update(operations=1, bytes=size, duration_ns=duration)
        bucket = timestamp // 1_000_000_000
        series[bucket].update(operations=1, bytes=size, duration_ns=duration)
        frames = row.get("stack") or [
            f"pid:{pid}",
            operation,
            str(row.get("path") or row.get("device") or "[unknown]"),
        ]
        if not isinstance(frames, list) or len(frames) > 128:
            fail("I/O stack must be a bounded array")
        stacks.append((frames, size))
    elapsed = (end - start) / 1e9 if start is not None and end > start else 0
    active, maximum, integral, previous = 0, 0, 0, start or 0
    for timestamp, delta in sorted(intervals, key=lambda value: (value[0], -value[1])):
        integral += max(0, timestamp - previous) * active
        active += delta
        maximum, previous = max(maximum, active), timestamp

    def hotspots(target):
        return [
            {"name": key, **value}
            for key, value in sorted(
                target.items(), key=lambda item: (-item[1]["bytes"], item[0])
            )[:100]
        ]

    parsed = stack_result(
        stacks, unit="bytes", metadata={"format": "io-jsonl/v1", "lost_events": lost}
    )
    operation_count = len(stacks)
    parsed["sections"] = {
        "io": {
            "operations": operation_count,
            "bytes": total_bytes,
            "error_operations": error_operations,
            "read_operations": reads,
            "write_operations": writes,
            "duration_seconds": elapsed,
            "throughput_bytes_per_second": total_bytes / elapsed if elapsed else None,
            "iops": operation_count / elapsed if elapsed else None,
            "latency_ns": percentiles(durations),
            "latency_observations": len(durations),
            "queue": {
                "max_depth": maximum if durations else None,
                "average_depth": integral / ((end or 0) - (start or 0))
                if elapsed and durations
                else None,
                "derivation": "overlapping observed issue/completion intervals",
            },
            "devices": hotspots(by_device),
            "processes": hotspots(by_process),
            "files": hotspots(by_file),
            "timeseries": [
                {"time_ns": bucket * 1_000_000_000, **value}
                for bucket, value in sorted(series.items())
            ][:3600],
            "lost_events": lost,
            "limitations": [
                "队列深度由已观测事件区间计算；采样、丢失和未覆盖设备会影响统计。"
            ],
        }
    }
    return parsed


def analyze_memory(raw):
    rows = events(raw)
    rows.sort(key=lambda row: integer(row, "time_ns", row.get("timestamp_ns", 0)))
    live, allocated, freed, peak, current, alloc_count, free_count, unknown_frees = (
        {},
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    faults, stacks, series, last = Counter(), [], defaultdict(Counter), 0
    failed_allocations, mapping_work = 0, 0
    for row in rows:
        event = row.get("event")
        timestamp = integer(row, "time_ns", row.get("timestamp_ns", 0))
        if timestamp < last:
            fail("Memory events must be ordered by timestamp")
        last = timestamp
        bucket = timestamp // 1_000_000_000
        if event in {"alloc", "malloc", "calloc", "realloc", "mmap"}:
            size = integer(row, "size", row.get("bytes", 0))
            key = (integer(row, "pid"), address(row))
            if key[1] in {0, 2**64 - 1}:
                failed_allocations += 1
                continue
            if event == "realloc":
                old = (
                    key[0],
                    address(row, "old_address") if "old_address" in row else key[1],
                )
                previous = live.pop(old, None)
                if previous:
                    current -= previous[0]
                    freed += previous[0]
                    free_count += 1
                    series[bucket].update(freed_bytes=previous[0], frees=1)
            if key in live:
                fail("Allocation reuses live address without a free event")
            frames = row.get("stack") or [f"pid:{key[0]}", event]
            if not isinstance(frames, list) or len(frames) > 128:
                fail("Memory stack must be a bounded array")
            live[key] = (size, timestamp, frames, event)
            allocated, current, alloc_count = (
                allocated + size,
                current + size,
                alloc_count + 1,
            )
            peak = max(peak, current)
            stacks.append((frames, size))
            series[bucket].update(allocated_bytes=size, allocations=1)
        elif event in {"free", "munmap"}:
            key = (integer(row, "pid"), address(row))
            if key[1] == 0:
                continue  # 观测到的 free(NULL) 不释放对象，不应改变存活分配统计。
            previous = live.get(key)
            released = 0
            if event == "munmap" and "size" in row:
                length = integer(row, "size")
                if previous and previous[0] == length:
                    released = live.pop(key)[0]
                else:
                    stop = key[1] + length
                    changes = []
                    for candidate, allocation in live.items():
                        mapping_work += 1
                        if mapping_work > 20_000_000:
                            raise AnalysisError(
                                "INPUT_LIMIT_EXCEEDED",
                                "Memory mapping reconciliation exceeds work limit",
                            )
                        if candidate[0] != key[0] or allocation[3] != "mmap":
                            continue
                        begin, end = candidate[1], candidate[1] + allocation[0]
                        overlap_begin, overlap_end = max(begin, key[1]), min(end, stop)
                        if overlap_begin < overlap_end:
                            changes.append(
                                (candidate, allocation, overlap_begin, overlap_end)
                            )
                    for candidate, allocation, begin, end in changes:
                        del live[candidate]
                        released += end - begin
                        if candidate[1] < begin:
                            live[candidate] = (begin - candidate[1], *allocation[1:])
                        if end < candidate[1] + allocation[0]:
                            live[(candidate[0], end)] = (
                                candidate[1] + allocation[0] - end,
                                *allocation[1:],
                            )
            elif previous:
                released = live.pop(key)[0]
            if released or (event == "free" and previous is not None):
                current -= released
                freed += released
                free_count += 1
                series[bucket].update(freed_bytes=released, frees=1)
            else:
                unknown_frees += 1
        elif event in {"pagefault", "minor_fault", "major_fault"}:
            category = (
                "unknown"
                if event == "pagefault" and "major" not in row
                else "major"
                if event == "major_fault" or bool(row.get("major", False))
                else "minor"
            )
            faults[category] += 1
            series[bucket].update(pagefaults=1)
        elif event == "lost":
            faults["lost_events"] += integer(row, "count")
        else:
            fail("Unsupported memory event type")
        series[bucket]["live_bytes"] = current
    survivors = sorted(live.items(), key=lambda item: (-item[1][0], item[0]))[:100]
    parsed = stack_result(stacks, unit="bytes", metadata={"format": "memory-jsonl/v1"})
    parsed["sections"] = {
        "memory": {
            "allocated_bytes": allocated,
            "freed_bytes": freed,
            "live_bytes": current,
            "peak_live_bytes": peak,
            "allocations": alloc_count,
            "frees": free_count,
            "unknown_frees": unknown_frees,
            "failed_allocations": failed_allocations,
            "pagefaults": {
                "minor": faults["minor"],
                "major": faults["major"],
                "unknown": faults["unknown"],
                "total": faults["minor"] + faults["major"] + faults["unknown"],
            },
            "lost_events": faults["lost_events"],
            "survivors": [
                {
                    "pid": key[0],
                    "address": hex(key[1]),
                    "bytes": value[0],
                    "age_ns": last - value[1],
                    "stack": value[2],
                    "classification": "unfreed_during_observation",
                }
                for key, value in survivors
            ],
            "timeseries": [
                {"time_ns": bucket * 1_000_000_000, **value}
                for bucket, value in sorted(series.items())
            ][:3600],
            "limitations": [
                "观测结束仍存活不等于内存泄漏；采集前分配和丢失事件可能造成未知 free。"
            ],
        }
    }
    return parsed


def analyze_resources(raw):
    value = document(raw)
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("samples"), list)
        or len(value["samples"]) > 3600
    ):
        fail("Resource snapshot requires at most 3600 samples")
    samples = []
    for sample in value["samples"]:
        if not isinstance(sample, dict):
            fail("Resource sample must be an object")
        row = {
            "timestamp_ns": integer(sample, "timestamp_ns", sample.get("time_ns", 0))
        }
        for key in (
            "rss_bytes",
            "vms_bytes",
            "read_bytes",
            "write_bytes",
            "threads",
            "fd_count",
            "network_rx_bytes",
            "network_tx_bytes",
            "context_switches",
            "voluntary_context_switches",
            "involuntary_context_switches",
            "collector_cpu_ticks",
            "collector_rss_bytes",
            "memory_total_bytes",
            "memory_used_bytes",
            "cpu_ticks",
            "cpu_total_ticks",
            "cpu_idle_ticks",
            "pid",
        ):
            row[key] = integer(sample, key) if sample.get(key) is not None else None
        cpu = sample.get("cpu_percent")
        if cpu is not None and (
            isinstance(cpu, bool)
            or not isinstance(cpu, (int, float))
            or not math.isfinite(cpu)
            or cpu < 0
            or cpu > 100000
        ):
            fail("Resource CPU percentage is invalid")
        row["cpu_percent"] = cpu
        load_average = sample.get("load_average")
        if load_average is not None and (
            not isinstance(load_average, str) or len(load_average) > 256
        ):
            fail("Host load average must be a bounded string")
        row["load_average"] = load_average
        io_available = sample.get("io_available")
        if io_available is not None and not isinstance(io_available, bool):
            fail("I/O availability must be boolean")
        row["io_available"] = io_available
        samples.append(row)
    peaks = {
        key: max(
            (sample[key] for sample in samples if sample[key] is not None), default=None
        )
        for key in (
            "cpu_percent",
            "rss_bytes",
            "vms_bytes",
            "threads",
            "fd_count",
            "memory_total_bytes",
            "memory_used_bytes",
        )
    }
    network_scope = value.get("network_scope")
    context_switch_scope = value.get("context_switch_scope")
    if context_switch_scope is not None and (
        not isinstance(context_switch_scope, str) or len(context_switch_scope) > 256
    ):
        fail("Context switch scope must be a bounded string")
    if network_scope is not None and (
        not isinstance(network_scope, str) or len(network_scope) > 256
    ):
        fail("Resource network scope must be a bounded string")
    overhead = value.get("collector_overhead")
    if overhead is not None:
        if not isinstance(overhead, dict):
            fail("Collector overhead must be an object")
        sanitized = {}
        for key in (
            "user_cpu_seconds",
            "system_cpu_seconds",
            "children_user_cpu_seconds",
            "children_system_cpu_seconds",
        ):
            number = overhead.get(key)
            if number is not None and (
                isinstance(number, bool)
                or not isinstance(number, (int, float))
                or not math.isfinite(number)
                or not 0 <= number <= 2**53 - 1
            ):
                fail(
                    "Collector overhead CPU value must be a bounded nonnegative number"
                )
            sanitized[key] = number
        scope = overhead.get("scope")
        if scope is not None and (not isinstance(scope, str) or len(scope) > 256):
            fail("Collector overhead scope must be a bounded string")
        overhead = {**sanitized, "scope": scope}
    dmesg = value.get(
        "dmesg",
        {
            "status": "unavailable",
            "lines": [],
            "reason": "Not included in the input artifact",
            "truncated": False,
        },
    )
    if not isinstance(dmesg, dict) or dmesg.get("status") not in {
        "ok",
        "unavailable",
        "permission_denied",
    }:
        fail("Kernel log availability status is invalid")
    lines = dmesg.get("lines", [])
    if (
        not isinstance(lines, list)
        or len(lines) > 200
        or any(not isinstance(line, str) or len(line) > 1024 for line in lines)
        or sum(len(line.encode("utf8")) for line in lines) > 64 * 1024
    ):
        fail("Kernel log excerpt exceeds its bounded line or byte limit")
    reason = dmesg.get("reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > 1024):
        fail("Kernel log reason must be a bounded string")
    if not isinstance(dmesg.get("truncated", False), bool):
        fail("Kernel log truncation must be boolean")
    if dmesg["status"] != "ok" and lines:
        fail("Unavailable kernel logs cannot contain successful log lines")
    dmesg = {
        "status": dmesg["status"],
        "lines": lines,
        "reason": reason,
        "truncated": dmesg.get("truncated", False),
    }
    parsed = stack_result([], unit="bytes", metadata={"format": "resources-json/v1"})
    parsed["sections"] = {
        "resources": {
            "samples": samples,
            "peaks": peaks,
            "sample_count": len(samples),
            "network_scope": network_scope,
            "context_switch_scope": context_switch_scope,
            "collector_overhead": overhead,
            "dmesg": dmesg,
            "limitations": ["进程快照可能遗漏采样间隔内的短暂峰值。"],
        }
    }
    return parsed


def analyze_script(raw):
    value = document(raw)
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("template"), str)
        or not value["template"]
        or len(value["template"]) > 128
    ):
        fail("Script result requires a fixed template identifier")
    exit_code = value.get("exit_code")
    if (
        not isinstance(exit_code, int)
        or isinstance(exit_code, bool)
        or not -255 <= exit_code <= 255
    ):
        fail("Script result exit code is invalid")
    output = {key: value.get(key, "") for key in ("stdout", "stderr")}
    if any(
        not isinstance(text, str) or len(text.encode()) > 1024 * 1024
        for text in output.values()
    ):
        raise AnalysisError("INPUT_LIMIT_EXCEEDED", "Script output exceeds bound")
    if exit_code:
        raise AnalysisError(
            "SCRIPT_FAILED", "Fixed template exited with a nonzero status"
        )
    parsed = stack_result([], unit="operations", metadata={"format": "script-json/v1"})
    parsed["sections"] = {
        "script": {
            "template": value["template"],
            "exit_code": exit_code,
            **output,
            "duration_ms": integer(value, "duration_ms"),
            "truncated": bool(value.get("truncated", False)),
        }
    }
    return parsed
