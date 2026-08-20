"""Small allow-listed HTTP proxy that injects delay on a dependency edge."""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urllib_request


UPSTREAM_URL = os.getenv("UPSTREAM_URL", "http://downstream-service:8082").rstrip("/")


class EdgeLatency:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._delay_ms = 0
        self._deadline: float | None = None
        self._forwarded = 0

    def start(self, delay_ms: int, duration_seconds: float | None) -> None:
        with self._lock:
            self._delay_ms = max(100, min(delay_ms, 3000))
            self._deadline = time.monotonic() + duration_seconds if duration_seconds else None

    def stop(self) -> None:
        with self._lock:
            self._delay_ms = 0
            self._deadline = None

    def before_forward(self) -> int:
        with self._lock:
            if self._deadline is not None and time.monotonic() >= self._deadline:
                self._delay_ms = 0
                self._deadline = None
            delay_ms = self._delay_ms
            self._forwarded += 1
        if delay_ms:
            time.sleep(delay_ms / 1000)
        return delay_ms

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "network_fault_active": self._delay_ms > 0,
                "network_delay_ms": self._delay_ms,
                "network_forward_count": self._forwarded,
            }


FAULT = EdgeLatency()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(200, {"status": "ok", "service": "network-proxy"})
            return
        if self.path == "/snapshot":
            self._json(200, FAULT.snapshot())
            return
        if self.path == "/work":
            applied = FAULT.before_forward()
            started = time.monotonic()
            with urllib_request.urlopen(f"{UPSTREAM_URL}/work", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            result = payload if isinstance(payload, dict) else {}
            result.update(
                {
                    **FAULT.snapshot(),
                    "network_proxy_delay_ms": applied,
                    "proxy_upstream_latency_ms": round((time.monotonic() - started) * 1000, 2),
                }
            )
            self._json(200, result)
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/faults/latency/start":
            payload = self._read_json()
            FAULT.start(
                int(payload.get("delay_ms") or 650),
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
    ThreadingHTTPServer(("0.0.0.0", 8083), Handler).serve_forever()
