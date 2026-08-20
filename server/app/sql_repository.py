"""SQLAlchemy 持久化 Repository。

接口与 InMemoryRepository 保持一致，替换时 gRPC 服务和 HTTP handler
无需修改调用代码。通过 DATABASE_URL 切换 PostgreSQL / SQLite 后端。
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


# 领域 mixin 组合（按域拆分自本文件，方法签名不变）
from server.app.repositories.agent_repo import AgentMixin
from server.app.repositories.task_repo import TaskMixin
from server.app.repositories.artifact_repo import ArtifactMixin
from server.app.repositories.analysis_job_repo import AnalysisJobMixin
from server.app.repositories.outbox_repo import OutboxMixin
from server.app.repositories.schedule_repo import ScheduleMixin
from server.app.repositories.composite_repo import CompositeMixin
from server.app.repositories.diagnosis_repo import DiagnosisMixin
from server.app.repositories.feedback_repo import FeedbackMixin


class SqlRepository(AgentMixin, TaskMixin, ArtifactMixin, AnalysisJobMixin, OutboxMixin, ScheduleMixin, CompositeMixin, DiagnosisMixin, FeedbackMixin):
    """SQLAlchemy 持久化 Repository。

    基类保留共享状态与跨域内部方法；各领域方法来自 repositories/ 下的 mixin。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # Compatibility shim for older tests; dispatch now reads PENDING tasks from DB.
        self._task_queues: dict[str, deque[str]] = {}
        self.agent_metrics: dict[str, dict[str, Any]] = {}
        # TTL 缓存：key → (expires_at, value)
        self._cache: dict[str, tuple[float, Any]] = {}

    def _cached(self, key: str, ttl_sec: float, factory):
        """带 TTL 的简单缓存。

        如果 key 未过期则返回缓存值，否则调用 factory() 重新计算并缓存。
        """
        now = time.monotonic()
        if key in self._cache:
            expires_at, value = self._cache[key]
            if now < expires_at:
                return value
        value = factory()
        self._cache[key] = (now + ttl_sec, value)
        return value

    @contextmanager
    def _write_session(self):
        """写事务 context manager：加锁 → 建 session → 提交/回滚 → 关闭 → 清缓存。

        用于 register_agent / create_task / transition_task 等写操作。
        自动处理 lock → new_session → commit → close → cache_invalidation。
        """
        with self._lock:
            session = new_session()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
            # 写入后清除所有 TTL 缓存，确保下次读取拿到最新数据
            self._cache.clear()

    @contextmanager
    def _read_session(self):
        """只读 session context manager：建 session → 查询 → 关闭。

        用于 agents / tasks / events / artifacts 等只读查询。
        不加锁，不提交事务。
        """
        session = new_session()
        try:
            yield session
        finally:
            session.close()

    def as_dict(self, value: Any) -> dict[str, Any]:
        if isinstance(value, StatusEvent):
            data = asdict(value)
            data["from_status"] = value.from_status.value if value.from_status else None
            data["to_status"] = value.to_status.value
            data["actor"] = value.actor.value
            return data
        if isinstance(value, (
            AgentModel, TaskModel, TaskAttemptModel, StatusEventModel, AuditLogModel, ArtifactModel,
            AnalysisJobModel,
            DiagnosisRunModel, DiagnosisToolResultModel, DiagnosisReportModel,
            RepairPlanModel,
        )):
            return value.to_dict()
        return json.loads(json.dumps(value, default=str))

    def _write_audit(
        self, session: OrmSession, event_type: str, agent_id: str | None = None,
        task_id: str | None = None, message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        session.add(AuditLogModel(
            event_type=event_type,
            message=message,
            agent_id=agent_id,
            task_id=task_id,
            meta_json=metadata or {},
            created_at=now_utc(),
        ))

    @property
    def audit_logs(self) -> list[AuditLogModel]:
        return self._cached("audit_logs", 5.0, self._query_all_audit_logs)

    def _query_all_audit_logs(self) -> list[AuditLogModel]:
        s = new_session()
        try:
            return s.query(AuditLogModel).all()
        finally:
            s.close()

    def _write_event(
        self, session: OrmSession, task_id: str,
        from_status, to_status: TaskStatus,
        reason: str, actor: Actor,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        session.add(StatusEventModel(
            task_id=task_id,
            from_status=from_status.value if from_status else None,
            to_status=to_status.value,
            reason=reason,
            actor=actor.value,
            meta_json=metadata or {},
            created_at=now_utc(),
        ))

    def _transition_task_in_session(
        self, session: OrmSession, task_id: str,
        to_status: TaskStatus, reason: str, actor: Actor,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        task = session.get(TaskModel, task_id)
        # 事件：from 用旧 status value
        from_status = task.status
        session.add(StatusEventModel(
            task_id=task_id,
            from_status=from_status,
            to_status=to_status.value,
            reason=reason,
            actor=actor.value,
            meta_json=metadata or {},
            created_at=now_utc(),
        ))
        record_task_transition(from_status, to_status.value)
        task.status = to_status.value
        task.status_reason = reason
        self._update_execution_dimensions(task, to_status, actor)
        if to_status == TaskStatus.RUNNING:
            started_at = now_utc()
            if task.started_at is None:
                task.started_at = started_at
            attempt_no = (
                session.query(TaskAttemptModel)
                .filter(TaskAttemptModel.task_id == task_id)
                .count()
                + 1
            )
            session.add(TaskAttemptModel(
                id=f"attempt_{uuid4().hex}",
                task_id=task_id,
                attempt_no=attempt_no,
                agent_id=task.agent_id,
                status=TaskStatus.RUNNING.value,
                reason=reason,
                lease_expires_at=started_at + timedelta(seconds=task.duration_sec + 30),
                metadata_json=metadata or {},
                created_at=started_at,
                started_at=started_at,
            ))
        elif to_status in {
            TaskStatus.UPLOADING,
            TaskStatus.ANALYZING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            attempt = (
                session.query(TaskAttemptModel)
                .filter(TaskAttemptModel.task_id == task_id)
                .order_by(TaskAttemptModel.attempt_no.desc())
                .first()
            )
            if attempt is not None:
                # TaskAttempt describes collection execution only. Analyzer
                # retries and failures are tracked by AnalysisJobModel.
                if to_status == TaskStatus.ANALYZING:
                    attempt.status = CollectionStatus.SUCCEEDED.value
                elif to_status == TaskStatus.FAILED and actor == Actor.ANALYZER:
                    pass
                else:
                    attempt.status = to_status.value
                attempt.reason = reason
                attempt.metadata_json = {**(attempt.metadata_json or {}), **(metadata or {})}
                if to_status == TaskStatus.ANALYZING or (
                    to_status in (TaskStatus.FAILED, TaskStatus.CANCELLED)
                    and actor != Actor.ANALYZER
                ):
                    attempt.finished_at = now_utc()
        if to_status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
            task.finished_at = now_utc()

        # 发布 SSE 事件
        notify_task_changed(task_id, from_status, to_status.value, reason)

    @staticmethod
    def _update_execution_dimensions(
        task: TaskModel,
        to_status: TaskStatus,
        actor: Actor,
    ) -> None:
        """Maintain collection/analysis states without breaking the legacy status API."""

        if to_status == TaskStatus.PENDING:
            task.collection_status = CollectionStatus.QUEUED.value
            task.analysis_status = AnalysisStatus.NOT_STARTED.value
        elif to_status == TaskStatus.RUNNING:
            task.collection_status = CollectionStatus.COLLECTING.value
        elif to_status == TaskStatus.UPLOADING:
            task.collection_status = CollectionStatus.UPLOADING.value
        elif to_status == TaskStatus.ANALYZING:
            task.collection_status = CollectionStatus.SUCCEEDED.value
            task.analysis_status = AnalysisStatus.QUEUED.value
        elif to_status == TaskStatus.DONE:
            task.collection_status = CollectionStatus.SUCCEEDED.value
            task.analysis_status = AnalysisStatus.SUCCEEDED.value
        elif to_status == TaskStatus.FAILED:
            if actor == Actor.ANALYZER:
                task.collection_status = CollectionStatus.SUCCEEDED.value
                task.analysis_status = AnalysisStatus.FAILED.value
            else:
                task.collection_status = CollectionStatus.FAILED.value
                task.analysis_status = AnalysisStatus.SKIPPED.value
        elif to_status == TaskStatus.CANCELLED:
            if task.collection_status == CollectionStatus.SUCCEEDED.value:
                task.analysis_status = AnalysisStatus.CANCELLED.value
            else:
                task.collection_status = CollectionStatus.CANCELLED.value
                task.analysis_status = AnalysisStatus.SKIPPED.value

    def create_task_in_session(
        self,
        session,
        payload: CreateTaskRequest,
        *,
        reason: str = "AI tool call created task",
        actor: Actor = Actor.AI,
    ) -> TaskModel:
        """Create Task, initial event and audit inside the caller transaction.

        The caller owns commit/rollback.  This prevents an AI ToolCall from
        losing its Task link after the Task row has already committed.
        """
        ts = now_utc()
        task_id = f"task_{ts.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
        agent = session.get(AgentModel, payload.agent_id)
        if agent is None:
            raise ValueError(f"Agent {payload.agent_id} does not exist")
        task = TaskModel(
            id=task_id,
            name=payload.name,
            agent_id=payload.agent_id,
            target_pid=payload.target_pid,
            collector_type=payload.collector_type,
            sample_rate=payload.sample_rate,
            duration_sec=payload.duration_sec,
            status=TaskStatus.PENDING.value,
            status_reason=reason,
            collection_status=CollectionStatus.QUEUED.value,
            analysis_status=AnalysisStatus.NOT_STARTED.value,
            request_params=payload.model_dump(),
            diagnosis_step_id=(payload.options or {}).get("diagnosis_step_id"),
            created_at=ts,
        )
        session.add(task)
        session.flush()
        self._write_event(
            session,
            task_id,
            None,
            TaskStatus.PENDING,
            reason,
            actor,
            payload.model_dump(),
        )
        self._write_audit(
            session,
            "TASK_CREATED",
            task_id=task_id,
            message=f"Task {task_id} created by AI tool call",
            metadata=payload.model_dump(),
        )
        record_task_transition("NONE", TaskStatus.PENDING.value)
        # §9.6: every task-creation path publishes task.created through the
        # transactional outbox, not just the Go/Web entrypoint.
        self.enqueue_outbox(
            "task", task_id, "task.created", payload.model_dump(),
            session=session,
        )
        return task

    def enqueue_outbox(
        self,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        session: OrmSession | None = None,
    ) -> OutboxMessageModel:
        """Persist an outbox message.

        Pass an open ``session`` to enqueue in the SAME transaction as the
        domain write; otherwise a fresh write session is used.
        """
        ts = now_utc()
        message = OutboxMessageModel(
            id=f"outbox_{uuid4().hex}",
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload_json=payload or {},
            status="PENDING",
            attempts=0,
            next_attempt_at=ts,
            created_at=ts,
            updated_at=ts,
        )
        if session is not None:
            session.add(message)
            session.flush()
            return message
        with self._write_session() as write:
            write.add(message)
            write.flush()
            return message
