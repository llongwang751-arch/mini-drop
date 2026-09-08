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
from server.app.drop_insight.service import expire_stale_autonomous_diagnoses
from server.app.drop_insight.service import resolve_diagnosis_scope_autonomously
from server.app.drop_insight.service import run_diagnosis_planner
from server.app.drop_insight.schemas import RunPlannerRequest
from server.app.drop_insight.builtin_skills import seed_repository_skills
from server.app.drop_insight.frozen_replay_showcase import advance_frozen_replay_showcases
from server.app.drop_insight.skill_experiments import evaluate_experiment
from server.app.logging_utils import log_event
from server.app.models import (
    DiagnosticExperimentAssignmentModel,
    DiagnosticExperimentMetricModel,
    DiagnosticExperimentModel,
    DiagnosticExperimentObservationModel,
    DropInsightSessionModel,
    DropInsightToolCallModel,
)
from server.app.process_attestation import ProcessIdentityBinding
from server.app.outbox_dispatcher import OutboxDispatcher


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
            if (
                diagnosis is not None
                and diagnosis.status not in {
                    "COMPLETED",
                    "INSUFFICIENT_EVIDENCE",
                    "FAILED",
                    "CANCELLED",
                }
                and _has_process_binding_authority(target)
            ):
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


def _expire_stale_drop_insight() -> int:
    """Enforce the diagnosis wall-clock budget before resuming persisted work."""

    return len(expire_stale_autonomous_diagnoses())


def _start_autonomous_drop_insight() -> int:
    """Start scoped autonomous sessions without waiting for a browser tab."""

    with new_session() as session:
        candidates = (
            session.query(DropInsightSessionModel)
            .filter(
                DropInsightSessionModel.mode == "AUTONOMOUS",
                DropInsightSessionModel.deleted_at.is_(None),
                DropInsightSessionModel.status.in_(("UNDERSTANDING", "PLANNING")),
            )
            .all()
        )
        diagnosis_ids = []
        for diagnosis in candidates:
            has_call = (
                session.query(DropInsightToolCallModel.id)
                .filter(DropInsightToolCallModel.diagnosis_id == diagnosis.id)
                .first()
                is not None
            )
            if not has_call and _has_process_binding_authority(diagnosis.target_json):
                diagnosis_ids.append(diagnosis.id)

    started = 0
    for diagnosis_id in diagnosis_ids:
        try:
            result = run_diagnosis_planner(
                diagnosis_id,
                RunPlannerRequest(),
                requested_by="system:autonomous-worker",
            )
            if result and result.get("tool_call"):
                started += 1
        except Exception as exc:
            log_event(
                "error",
                "autonomous_diagnosis_start_failed",
                diagnosis_id=diagnosis_id,
                error=type(exc).__name__,
                message=str(exc),
            )
    return started


def _resolve_autonomous_scopes() -> int:
    """Discover and bind targets without depending on an open browser tab."""

    with new_session() as session:
        diagnosis_ids = [
            row[0]
            for row in (
                session.query(DropInsightSessionModel.id)
                .filter(
                    DropInsightSessionModel.mode == "AUTONOMOUS",
                    DropInsightSessionModel.deleted_at.is_(None),
                    DropInsightSessionModel.status == "NEEDS_CLARIFICATION",
                )
                .all()
            )
        ]
    resolved = 0
    for diagnosis_id in diagnosis_ids:
        try:
            resolved += int(resolve_diagnosis_scope_autonomously(diagnosis_id))
        except Exception as exc:
            log_event(
                "error",
                "autonomous_scope_resolution_failed",
                diagnosis_id=diagnosis_id,
                error=type(exc).__name__,
                message=str(exc),
            )
    return resolved


def _evaluate_changed_skill_experiments() -> int:
    """Persist a metric snapshot after new labeled outcomes arrive.

    Evaluation can recommend a rollout but never approves or deploys it.  The
    approver-only HTTP route remains the sole transition to APPROVED.
    """

    with new_session() as session:
        experiments = (
            session.query(DiagnosticExperimentModel)
            .filter(
                DiagnosticExperimentModel.status.in_(
                    ("RUNNING", "ROLLOUT_RECOMMENDED", "APPROVED")
                )
            )
            .all()
        )
        due_ids = []
        for experiment in experiments:
            latest_observation = (
                session.query(DiagnosticExperimentObservationModel.updated_at)
                .join(
                    DiagnosticExperimentAssignmentModel,
                    DiagnosticExperimentObservationModel.assignment_id
                    == DiagnosticExperimentAssignmentModel.id,
                )
                .filter(
                    DiagnosticExperimentAssignmentModel.experiment_id
                    == experiment.id
                )
                .order_by(DiagnosticExperimentObservationModel.updated_at.desc())
                .first()
            )
            if latest_observation is None:
                continue
            latest_metric = (
                session.query(DiagnosticExperimentMetricModel.created_at)
                .filter(
                    DiagnosticExperimentMetricModel.experiment_id == experiment.id
                )
                .order_by(DiagnosticExperimentMetricModel.created_at.desc())
                .first()
            )
            if latest_metric is None or latest_observation[0] > latest_metric[0]:
                due_ids.append(experiment.id)

    evaluated = 0
    for experiment_id in due_ids:
        try:
            if evaluate_experiment(
                experiment_id, evaluated_by="system:experiment-monitor"
            ):
                evaluated += 1
        except Exception as exc:
            log_event(
                "error",
                "diagnostic_experiment_evaluation_failed",
                experiment_id=experiment_id,
                error=type(exc).__name__,
                message=str(exc),
            )
    return evaluated


