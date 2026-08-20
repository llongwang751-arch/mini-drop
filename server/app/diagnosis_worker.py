"""独立的 AI 诊断推进 Worker。

诊断会话、假设、证据和预算均持久化在数据库中。把推进循环从 HTTP 进程移出后，
Web API 的滚动重启不会中断多轮诊断，多个 API 副本也不会重复承担后台调度。
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Callable

from sqlalchemy import text

from server.app.database import init_db, new_session
from server.app.diagnosis import DiagnosisOrchestrator
from server.app.diagnosis.continuous_trigger import ContinuousDiagnosisTrigger
from server.app.drop_insight.service import advance_diagnosis
from server.app.logging_utils import log_event
from server.app.models import DropInsightToolCallModel
from server.app.sql_repository import SqlRepository


def _advance_active_drop_insight() -> int:
    """推进已经下发真实采集任务、但尚未归档结果的 V2 诊断。

    Drop Insight V2 与旧 DiagnosisOrchestrator 使用不同的持久化模型。此前只有
    浏览器轮询会调用 ``advance_diagnosis``，页面关闭、SSE 断开或浏览器节流后，
    已经 DONE 的采集任务会永久停留在 TASK_CREATED。这里由独立 Worker 接管，
    使诊断推进不再依赖某个浏览器标签页保持在线。
    """

    with new_session() as session:
        diagnosis_ids = [
            row[0]
            for row in (
                session.query(DropInsightToolCallModel.diagnosis_id)
                .filter(
                    DropInsightToolCallModel.task_id.is_not(None),
                    DropInsightToolCallModel.status.in_(("TASK_CREATED", "RUNNING")),
                )
                .distinct()
                .all()
            )
        ]

    advanced = 0
    for diagnosis_id in diagnosis_ids:
        result = advance_diagnosis(diagnosis_id)
        if result and result.get("actions"):
            advanced += 1
    return advanced


class DiagnosisWorker:
    def __init__(
        self,
        orchestrator: DiagnosisOrchestrator,
        drop_insight_advancer: Callable[[], int] | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        # Lightweight process-isolation tests use a minimal orchestrator double
        # without a repository. The production orchestrator always owns `repo`.
        self.continuous_trigger = (
            ContinuousDiagnosisTrigger(orchestrator)
            if hasattr(orchestrator, "repo")
            else None
        )
        self.drop_insight_advancer = (
            drop_insight_advancer
            if drop_insight_advancer is not None
            else (_advance_active_drop_insight if hasattr(orchestrator, "repo") else None)
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
        drop_insight_advanced = (
            self.drop_insight_advancer()
            if self.drop_insight_advancer is not None
            else 0
        )
        return promoted + drop_insight_advanced


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
