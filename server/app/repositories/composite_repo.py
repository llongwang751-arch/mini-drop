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



class CompositeMixin:
    def create_composite_task(
        self,
        *,
        name: str,
        strategy: str,
        children: list[dict],
        required_success_count: int | None = None,
        created_by: str | None = None,
        actor: Actor = Actor.WEB,
    ) -> CompositeTaskModel:
        """Create the composite and its child tasks in one transaction."""
        ts = now_utc()
        with self._write_session() as session:
            composite = CompositeTaskModel(
                id=f"composite_{uuid4().hex}",
                name=name,
                strategy=strategy,
                required_success_count=required_success_count,
                status="PENDING",
                created_by=created_by,
                created_at=ts,
                updated_at=ts,
            )
            session.add(composite)
            session.flush()
            for index, child in enumerate(children):
                payload = CreateTaskRequest(**child["task_template"])
                task = self.create_task_in_session(
                    session,
                    payload,
                    reason=f"复合任务 {name} 子任务",
                    actor=actor,
                )
                session.add(
                    CompositeTaskItemModel(
                        id=f"citem_{uuid4().hex}",
                        composite_id=composite.id,
                        task_id=task.id,
                        role=child.get("role", "required"),
                        sort_order=index,
                        status="running",
                        created_at=ts,
                    )
                )
            session.flush()
            record_composite_created(strategy)
            return composite

    def list_composite_tasks(self) -> list[CompositeTaskModel]:
        with self._read_session() as session:
            return session.query(CompositeTaskModel).order_by(
                CompositeTaskModel.created_at.desc()
            ).all()

    def get_composite_task(
        self, composite_id: str
    ) -> CompositeTaskModel | None:
        with self._read_session() as session:
            return session.get(CompositeTaskModel, composite_id)

    def create_fix_verification(
        self,
        *,
        diagnosis_id: str,
        fix_summary: str | None,
        before_task_id: str,
        after_task_id: str,
        outcome: str,
        before_hotspot: dict | None,
        after_hotspot: dict | None,
        comparison: dict | None,
        created_by: str | None = None,
    ) -> FixVerificationModel:
        ts = now_utc()
        with self._write_session() as session:
            model = FixVerificationModel(
                id=f"fix_{uuid4().hex}",
                diagnosis_id=diagnosis_id,
                fix_summary=fix_summary,
                before_task_id=before_task_id,
                after_task_id=after_task_id,
                outcome=outcome,
                before_hotspot_json=before_hotspot,
                after_hotspot_json=after_hotspot,
                comparison_json=comparison,
                created_by=created_by,
                created_at=ts,
            )
            session.add(model)
            session.flush()
            return model

    def list_fix_verifications(
        self, diagnosis_id: str, *, limit: int = 50
    ) -> list[FixVerificationModel]:
        with self._read_session() as session:
            return (
                session.query(FixVerificationModel)
                .filter(FixVerificationModel.diagnosis_id == diagnosis_id)
                .order_by(FixVerificationModel.created_at.desc())
                .limit(max(1, int(limit)))
                .all()
            )

    def list_composite_items(
        self, composite_id: str
    ) -> list[CompositeTaskItemModel]:
        with self._read_session() as session:
            return (
                session.query(CompositeTaskItemModel)
                .filter(CompositeTaskItemModel.composite_id == composite_id)
                .order_by(CompositeTaskItemModel.sort_order.asc())
                .all()
            )

    def aggregate_composite(self, composite_id: str) -> str | None:
        """Refresh child outcomes from the task table and reduce by strategy."""
        from server.app.composite_service import aggregate_status, child_outcome

        ts = now_utc()
        with self._write_session() as session:
            composite = session.get(CompositeTaskModel, composite_id)
            if composite is None:
                return None
            items = (
                session.query(CompositeTaskItemModel)
                .filter(CompositeTaskItemModel.composite_id == composite_id)
                .all()
            )
            outcomes = []
            for item in items:
                outcome = "running"
                if item.task_id:
                    task = session.get(TaskModel, item.task_id)
                    outcome = child_outcome(task.status if task is not None else None)
                item.status = outcome
                outcomes.append({"status": outcome, "role": item.role})
            status = aggregate_status(
                composite.strategy, outcomes, composite.required_success_count
            )
            composite.status = status
            composite.updated_at = ts
            session.flush()
            record_composite_status(status)
            return status

    def cancel_composite_task(
        self, composite_id: str, *, reason: str = "复合任务取消"
    ) -> CompositeTaskModel | None:
        """Cancel the composite and all its non-terminal child tasks."""
        from server.app.composite_service import TERMINAL_COMPOSITE_STATUSES

        with self._write_session() as session:
            composite = session.get(CompositeTaskModel, composite_id)
            if composite is None:
                return None
            if composite.status in TERMINAL_COMPOSITE_STATUSES:
                return composite
            items = (
                session.query(CompositeTaskItemModel)
                .filter(CompositeTaskItemModel.composite_id == composite_id)
                .all()
            )
            for item in items:
                if not item.task_id:
                    continue
                task = session.get(TaskModel, item.task_id)
                if task is not None and task.status not in {
                    "DONE", "FAILED", "CANCELLED",
                }:
                    self._transition_task_in_session(
                        session,
                        task.id,
                        TaskStatus.CANCELLED,
                        reason,
                        Actor.WEB,
                        {"composite_id": composite_id},
                    )
                    item.status = "cancelled"
            composite.status = "CANCELLED"
            composite.updated_at = now_utc()
            session.flush()
            return composite
