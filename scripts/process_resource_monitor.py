"""Sample only the owned fixture PID; missing observations never become zero."""
from __future__ import annotations

import json
import math
from pathlib import Path
import statistics
import threading
import time

import psutil


def summarize_resources(rows, soak_seconds, interval=1.0):
    valid = [r for r in rows if r.get("status") == "OK"]
    soak = [r for r in valid if r["phase"] == "soak"]
    errors = [r for r in rows if r.get("status") != "OK"]
    reasons = []
    expected = max(1, math.floor(soak_seconds / interval))
    if errors:
        reasons.append("RESOURCE_OBSERVATION_FAILED")
    if len(soak) < max(5, math.floor(expected * .8)):
        reasons.append("INSUFFICIENT_RESOURCE_SAMPLES")
    offsets = [r["elapsed_seconds"] for r in soak]
    if any(b - a > interval * 5 for a, b in zip(offsets, offsets[1:])):
        reasons.append("RESOURCE_SAMPLING_GAP")
    invalid = bool(reasons)
    trends = {}
    if len(soak) >= 5:
        count = max(1, len(soak) // 3)
        for key, limit in (("rss_bytes", 32 * 1024 * 1024), ("threads", 8), ("handles_or_fds", 32)):
            first = statistics.median(r[key] for r in soak[:count])
            last = statistics.median(r[key] for r in soak[-count:])
            trends[key] = {"first_third_median": first, "last_third_median": last,
                           "growth": last - first, "growth_limit": limit,
                           "peak": max(r[key] for r in soak)}
            if last - first > limit:
                reasons.append(f"{key.upper()}_GROWTH")
    return {"status": "INVALID" if invalid else "GROWTH_DETECTED" if reasons else "PASSED",
            "reasons": reasons, "samples": len(rows), "soak_samples": len(soak),
            "error_samples": len(errors), "soak_trends": trends,
            "cpu_percent_scope": "100 percent equals one logical core; first sample is null",
            "handle_kind": valid[0]["handle_kind"] if valid else None,
            "cpu_peak_percent": max((r["cpu_percent"] for r in soak if r["cpu_percent"] is not None), default=None),
            "boundary": "Growth screen for this PID and window, not proof of absence of leaks"}


class ResourceMonitor:
    def __init__(self, pid: int, output: Path, interval=1.0):
        self.process = psutil.Process(pid)
        self.created = self.process.create_time()
        self.pid = pid
        self.output = output
        self.interval = interval
        self.rows = []
        self.phase = "warmup"
        self.stop_event = threading.Event()
        self.thread = None
        self.previous = None
        self.origin = time.perf_counter()
        self.failure = None

    def sample(self):
        now = time.perf_counter()
        row = {"pid": self.pid, "create_time": self.created, "phase": self.phase,
               "elapsed_seconds": now - self.origin, "status": "OK"}
        try:
            # is_running includes psutil's PID reuse identity check.
            if not self.process.is_running() or self.process.create_time() != self.created:
                raise RuntimeError("target identity changed or exited")
            with self.process.oneshot():
                cpu = self.process.cpu_times()
                total = cpu.user + cpu.system
                row["rss_bytes"] = self.process.memory_info().rss
                row["threads"] = self.process.num_threads()
                if hasattr(self.process, "num_handles"):
                    row["handle_kind"] = "windows_handles"
                    row["handles_or_fds"] = self.process.num_handles()
                else:
                    row["handle_kind"] = "posix_file_descriptors"
                    row["handles_or_fds"] = self.process.num_fds()
            row["cpu_seconds"] = total
            row["cpu_percent"] = None
            if self.previous is not None:
                before, seconds = self.previous
                if total < seconds or now <= before:
                    raise RuntimeError("nonmonotonic resource counters")
                row["cpu_percent"] = 100 * (total - seconds) / (now - before)
            self.previous = now, total
        except Exception as exc:
            row = {**{k:row[k] for k in ("pid", "create_time", "phase", "elapsed_seconds")},
                   "status": "MISSING", "error": type(exc).__name__}
            self.previous = None
        return row

    def __enter__(self):
        self.stream = self.output.open("x", encoding="utf-8")

        def collect():
            try:
                while not self.stop_event.is_set():
                    row = self.sample()
                    self.rows.append(row)
                    self.stream.write(json.dumps(row, allow_nan=False) + "\n")
                    self.stream.flush()
                    self.stop_event.wait(self.interval)
            except Exception as exc:
                self.failure = type(exc).__name__

        self.thread = threading.Thread(target=collect, name="fixture-resource-monitor", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop_event.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("resource monitor failed to stop")
        self.stream.close()
        if self.failure:
            raise RuntimeError(f"resource evidence write failed: {self.failure}")
        return False
