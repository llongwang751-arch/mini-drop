"""Controllable Python CPU target used by profiling and diagnosis campaigns.

The service deliberately exposes only a tiny, allow-listed fault API.  It is
not a generic command runner: a campaign may start/stop the CPU loop and read
process snapshots, which keeps the demo deterministic and makes cleanup
verifiable.
"""

from __future__ import annotations

import hashlib
import gc
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urllib_request


class CpuFault:
    def __init__(self) -> None:
        self._enabled = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._operations = 0
        self._started_at: float | None = None
        self._deadline: float | None = None
        self._last_wall = time.monotonic()
        self._last_cpu = time.process_time()
        self._thread = threading.Thread(target=self._run, name="cpu-hotspot", daemon=True)
        self._thread.start()
        if os.getenv("CPU_HOTSPOT_ACTIVE", "1") == "1":
            self.start()

    def start(self, duration_seconds: float | None = None) -> None:
        with self._lock:
            self._started_at = time.time()
            self._deadline = (
                time.monotonic() + duration_seconds
                if duration_seconds and duration_seconds > 0
                else None
            )
        self._enabled.set()

    def stop(self) -> None:
        self._enabled.clear()
        with self._lock:
            self._deadline = None

    def snapshot(self) -> dict[str, object]:
        now_wall = time.monotonic()
        now_cpu = time.process_time()
        with self._lock:
            wall_delta = max(now_wall - self._last_wall, 0.001)
            cpu_delta = max(now_cpu - self._last_cpu, 0.0)
            self._last_wall = now_wall
            self._last_cpu = now_cpu
            operations = self._operations
            started_at = self._started_at
            deadline = self._deadline
        host_pid = _host_pid()
        return {
            "timestamp": time.time(),
            "fault": "CPU_HOTSPOT",
            "fault_active": self._enabled.is_set(),
            "process_cpu_percent": round(cpu_delta / wall_delta * 100.0, 2),
            "operation_count": operations,
            "pid": os.getpid(),
            "host_pid": host_pid,
            "started_at": started_at,
            "auto_stop_remaining_seconds": (
                round(max(deadline - now_wall, 0.0), 2) if deadline else None
            ),
        }

    def _run(self) -> None:
        payload = b"mini-drop-python-hotspot"
        index = 0
        while not self._stop.is_set():
            if not self._enabled.wait(timeout=0.1):
                continue
            with self._lock:
                deadline = self._deadline
            if deadline is not None and time.monotonic() >= deadline:
                self.stop()
                continue
            for _ in range(600):
                payload = hashlib.sha256(payload + str(index).encode()).digest()
                index += 1
            with self._lock:
                self._operations += 600


def _host_pid() -> int:
    """Return the outer PID when Linux exposes the NSpid namespace chain."""

    try:
        for line in open("/proc/self/status", encoding="utf-8"):
            if line.startswith("NSpid:"):
                values = [int(value) for value in line.split()[1:]]
                return values[0] if values else os.getpid()
    except (OSError, ValueError):
        pass
    return os.getpid()


FAULT = CpuFault()


def source_hot_function(seed: int) -> int:
    """Deliberately expensive Python function with a stable source location."""

    value = seed
    for index in range(120_000):
        value = ((value * 1_103_515_245) + index + 12_345) & 0x7FFFFFFF
        if index % 10_000 == 0:
            time.sleep(0)
    return value


