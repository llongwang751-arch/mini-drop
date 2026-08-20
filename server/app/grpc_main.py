"""独立的 Agent gRPC 控制面进程。

该进程只负责 Agent 注册、心跳、任务领取和结果上报，并在同一进程中维护
心跳超时判定与 Agent 指标快照。HTTP API 重启不会再中断 Agent 连接。
"""

from __future__ import annotations

import argparse
import os
import socket
import threading
import time

from server.app.database import init_db
from server.app.grpc_server import serve
from server.app.logging_utils import log_event
from server.app.sql_repository import SqlRepository


class ControlPlaneMaintenance:
    """与 gRPC Repository 共享内存指标缓存的后台维护循环。"""

    def __init__(self, repo: SqlRepository, *, timeout_sec: int = 30, interval_sec: float = 5) -> None:
        self.repo = repo
        self.timeout_sec = max(5, timeout_sec)
        self.interval_sec = max(0.2, interval_sec)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> dict[str, int]:
        offline = self.repo.mark_offline_agents(timeout_sec=self.timeout_sec)
        snapshots = self.repo.persist_agent_metric_snapshots()
        return {"offline_agents": len(offline), "metric_snapshots": snapshots}

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="control-plane-maintenance",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.interval_sec + 1))

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # maintenance failure must not kill gRPC
                log_event(
                    "error",
                    "control_plane_maintenance_failed",
                    error=type(exc).__name__,
                    message=str(exc),
                )
            self._stop.wait(self.interval_sec)


def _tcp_healthcheck(host: str, port: int, timeout: float = 2.0) -> int:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return 0
    except OSError:
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini-Drop Agent gRPC Control Plane")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()

    port = int(os.getenv("MINI_DROP_GRPC_PORT", "50051"))
    if args.healthcheck:
        raise SystemExit(_tcp_healthcheck("127.0.0.1", port))

    init_db()
    repo = SqlRepository()
    grpc_server = serve(repo, port=port)
    maintenance = ControlPlaneMaintenance(
        repo,
        timeout_sec=int(os.getenv("AGENT_OFFLINE_TIMEOUT_SEC", "30")),
        interval_sec=float(os.getenv("MINI_DROP_CONTROL_MAINTENANCE_SEC", "5")),
    )
    maintenance.start()
    log_event("info", "grpc_control_plane_started", port=port)
    try:
        grpc_server.wait_for_termination()
    except KeyboardInterrupt:
        pass
    finally:
        maintenance.stop()
        grpc_server.stop(grace=5).wait(timeout=10)


if __name__ == "__main__":
    main()
