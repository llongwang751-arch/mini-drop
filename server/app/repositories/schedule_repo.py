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



class ScheduleMixin:
    def create_schedule(
        self,
        *,
        name: str,
        cron_expression: str,
        timezone: str,
        task_template: dict,
        enabled: bool = True,
        created_by: str | None = None,
    ) -> ScheduleModel:
        ts = now_utc()
        next_run = next_schedule_fire(cron_expression, timezone, ts)
        with self._write_session() as session:
            model = ScheduleModel(
                id=f"schedule_{uuid4().hex}",
                name=name,
                cron_expression=cron_expression,
                timezone=timezone,
                task_template_json=task_template or {},
                enabled=enabled,
                next_run_at=next_run,
                created_by=created_by,
                created_at=ts,
                updated_at=ts,
            )
            session.add(model)
            session.flush()
            return model

    def list_schedules(self) -> list[ScheduleModel]:
        with self._read_session() as session:
            return session.query(ScheduleModel).order_by(
                ScheduleModel.created_at.desc()
            ).all()

    def get_schedule(self, schedule_id: str) -> ScheduleModel | None:
        with self._read_session() as session:
            return session.get(ScheduleModel, schedule_id)

    def update_schedule(
        self,
        schedule_id: str,
        *,
        name: str | None = None,
        cron_expression: str | None = None,
        timezone: str | None = None,
        task_template: dict | None = None,
        enabled: bool | None = None,
    ) -> ScheduleModel | None:
        ts = now_utc()
        with self._write_session() as session:
            model = session.get(ScheduleModel, schedule_id)
            if model is None:
                return None
            if name is not None:
                model.name = name
            if cron_expression is not None:
                model.cron_expression = cron_expression
            if timezone is not None:
                model.timezone = timezone
            if task_template is not None:
                model.task_template_json = task_template
            if enabled is not None:
                model.enabled = enabled
            # Recompute the next fire whenever the schedule definition changes
            # or it is re-enabled.
            model.next_run_at = next_schedule_fire(
                model.cron_expression, model.timezone, ts
            )
            model.updated_at = ts
            session.flush()
            return model

    def delete_schedule(self, schedule_id: str) -> bool:
        with self._write_session() as session:
            model = session.get(ScheduleModel, schedule_id)
            if model is None:
                return False
            session.delete(model)
            return True

    def claim_due_schedules(
        self, *, limit: int = 10, now: datetime | None = None
    ) -> list[ScheduleModel]:
        now = now or now_utc()
        with self._write_session() as session:
            due = (
                session.query(ScheduleModel)
                .filter(
                    ScheduleModel.enabled.is_(True),
                    ScheduleModel.next_run_at <= now,
                )
                .order_by(ScheduleModel.next_run_at.asc())
                .limit(max(1, int(limit)))
                .with_for_update(skip_locked=True)
                .all()
            )
            return due

    def fire_schedule(
        self,
        schedule: ScheduleModel,
        *,
        scheduled_at: datetime,
        next_run_at: datetime,
        payload: CreateTaskRequest,
        actor: Actor = Actor.SCHEDULE,
    ) -> TaskModel:
        """Create the scheduled task, record the firing slot and advance the
        schedule in ONE transaction. The unique (schedule_id, scheduled_at)
        slot keeps concurrent workers from firing the same minute twice."""
        ts = now_utc()
        with self._write_session() as session:
            task = self.create_task_in_session(
                session, payload, reason=f"计划任务: {schedule.name}", actor=actor
            )
            session.add(
                ScheduleRecordModel(
                    id=f"schedrec_{uuid4().hex}",
                    schedule_id=schedule.id,
                    scheduled_at=scheduled_at,
                    task_id=task.id,
                    status="created",
                    created_at=ts,
                )
            )
            db_schedule = session.get(ScheduleModel, schedule.id)
            if db_schedule is not None:
                db_schedule.next_run_at = next_run_at
                db_schedule.updated_at = ts
            session.flush()
            return task

    def record_schedule_run(
        self,
        schedule_id: str,
        scheduled_at: datetime,
        *,
        task_id: str | None = None,
        status: str,
        error: str | None = None,
    ) -> None:
        ts = now_utc()
        with self._write_session() as session:
            session.add(
                ScheduleRecordModel(
                    id=f"schedrec_{uuid4().hex}",
                    schedule_id=schedule_id,
                    scheduled_at=scheduled_at,
                    task_id=task_id,
                    status=status,
                    error_message=(error or "")[:2000],
                    created_at=ts,
                )
            )
            session.flush()

    def advance_schedule_next_run(
        self, schedule_id: str, *, now: datetime | None = None
    ) -> ScheduleModel | None:
        """Advance a schedule to its next fire after the given moment."""
        moment = now or now_utc()
        with self._write_session() as session:
            model = session.get(ScheduleModel, schedule_id)
            if model is None:
                return None
            model.next_run_at = next_schedule_fire(
                model.cron_expression, model.timezone, moment
            )
            model.updated_at = now_utc()
            session.flush()
            return model

    def list_schedule_records(
        self, schedule_id: str, *, limit: int = 100
    ) -> list[ScheduleRecordModel]:
        with self._read_session() as session:
            return (
                session.query(ScheduleRecordModel)
                .filter(ScheduleRecordModel.schedule_id == schedule_id)
                .order_by(ScheduleRecordModel.scheduled_at.desc())
                .limit(max(1, int(limit)))
                .all()
            )
