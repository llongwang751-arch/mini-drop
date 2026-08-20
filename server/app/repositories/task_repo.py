"""SqlRepository 领域 mixin —— 按领域拆分自 server/app/sql_repository.py。

拆分为 mixin 后，``class SqlRepository(...)`` 在 sql_repository.py 组合这些 mixin。
方法签名、属性名与返回类型与原实现完全一致，调用方零改动。
"""
from __future__ import annotations

import json
import threading
import time

from server.app.event_bus import notify_task_changed, notify_agent_status
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy import and_, func, or_, text
from sqlalchemy.orm import Session as OrmSession

from server.app.cron import next_schedule_fire
from server.app.database import new_session
from server.app.artifact_integrity import prepare_artifact
from server.app.models import (
    AgentMetricSnapshotModel,
    AgentModel,
    AnalysisJobModel,
    ArtifactModel,
    AuditLogModel,
    DiagnosisReportModel,
    DiagnosisRunModel,
    DiagnosisToolResultModel,
    CompositeTaskItemModel,
    CompositeTaskModel,
    FixVerificationModel,
    OutboxMessageModel,
    RCAFeedbackModel,
    RCAFeedbackWeightModel,
    RepairPlanModel,
    ScheduleModel,
    ScheduleRecordModel,
    StatusEventModel,
    TaskAttemptModel,
    TaskModel,
)
from server.app.prometheus_metrics import (
    observe_analysis_job_duration,
    record_analysis_job,
    record_composite_created,
    record_composite_status,
    record_task_transition,
)
from server.app.rca.models import FeedbackPrior
from server.app.schemas import CreateTaskRequest
from server.app.state_machine import (
    AnalysisStatus,
    Actor,
    CollectionStatus,
    StatusEvent,
    TaskStatus,
    build_status_event,
    now_utc,
)

def _same_task_request(a: dict, b: dict) -> bool:
    """Canonical JSON equality (key order independent)."""
    if a is None or b is None:
        return a == b
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(
        b, sort_keys=True, default=str
    )



