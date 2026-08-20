"""Allow-listed downstream latency target for cross-service campaigns."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class LatencyFault:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._delay_ms = 0
        self._deadline: float | None = None
        self._requests = 0

    def start(self, delay_ms: int, duration_seconds: float | None) -> None:
        with self._lock:
            self._delay_ms = max(100, min(delay_ms, 3000))
            self._deadline = (
                time.monotonic() + duration_seconds
                if duration_seconds and duration_seconds > 0
                else None
            )

    def stop(self) -> None:
        with self._lock:
            self._delay_ms = 0
            self._deadline = None

    def before_work(self) -> int:
        with self._lock:
            if self._deadline is not None and time.monotonic() >= self._deadline:
                self._delay_ms = 0
                self._deadline = None
            delay_ms = self._delay_ms
            self._requests += 1
        if delay_ms:
            time.sleep(delay_ms / 1000)
        return delay_ms

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "downstream_fault_active": self._delay_ms > 0,
                "downstream_delay_ms": self._delay_ms,
                "downstream_request_count": self._requests,
            }


FAULT = LatencyFault()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(200, {"status": "ok", "service": "downstream-service"})
            return
        if self.path == "/work":
            applied_delay_ms = FAULT.before_work()
            self._json(
                200,
                {
                    "status": "ok",
                    "service": "downstream-service",
                    "applied_delay_ms": applied_delay_ms,
                    **FAULT.snapshot(),
                },
            )
            return
        if self.path == "/snapshot":
            self._json(200, FAULT.snapshot())
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/faults/latency/start":
            payload = self._read_json()
            FAULT.start(
                int(payload.get("delay_ms") or 750),
                float(payload.get("duration_seconds") or 0) or None,
            )
            self._json(200, {"status": "started", **FAULT.snapshot()})
            return
        if self.path == "/faults/latency/stop":
            FAULT.stop()
            self._json(200, {"status": "stopped", **FAULT.snapshot()})
            return
        self._json(404, {"error": "not_found"})

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        value = json.loads(self.rfile.read(length).decode("utf-8"))
        return value if isinstance(value, dict) else {}

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8082), Handler).serve_forever()
