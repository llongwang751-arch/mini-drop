"""独立的 AI 诊断推进 Worker。

诊断会话、假设、证据和预算均持久化在数据库中。把推进循环从 HTTP 进程移出后，
Web API 的滚动重启不会中断多轮诊断，多个 API 副本也不会重复承担后台调度。
"""

from __future__ import annotations

import argparse
import os
import time

from sqlalchemy import text

from server.app.database import init_db, new_session
from server.app.diagnosis import DiagnosisOrchestrator
from server.app.diagnosis.continuous_trigger import ContinuousDiagnosisTrigger
from server.app.logging_utils import log_event
from server.app.sql_repository import SqlRepository


class DiagnosisWorker:
    def __init__(self, orchestrator: DiagnosisOrchestrator) -> None:
        self.orchestrator = orchestrator
        # Lightweight process-isolation tests use a minimal orchestrator double
        # without a repository. The production orchestrator always owns `repo`.
        self.continuous_trigger = (
            ContinuousDiagnosisTrigger(orchestrator)
            if hasattr(orchestrator, "repo")
            else None
        )

    def process_once(self) -> int:
        """推进所有可运行诊断。

        当前编排器自行遍历活跃会话且不返回计数；Worker 只需要保证每轮调用一次。
        """
        promoted = 0
        if (
            self.continuous_trigger is not None
            and os.getenv("MINI_DROP_CONTINUOUS_AUTO_DIAGNOSIS", "1") == "1"
        ):
            promoted = self.continuous_trigger.scan_once()
        self.orchestrator.advance_active()
        return promoted


def _healthcheck() -> int:
    try:
        with new_session() as session:
            session.execute(text("SELECT 1"))
        return 0
    except Exception:
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini-Drop AI Diagnosis Worker")
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    init_db()
    if args.healthcheck:
        raise SystemExit(_healthcheck())

    worker = DiagnosisWorker(DiagnosisOrchestrator(SqlRepository()))
    poll_sec = max(0.2, float(os.getenv("MINI_DROP_DIAGNOSIS_POLL_SEC", "2")))
    while True:
        try:
            advanced = worker.process_once()
            if advanced:
                log_event("info", "diagnosis_worker_advanced", count=advanced)
        except Exception as exc:  # one malformed diagnosis must not terminate the worker
            log_event(
                "error",
                "diagnosis_worker_iteration_failed",
                error=type(exc).__name__,
                message=str(exc),
            )
        if args.once:
            return
        time.sleep(poll_sec)


if __name__ == "__main__":
    main()