class TaskMixin:
    def delete_task(self, task_id: str) -> bool:
        """Archive a terminal task while retaining audit and AI evidence."""
        with self._write_session() as session:
            task = session.get(TaskModel, task_id)
            if task is None or task.deleted_at is not None:
                return False
            if task.status in {
                TaskStatus.PENDING.value, TaskStatus.RUNNING.value,
                TaskStatus.UPLOADING.value, TaskStatus.ANALYZING.value,
            }:
                raise ValueError(f"\u4efb\u52a1\u72b6\u6001\u4e3a {task.status}\uff0c\u8bf7\u5148\u53d6\u6d88\u6216\u7b49\u5f85\u4efb\u52a1\u7ed3\u675f")
            task.deleted_at = now_utc()
            task.deleted_by = "web"
            task.delete_reason = "\u7528\u6237\u5728\u63a7\u5236\u53f0\u5f52\u6863\u4efb\u52a1"
            self._write_audit(
                session,
                event_type="TASK_ARCHIVED",
                task_id=task_id,
                message=f"\u4efb\u52a1 {task.name or task_id} \u5df2\u5f52\u6863\uff0c\u8bc1\u636e\u4ecd\u4fdd\u7559",
                metadata={"deletion_mode": "soft", "evidence_retained": True},
            )
            self._cache.pop("tasks", None)
            self._cache.pop("events", None)
            return True

    def create_task(
        self,
        payload: CreateTaskRequest,
        *,
        idempotency_key: str | None = None,
        creator_id: str | None = None,
    ) -> TaskModel:
        """Create a task, honoring (creator_id, idempotency_key) replay (guide §6.12)."""
        if idempotency_key:
            existing = self._find_idempotent_task(creator_id, idempotency_key, payload)
            if existing is not None:
                return existing
        with self._write_session() as session:
            ts = now_utc()
            hex_suffix = uuid4().hex[:6]
            task_id = f"task_{ts.strftime('%Y%m%d_%H%M%S')}_{hex_suffix}"
            agent = session.get(AgentModel, payload.agent_id)
            if agent is None:
                raise ValueError(f"Agent {payload.agent_id} 不存在")

            task = TaskModel(
                id=task_id,
                name=payload.name,
                agent_id=payload.agent_id,
                target_pid=payload.target_pid,
                collector_type=payload.collector_type,
                sample_rate=payload.sample_rate,
                duration_sec=payload.duration_sec,
                status=TaskStatus.PENDING.value,
                status_reason="Web 请求创建任务",
                collection_status=CollectionStatus.QUEUED.value,
                analysis_status=AnalysisStatus.NOT_STARTED.value,
                request_params=payload.model_dump(),
                diagnosis_step_id=(payload.options or {}).get("diagnosis_step_id"),
                idempotency_key=idempotency_key,
                creator_id=creator_id,
                created_at=ts,
            )
            session.add(task)
            session.flush()

            # 状态事件
            self._write_event(session, task_id, None, TaskStatus.PENDING,
                              "Web 请求创建任务", Actor.WEB, payload.model_dump())
            record_task_transition("NONE", TaskStatus.PENDING.value)

            # 审计日志
            self._write_audit(session, "TASK_CREATED", task_id=task_id,
                              message=f"任务 {task_id} 已创建",
                              metadata=payload.model_dump())

            # 通用 Transactional Outbox：与 Task + Event + Audit 同一事务提交，
            # Dispatcher 领取后幂等发布（指南 §9.6）。
            self.enqueue_outbox(
                "task", task_id, "task.created", payload.model_dump(),
                session=session,
            )

            return task

    def _find_idempotent_task(
        self,
        creator_id: str | None,
        idempotency_key: str,
        payload: CreateTaskRequest,
    ) -> TaskModel | None:
        """Replay guard: same key + same params returns the existing task; a
        key reused with different params is rejected (mirrors the Go entry)."""
        if not creator_id:
            return None
        with self._read_session() as session:
            existing = (
                session.query(TaskModel)
                .filter(
                    TaskModel.creator_id == creator_id,
                    TaskModel.idempotency_key == idempotency_key,
                )
                .first()
            )
            if existing is None:
                return None
            if not _same_task_request(existing.request_params or {}, payload.model_dump()):
                raise ValueError(
                    f"Idempotency-Key 已用于不同参数的请求: {idempotency_key}"
                )
            return existing

    def get_task_by_diagnosis_step_id(self, step_id: str) -> TaskModel | None:
        session = new_session()
        try:
            return session.query(TaskModel).filter(
                TaskModel.diagnosis_step_id == step_id,
                TaskModel.deleted_at.is_(None),
            ).first()
        finally:
            session.close()

    def get_task(self, task_id: str) -> TaskModel | None:
        session = new_session()
        try:
            return session.query(TaskModel).filter(
                TaskModel.id == task_id,
                TaskModel.deleted_at.is_(None),
            ).first()
        finally:
            session.close()

    def get_task_attempts(self, task_id: str) -> list[TaskAttemptModel]:
        session = new_session()
        try:
            return (
                session.query(TaskAttemptModel)
                .filter(TaskAttemptModel.task_id == task_id)
                .order_by(TaskAttemptModel.attempt_no.asc())
                .all()
            )
        finally:
            session.close()

    def transition_task(
        self, task_id: str, to_status: TaskStatus,
        reason: str, actor: Actor,
        metadata: dict[str, Any] | None = None,
    ) -> TaskModel:
        with self._write_session() as session:
            task = session.get(TaskModel, task_id)
            if task is None:
                raise ValueError(f"任务 {task_id} 不存在")

            _ = build_status_event(
                task_id, TaskStatus(task.status), to_status,
                reason, actor, metadata or {},
            )

            self._transition_task_in_session(
                session, task_id, to_status, reason, actor, metadata,
            )
            task.status = to_status.value
            return task

    def cancel_task(
        self,
        task_id: str,
        reason: str,
        actor: Actor = Actor.WEB,
    ) -> TaskModel:
        """Cancel an active task in the same transaction as its audit record."""
        with self._write_session() as session:
            task = session.get(TaskModel, task_id)
            if task is None:
                raise ValueError(f"任务 {task_id} 不存在")
            current = TaskStatus(task.status)
            _ = build_status_event(
                task_id,
                current,
                TaskStatus.CANCELLED,
                reason,
                actor,
                {"previous_status": current.value},
            )
            self._transition_task_in_session(
                session,
                task_id,
                TaskStatus.CANCELLED,
                reason,
                actor,
                {"previous_status": current.value},
            )
            self._write_audit(
                session,
                "TASK_CANCELLED",
                task_id=task_id,
                message=f"任务 {task_id} 已取消",
                metadata={"reason": reason, "actor": actor.value},
            )
            task.status = TaskStatus.CANCELLED.value
            return task

    @property
    def tasks(self) -> dict[str, TaskModel]:
        return self._cached("tasks", 2.0, self._query_all_tasks)

    def _query_all_tasks(self) -> dict[str, TaskModel]:
        s = new_session()
        try:
            return {
                t.id: t for t in s.query(TaskModel)
                .filter(TaskModel.deleted_at.is_(None)).all()
            }
        finally:
            s.close()

    @property
    def events(self) -> list[StatusEvent]:
        """返回所有状态事件，兼容原有 list[StatusEvent] 接口。"""
        return self._cached("events", 2.0, self._query_all_events)

    def _query_all_events(self) -> list[StatusEvent]:
        s = new_session()
        try:
            models = s.query(StatusEventModel).all()
            result: list[StatusEvent] = []
            for m in models:
                result.append(StatusEvent(
                    task_id=m.task_id if m.task_id else "",
                    from_status=TaskStatus(m.from_status) if m.from_status else None,
                    to_status=TaskStatus(m.to_status),
                    reason=m.reason if m.reason else "",
                    actor=Actor(m.actor) if m.actor else Actor.SERVER,
                    metadata=m.meta_json if isinstance(m.meta_json, dict) else {},
                    created_at=m.created_at if m.created_at else now_utc(),
                ))
            return result
        finally:
            s.close()