class DiagnosisWorker:
    def __init__(
        self,
        drop_insight_advancer: Callable[[], int] | None = None,
        scope_resolver: Callable[[], int] | None = None,
        autonomous_starter: Callable[[], int] | None = None,
        outbox_dispatcher: Callable[[], int] | None = None,
        frozen_replay_advancer: Callable[[], int] | None = None,
        experiment_evaluator: Callable[[], int] | None = None,
        diagnosis_expirer: Callable[[], int] | None = None,
    ) -> None:
        self.drop_insight_advancer = (
            drop_insight_advancer
            if drop_insight_advancer is not None
            else _advance_active_drop_insight
        )
        self.diagnosis_expirer = (
            diagnosis_expirer
            if diagnosis_expirer is not None
            else _expire_stale_drop_insight
        )
        self.autonomous_starter = (
            autonomous_starter
            if autonomous_starter is not None
            else _start_autonomous_drop_insight
        )
        self.scope_resolver = (
            scope_resolver
            if scope_resolver is not None
            else _resolve_autonomous_scopes
        )
        self.outbox_dispatcher = (
            outbox_dispatcher
            if outbox_dispatcher is not None
            else OutboxDispatcher().process_once
        )
        self.frozen_replay_advancer = (
            frozen_replay_advancer
            if frozen_replay_advancer is not None
            else advance_frozen_replay_showcases
        )
        self.experiment_evaluator = (
            experiment_evaluator
            if experiment_evaluator is not None
            else _evaluate_changed_skill_experiments
        )
        self.experiment_evaluation_interval = max(
            30.0,
            float(os.getenv("MINI_DROP_EXPERIMENT_EVAL_INTERVAL_SEC", "300")),
        )
        self._last_experiment_evaluation = 0.0

    def process_once(self) -> int:
        """推进所有可运行诊断。

        当前编排器自行遍历活跃会话且不返回计数；Worker 只需要保证每轮调用一次。
        """
        diagnoses_expired = (
            self.diagnosis_expirer()
            if self.diagnosis_expirer is not None
            else 0
        )
        scopes_resolved = (
            self.scope_resolver()
            if self.scope_resolver is not None
            else 0
        )
        autonomous_started = (
            self.autonomous_starter()
            if self.autonomous_starter is not None
            else 0
        )
        drop_insight_advanced = (
            self.drop_insight_advancer()
            if self.drop_insight_advancer is not None
            else 0
        )
        frozen_replays_advanced = (
            self.frozen_replay_advancer()
            if self.frozen_replay_advancer is not None
            else 0
        )
        outbox_published = (
            self.outbox_dispatcher()
            if self.outbox_dispatcher is not None
            else 0
        )
        experiments_evaluated = 0
        now = time.monotonic()
        if now - self._last_experiment_evaluation >= self.experiment_evaluation_interval:
            self._last_experiment_evaluation = now
            experiments_evaluated = (
                self.experiment_evaluator()
                if self.experiment_evaluator is not None
                else 0
            )
        return (
            diagnoses_expired
            + scopes_resolved
            + autonomous_started
            + drop_insight_advanced
            + frozen_replays_advanced
            + outbox_published
            + experiments_evaluated
        )


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
    skill_seed = seed_repository_skills()
    rpc_port = int(os.getenv("MINI_DROP_DIAGNOSTIC_AI_GRPC_PORT", "50061"))
    if args.healthcheck:
        raise SystemExit(_healthcheck() or _rpc_healthcheck(rpc_port))

    rpc_server = start_diagnostic_ai_server(rpc_port)
    worker = DiagnosisWorker()
    poll_sec = max(0.2, float(os.getenv("MINI_DROP_DIAGNOSIS_POLL_SEC", "2")))
    log_event("info", "diagnostic_ai_rpc_started", port=rpc_port)
    log_event("info", "repository_skills_loaded", **skill_seed)
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
