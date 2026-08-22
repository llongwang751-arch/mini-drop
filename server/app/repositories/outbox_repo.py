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



class OutboxMixin:
    def claim_outbox_messages(
        self,
        worker_id: str,
        limit: int = 10,
        *,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> list[OutboxMessageModel]:
        """Claim pending/retryable messages (or expired leases) atomically."""
        now = now or now_utc()
        with self._write_session() as session:
            due = (
                session.query(OutboxMessageModel)
                .filter(
                    or_(
                        and_(
                            OutboxMessageModel.status.in_(["PENDING", "FAILED"]),
                            OutboxMessageModel.next_attempt_at <= now,
                        ),
                        and_(
                            OutboxMessageModel.status == "DISPATCHING",
                            OutboxMessageModel.worker_lease_expires_at < now,
                        ),
                    )
                )
                .order_by(OutboxMessageModel.created_at.asc())
                .limit(max(1, int(limit)))
                # FOR UPDATE SKIP LOCKED on PostgreSQL; a no-op on SQLite.
                .with_for_update(skip_locked=True)
                .all()
            )
            for message in due:
                message.status = "DISPATCHING"
                message.worker_lease_owner = worker_id
                message.worker_lease_expires_at = now + timedelta(
                    seconds=max(1, int(lease_seconds))
                )
                message.updated_at = now
            session.flush()
            return due

    @staticmethod
    def _owned_outbox_claim(
        message: OutboxMessageModel,
        worker_id: str,
        now: datetime,
    ) -> bool:
        lease_expires_at = message.worker_lease_expires_at
        comparison_now = now
        if lease_expires_at is not None and lease_expires_at.tzinfo is None:
            comparison_now = now.replace(tzinfo=None)
        return (
            message.status == "DISPATCHING"
            and message.worker_lease_owner == worker_id
            and lease_expires_at is not None
            and lease_expires_at >= comparison_now
        )

    def mark_outbox_published(
        self, message_id: str, worker_id: str, *, now: datetime | None = None
    ) -> None:
        now = now or now_utc()
        with self._write_session() as session:
            message = session.get(OutboxMessageModel, message_id)
            if message is None or message.status == "PUBLISHED":
                return
            if not self._owned_outbox_claim(message, worker_id, now):
                return
            message.status = "PUBLISHED"
            message.published_at = now
            message.updated_at = now
            message.worker_lease_owner = None
            message.worker_lease_expires_at = None

    def fail_outbox_message(
        self,
        message_id: str,
        worker_id: str,
        error: str,
        *,
        max_attempts: int = 5,
        now: datetime | None = None,
    ) -> str:
        """Record a failure only while the caller still owns a live lease."""
        now = now or now_utc()
        with self._write_session() as session:
            message = session.get(OutboxMessageModel, message_id)
            if message is None:
                return "UNKNOWN"
            if message.status == "PUBLISHED":
                return "PUBLISHED"
            if not self._owned_outbox_claim(message, worker_id, now):
                return message.status or "UNKNOWN"
            message.attempts = (message.attempts or 0) + 1
            message.last_error = (error or "")[:2000]
            message.updated_at = now
            message.worker_lease_owner = None
            message.worker_lease_expires_at = None
            if message.attempts >= max(1, int(max_attempts)):
                message.status = "DEAD_LETTER"
                return "DEAD_LETTER"
            message.status = "FAILED"
            message.next_attempt_at = now + timedelta(
                seconds=min(3600, (2 ** message.attempts) * 5)
            )
            return "FAILED"

    def list_outbox_messages(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[OutboxMessageModel]:
        with self._read_session() as session:
            query = session.query(OutboxMessageModel)
            if status:
                query = query.filter(OutboxMessageModel.status == status)
            return (
                query.order_by(OutboxMessageModel.created_at.desc())
                .limit(max(1, int(limit)))
                .all()
            )