class SourceHotspotFault:
    """Bounded source-level hotspot with an independent stack sampler."""

    def __init__(self) -> None:
        self._enabled = threading.Event()
        self._lock = threading.Lock()
        self._deadline: float | None = None
        self._samples: Counter[tuple[str, str, int]] = Counter()
        self._worker = threading.Thread(target=self._run, name="source-hot-function", daemon=True)
        self._sampler = threading.Thread(target=self._sample, name="source-stack-sampler", daemon=True)
        self._worker.start()
        self._sampler.start()

    def start(self, duration_seconds: float | None = None) -> None:
        with self._lock:
            self._deadline = (
                time.monotonic() + duration_seconds
                if duration_seconds and duration_seconds > 0
                else None
            )
            self._samples.clear()
        self._enabled.set()

    def stop(self) -> None:
        self._enabled.clear()
        with self._lock:
            self._deadline = None

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            top = self._samples.most_common(1)
            total = sum(self._samples.values())
            deadline = self._deadline
        if top:
            (source_file, function, source_line), count = top[0]
        else:
            source_file, function, source_line, count = "", "", 0, 0
        return {
            "source_fault_active": self._enabled.is_set(),
            "source_profile_samples": total,
            "hot_function": function,
            "source_file": source_file,
            "source_line": source_line,
            "hot_function_samples": count,
            "source_auto_stop_remaining_seconds": (
                round(max(deadline - time.monotonic(), 0.0), 2) if deadline else None
            ),
        }

    def _run(self) -> None:
        value = 7
        while True:
            if not self._enabled.wait(timeout=0.1):
                continue
            with self._lock:
                deadline = self._deadline
            if deadline is not None and time.monotonic() >= deadline:
                self.stop()
                continue
            value = source_hot_function(value)

    def _sample(self) -> None:
        while True:
            if not self._enabled.wait(timeout=0.1):
                continue
            frame = sys._current_frames().get(self._worker.ident)
            selected = None
            while frame is not None:
                if frame.f_code.co_name == "source_hot_function":
                    selected = (
                        os.path.basename(frame.f_code.co_filename),
                        frame.f_code.co_name,
                        frame.f_lineno,
                    )
                    break
                frame = frame.f_back
            if selected is not None:
                with self._lock:
                    self._samples[selected] += 1
            time.sleep(0.005)


SOURCE_FAULT = SourceHotspotFault()


class MemoryFault:
    """Bounded retained-memory fault with an explicit cleanup switch."""

    def __init__(self) -> None:
        self._enabled = threading.Event()
        self._lock = threading.Lock()
        self._buffers: list[bytearray] = []
        self._target_bytes = 0
        self._deadline: float | None = None
        self._thread = threading.Thread(
            target=self._run, name="memory-hotspot", daemon=True
        )
        self._thread.start()

    def start(self, duration_seconds: float | None = None, megabytes: int = 96) -> None:
        bounded_mb = min(max(int(megabytes), 16), 256)
        with self._lock:
            self._target_bytes = bounded_mb * 1024 * 1024
            self._deadline = (
                time.monotonic() + duration_seconds
                if duration_seconds and duration_seconds > 0
                else None
            )
        self._enabled.set()

    def stop(self) -> None:
        self._enabled.clear()
        with self._lock:
            self._deadline = None
            self._target_bytes = 0
            self._buffers.clear()
        gc.collect()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            retained = sum(len(item) for item in self._buffers)
            deadline = self._deadline
        return {
            "memory_fault_active": self._enabled.is_set(),
            "process_rss_mb": _current_rss_mb(),
            "retained_memory_mb": round(retained / 1024 / 1024, 2),
            "memory_auto_stop_remaining_seconds": (
                round(max(deadline - time.monotonic(), 0.0), 2) if deadline else None
            ),
        }

    def _run(self) -> None:
        chunk_bytes = 4 * 1024 * 1024
        while True:
            if not self._enabled.wait(timeout=0.1):
                continue
            with self._lock:
                deadline = self._deadline
                retained = sum(len(item) for item in self._buffers)
                target = self._target_bytes
            if deadline is not None and time.monotonic() >= deadline:
                self.stop()
                continue
            if retained < target:
                allocation = bytearray(min(chunk_bytes, target - retained))
                allocation[::4096] = b"\x01" * (len(allocation[::4096]))
                with self._lock:
                    self._buffers.append(allocation)
            time.sleep(0.08)


def _current_rss_mb() -> float:
    try:
        pages = int(open("/proc/self/statm", encoding="utf-8").read().split()[1])
        return round(pages * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024, 2)
    except (OSError, ValueError, IndexError):
        return 0.0


MEMORY_FAULT = MemoryFault()


