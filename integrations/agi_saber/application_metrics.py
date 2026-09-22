"""Bounded ASGI request metrics for Mini-Drop process-bound collection."""
from __future__ import annotations

from collections import deque
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Any


class ApplicationMetricsMiddleware:
    """Publish aggregate HTTP counters without recording routes or payloads."""

    def __init__(self, app: Any, snapshot_path: str | None = None) -> None:
        self.app = app
        self.path = Path(
            snapshot_path
            or os.getenv("MINI_DROP_APPLICATION_METRICS_PATH")
            or "/tmp/mini-drop-app-metrics.json"
        )
        self.lock = threading.Lock()
        self.requests = 0
        self.failures = 0
        self.duration_ms = 0.0
        self.inflight = 0
        self.recent_durations: deque[float] = deque(maxlen=256)
        self._publish()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500
        with self.lock:
            self.inflight += 1
            self._publish_locked()

        async def observe_send(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, receive, observe_send)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            with self.lock:
                self.requests += 1
                self.failures += int(status_code >= 500)
                self.duration_ms += elapsed_ms
                self.inflight = max(0, self.inflight - 1)
                self.recent_durations.append(elapsed_ms)
                self._publish_locked()

    def _publish(self) -> None:
        with self.lock:
            self._publish_locked()

    def _publish_locked(self) -> None:
        recent = sorted(self.recent_durations)
        recent_average = sum(recent) / len(recent) if recent else 0.0
        p95_index = max(0, math.ceil(len(recent) * 0.95) - 1)
        snapshot = {
            "schema_version": "mini-drop.application-metrics.v1",
            "runtime": "python-asgi",
            "pid": os.getpid(),
            "http_requests": self.requests,
            "http_failures": self.failures,
            "http_duration_ms": round(self.duration_ms, 3),
            "http_inflight_requests": self.inflight,
            "http_recent_average_latency_ms": round(recent_average, 3),
            "http_recent_p95_latency_ms": round(recent[p95_index], 3) if recent else 0.0,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, self.path)
