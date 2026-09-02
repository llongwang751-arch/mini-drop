"""独立的 AI 诊断推进 Worker。

诊断会话、假设、证据和预算均持久化在数据库中。把推进循环从 HTTP 进程移出后，
Web API 的滚动重启不会中断多轮诊断，多个 API 副本也不会重复承担后台调度。
"""

from __future__ import annotations

import argparse
import os
import socket
import time
from collections.abc import Callable

from sqlalchemy import text

from server.app.database import init_db, new_session
from server.app.diagnostic_ai_rpc import start_diagnostic_ai_server
from server.app.drop_insight.service import advance_diagnosis
from server.app.logging_utils import log_event
from server.app.models import DropInsightSessionModel, DropInsightToolCallModel
from server.app.process_attestation import ProcessIdentityBinding


def _has_process_binding_authority(target: object) -> bool:
    if not isinstance(target, dict):
        return False
    try:
        ProcessIdentityBinding.from_mapping(target.get("process_binding"))
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _advance_active_drop_insight() -> int:
    """推进已经下发真实采集任务、但尚未归档结果的 V2 诊断。

    Drop Insight V2 与旧 DiagnosisOrchestrator 使用不同的持久化模型。此前只有
    浏览器轮询会调用 ``advance_diagnosis``，页面关闭、SSE 断开或浏览器节流后，
    已经 DONE 的采集任务会永久停留在 TASK_CREATED。这里由独立 Worker 接管，
    使诊断推进不再依赖某个浏览器标签页保持在线。
    """

    with new_session() as session:
        diagnosis_ids = []
        pending_ids = [
            row[0]
            for row in (
                session.query(DropInsightToolCallModel.diagnosis_id)
                .filter(
                    DropInsightToolCallModel.task_id.is_not(None),
                    DropInsightToolCallModel.status.in_(
                        ("TASK_CREATED", "RUNNING")
                    ),
                )
                .distinct()
                .all()
            )
        ]
        for diagnosis_id in pending_ids:
            diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
            target = diagnosis.target_json if diagnosis is not None else None
            # Pre-attestation legacy sessions cannot be safely promoted into
            # evidence. Keep their rows for audit, but do not busy-loop them.
            if _has_process_binding_authority(target):
                diagnosis_ids.append(diagnosis_id)

    advanced = 0
    for diagnosis_id in diagnosis_ids:
        try:
            result = advance_diagnosis(diagnosis_id)
            if result and result.get("actions"):
                advanced += 1
        except Exception as exc:
            # One stale or malformed diagnosis must not prevent other sessions
            # from importing terminal tasks in the same polling round.
            log_event(
                "error",
                "diagnosis_worker_session_failed",
                diagnosis_id=diagnosis_id,
                error=type(exc).__name__,
                message=str(exc),
            )
    return advanced


class DiagnosisWorker:
    def __init__(
        self,
        drop_insight_advancer: Callable[[], int] | None = None,
    ) -> None:
        self.drop_insight_advancer = (
            drop_insight_advancer
            if drop_insight_advancer is not None
            else _advance_active_drop_insight
        )

    def process_once(self) -> int:
        """推进所有可运行诊断。

        当前编排器自行遍历活跃会话且不返回计数；Worker 只需要保证每轮调用一次。
        """
        drop_insight_advanced = (
            self.drop_insight_advancer()
            if self.drop_insight_advancer is not None
            else 0
        )
        return drop_insight_advanced


def _healthcheck() -> int:
    try:
        with new_session() as session:
            session.execute(text("SELECT 1"))
        return 0
    except Exception:
        return 1


def _rpc_healthcheck(port: int) -> int:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return 0
    except OSError:
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini-Drop AI Diagnosis Worker")
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    init_db()
    rpc_port = int(os.getenv("MINI_DROP_DIAGNOSTIC_AI_GRPC_PORT", "50061"))
    if args.healthcheck:
        raise SystemExit(_healthcheck() or _rpc_healthcheck(rpc_port))

    rpc_server = start_diagnostic_ai_server(rpc_port)
    worker = DiagnosisWorker()
    poll_sec = max(0.2, float(os.getenv("MINI_DROP_DIAGNOSIS_POLL_SEC", "2")))
    log_event("info", "diagnostic_ai_rpc_started", port=rpc_port)
    try:
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
    finally:
        rpc_server.stop(grace=5).wait(timeout=10)


if __name__ == "__main__":
    main()