class IoFault:
    """Bounded synchronous-write workload used by the I/O Campaign."""

    def __init__(self) -> None:
        self._enabled = threading.Event()
        self._lock = threading.Lock()
        self._deadline: float | None = None
        self._bytes_written = 0
        self._path = "/tmp/mini-drop-io-fault.bin"
        self._thread = threading.Thread(target=self._run, name="io-hotspot", daemon=True)
        self._thread.start()

    def start(self, duration_seconds: float | None = None) -> None:
        with self._lock:
            self._deadline = (
                time.monotonic() + duration_seconds
                if duration_seconds and duration_seconds > 0
                else None
            )
            self._bytes_written = 0
        self._enabled.set()

    def stop(self) -> None:
        self._enabled.clear()
        with self._lock:
            self._deadline = None
        try:
            os.remove(self._path)
        except FileNotFoundError:
            pass

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            written = self._bytes_written
            deadline = self._deadline
        return {
            "io_fault_active": self._enabled.is_set(),
            "io_workload_bytes": written,
            "process_write_bytes": _process_write_bytes(),
            "io_auto_stop_remaining_seconds": (
                round(max(deadline - time.monotonic(), 0.0), 2) if deadline else None
            ),
        }

    def _run(self) -> None:
        chunk = b"mini-drop-io" * 8192
        while True:
            if not self._enabled.wait(timeout=0.1):
                continue
            with self._lock:
                deadline = self._deadline
            if deadline is not None and time.monotonic() >= deadline:
                self.stop()
                continue
            try:
                with open(self._path, "ab", buffering=0) as handle:
                    handle.write(chunk)
                    os.fdatasync(handle.fileno())
                with self._lock:
                    self._bytes_written += len(chunk)
                if os.path.getsize(self._path) >= 128 * 1024 * 1024:
                    os.remove(self._path)
            except OSError:
                self.stop()
            time.sleep(0.02)


def _process_write_bytes() -> int:
    try:
        for line in open("/proc/self/io", encoding="utf-8"):
            if line.startswith("write_bytes:"):
                return int(line.split(":", 1)[1].strip())
    except (OSError, ValueError):
        pass
    return 0


IO_FAULT = IoFault()


def _boot_id() -> str:
    try:
        return open("/proc/sys/kernel/random/boot_id", encoding="utf-8").read().strip()
    except OSError:
        return "unknown"


def _process_ticks(pid: int | None) -> int:
    if not pid:
        return 0
    try:
        fields = open(f"/proc/{pid}/stat", encoding="utf-8").read().split()
        return int(fields[13]) + int(fields[14])
    except (OSError, ValueError, IndexError):
        return 0


class NoisyNeighborFault:
    """A separate same-host process that consumes CPU until explicitly stopped."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._deadline: float | None = None

    def start(self, duration_seconds: float | None = None) -> None:
        self.stop()
        seconds = max(float(duration_seconds or 8), 2.0)
        code = (
            "import hashlib,time; end=time.time()+%r; data=b'noisy-neighbor'; i=0\n"
            "while time.time()<end:\n"
            " data=hashlib.sha256(data+str(i).encode()).digest(); i+=1\n"
        ) % seconds
        process = subprocess.Popen([sys.executable, "-c", code])
        with self._lock:
            self._process = process
            self._deadline = time.monotonic() + seconds

    def stop(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            self._deadline = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            process = self._process
            deadline = self._deadline
        active = bool(process and process.poll() is None)
        pid = process.pid if process else None
        return {
            "noisy_neighbor_active": active,
            "peer_pid": pid,
            "peer_cpu_ticks": _process_ticks(pid),
            "peer_boot_id": _boot_id(),
            "target_boot_id": _boot_id(),
            "same_host_verified": active and bool(pid),
            "noisy_auto_stop_remaining_seconds": (
                round(max(deadline - time.monotonic(), 0.0), 2) if deadline else None
            ),
        }


NOISY_FAULT = NoisyNeighborFault()


class RateFault:
    """Bounded producer/consumer model for traffic saturation and queue backlog."""

    def __init__(self, name: str, capacity: int = 64) -> None:
        self.name = name
        self._enabled = threading.Event()
        self._lock = threading.Lock()
        self._queue: queue.Queue[float] = queue.Queue(maxsize=capacity)
        self._deadline: float | None = None
        self._offered = 0
        self._completed = 0
        self._rejected = 0
        self._latency_total_ms = 0.0
        self._started = time.monotonic()
        self._producer = threading.Thread(target=self._produce, name=f"{name}-producer", daemon=True)
        self._consumer = threading.Thread(target=self._consume, name=f"{name}-consumer", daemon=True)
        self._producer.start()
        self._consumer.start()

    def start(self, duration_seconds: float | None = None) -> None:
        self.stop()
        with self._lock:
            self._deadline = time.monotonic() + max(float(duration_seconds or 8), 2.0)
            self._offered = self._completed = self._rejected = 0
            self._latency_total_ms = 0.0
            self._started = time.monotonic()
        self._enabled.set()

    def stop(self) -> None:
        self._enabled.clear()
        with self._lock:
            self._deadline = None
            self._offered = self._completed = self._rejected = 0
            self._latency_total_ms = 0.0
            self._started = time.monotonic()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def snapshot(self, prefix: str) -> dict[str, object]:
        with self._lock:
            elapsed = max(time.monotonic() - self._started, 0.001)
            offered, completed, rejected = self._offered, self._completed, self._rejected
            latency = self._latency_total_ms / completed if completed else 0.0
            deadline = self._deadline
        depth = self._queue.qsize()
        common = {
            f"{prefix}_fault_active": self._enabled.is_set(),
            f"{prefix}_offered_rps": round(offered / elapsed, 2),
            f"{prefix}_completed_rps": round(completed / elapsed, 2),
            f"{prefix}_rejected_requests": rejected,
            f"{prefix}_queue_depth": depth,
            f"{prefix}_latency_ms": round(latency, 2),
            f"{prefix}_auto_stop_remaining_seconds": (
                round(max(deadline - time.monotonic(), 0.0), 2) if deadline else None
            ),
        }
        if prefix == "queue":
            common.update({
                "producer_rate": round(offered / elapsed, 2),
                "consumer_rate": round(completed / elapsed, 2),
                "queue_lag": depth,
            })
        return common

    def _produce(self) -> None:
        while True:
            if not self._enabled.wait(timeout=0.1):
                continue
            with self._lock:
                deadline = self._deadline
            if deadline and time.monotonic() >= deadline:
                self.stop()
                continue
            with self._lock:
                self._offered += 1
            try:
                self._queue.put_nowait(time.monotonic())
            except queue.Full:
                with self._lock:
                    self._rejected += 1
            time.sleep(0.001 if self.name == "load" else 0.004)

    def _consume(self) -> None:
        while True:
            if not self._enabled.wait(timeout=0.1):
                continue
            try:
                queued_at = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            # CPU work makes load saturation observable; queue mode deliberately drains slower.
            rounds = 2_000
            value = b"mini-drop-rate-fault"
            for _ in range(rounds):
                value = hashlib.sha256(value).digest()
            time.sleep(0.012 if self.name == "load" else 0.025)
            with self._lock:
                self._completed += 1
                self._latency_total_ms += (time.monotonic() - queued_at) * 1000


LOAD_FAULT = RateFault("load", capacity=48)
QUEUE_FAULT = RateFault("queue", capacity=128)


DOWNSTREAM_URL = os.getenv("DOWNSTREAM_URL", "http://downstream-service:8082").rstrip("/")
NETWORK_PROXY_URL = os.getenv("NETWORK_PROXY_URL", "http://network-proxy:8083").rstrip("/")


def _downstream_request(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib_request.Request(
        f"{DOWNSTREAM_URL}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    with urllib_request.urlopen(req, timeout=5) as response:
        value = json.loads(response.read().decode("utf-8"))
    result = value if isinstance(value, dict) else {}
    result["upstream_latency_ms"] = round((time.monotonic() - started) * 1000, 2)
    result["host_pid"] = _host_pid()
    return result


def _network_request(method: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib_request.Request(
        f"{NETWORK_PROXY_URL}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    with urllib_request.urlopen(req, timeout=5) as response:
        value = json.loads(response.read().decode("utf-8"))
    result = value if isinstance(value, dict) else {}
    result["upstream_latency_ms"] = round((time.monotonic() - started) * 1000, 2)
    result["host_pid"] = _host_pid()
    return result


def _snapshot() -> dict[str, object]:
    return {
        **FAULT.snapshot(),
        **SOURCE_FAULT.snapshot(),
        **MEMORY_FAULT.snapshot(),
        **IO_FAULT.snapshot(),
        **NOISY_FAULT.snapshot(),
        **LOAD_FAULT.snapshot("load"),
        **QUEUE_FAULT.snapshot("queue"),
        "boot_id": _boot_id(),
    }


class ControlHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        if self.path == "/health":
            self._json(200, {"status": "ok", "runtime": "python"})
            return
        if self.path == "/snapshot":
            self._json(200, _snapshot())
            return
        if self.path == "/dependencies/downstream/probe":
            try:
                self._json(200, _downstream_request("GET", "/work"))
            except Exception as exc:
                self._json(502, {"error": "downstream_probe_failed", "detail": str(exc), "host_pid": _host_pid()})
            return
        if self.path == "/dependencies/network/probe":
            try:
                self._json(200, _network_request("GET", "/work"))
            except Exception as exc:
                self._json(502, {"error": "network_probe_failed", "detail": str(exc), "host_pid": _host_pid()})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        if self.path == "/faults/cpu/start":
            payload = self._read_json()
            duration = float(payload.get("duration_seconds") or 0) or None
            FAULT.start(duration)
            self._json(200, {"status": "started", **FAULT.snapshot()})
            return
        if self.path == "/faults/cpu/stop":
            FAULT.stop()
            self._json(200, {"status": "stopped", **_snapshot()})
            return
        if self.path == "/faults/source/start":
            payload = self._read_json()
            duration = float(payload.get("duration_seconds") or 0) or None
            SOURCE_FAULT.start(duration)
            self._json(200, {"status": "started", **_snapshot()})
            return
        if self.path == "/faults/source/stop":
            SOURCE_FAULT.stop()
            self._json(200, {"status": "stopped", **_snapshot()})
            return
        if self.path == "/faults/memory/start":
            payload = self._read_json()
            duration = float(payload.get("duration_seconds") or 0) or None
            megabytes = int(payload.get("megabytes") or 96)
            MEMORY_FAULT.start(duration, megabytes)
            self._json(200, {"status": "started", **_snapshot()})
            return
        if self.path == "/faults/memory/stop":
            MEMORY_FAULT.stop()
            self._json(200, {"status": "stopped", **_snapshot()})
            return
        if self.path == "/faults/io/start":
            payload = self._read_json()
            duration = float(payload.get("duration_seconds") or 0) or None
            IO_FAULT.start(duration)
            self._json(200, {"status": "started", **_snapshot()})
            return
        if self.path == "/faults/io/stop":
            IO_FAULT.stop()
            self._json(200, {"status": "stopped", **_snapshot()})
            return
        if self.path == "/faults/downstream/start":
            payload = self._read_json()
            duration = float(payload.get("duration_seconds") or 0) or None
            delay_ms = int(payload.get("delay_ms") or 750)
            self._json(200, _downstream_request("POST", "/faults/latency/start", {"duration_seconds": duration, "delay_ms": delay_ms}))
            return
        if self.path == "/faults/downstream/stop":
            self._json(200, _downstream_request("POST", "/faults/latency/stop", {}))
            return
        if self.path == "/faults/network/start":
            payload = self._read_json()
            duration = float(payload.get("duration_seconds") or 0) or None
            delay_ms = int(payload.get("delay_ms") or 650)
            self._json(200, _network_request("POST", "/faults/latency/start", {"duration_seconds": duration, "delay_ms": delay_ms}))
            return
        if self.path == "/faults/network/stop":
            self._json(200, _network_request("POST", "/faults/latency/stop", {}))
            return
        if self.path == "/faults/noisy/start":
            payload = self._read_json()
            NOISY_FAULT.start(float(payload.get("duration_seconds") or 0) or None)
            self._json(200, {"status": "started", **_snapshot()})
            return
        if self.path == "/faults/noisy/stop":
            NOISY_FAULT.stop()
            self._json(200, {"status": "stopped", **_snapshot()})
            return
        if self.path == "/faults/load/start":
            payload = self._read_json()
            LOAD_FAULT.start(float(payload.get("duration_seconds") or 0) or None)
            self._json(200, {"status": "started", **_snapshot()})
            return
        if self.path == "/faults/load/stop":
            LOAD_FAULT.stop()
            self._json(200, {"status": "stopped", **_snapshot()})
            return
        if self.path == "/faults/queue/start":
            payload = self._read_json()
            QUEUE_FAULT.start(float(payload.get("duration_seconds") or 0) or None)
            self._json(200, {"status": "started", **_snapshot()})
            return
        if self.path == "/faults/queue/stop":
            QUEUE_FAULT.stop()
            self._json(200, {"status": "stopped", **_snapshot()})
            return
        self._json(404, {"error": "not_found"})

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        try:
            value = json.loads(self.rfile.read(length))
            return value if isinstance(value, dict) else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8081), ControlHandler).serve_forever()
