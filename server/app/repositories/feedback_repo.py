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



class FeedbackMixin:
    def get_feedback_priors(self) -> dict[str, FeedbackPrior]:
        with self._read_session() as session:
            priors: dict[str, FeedbackPrior] = {}
            for row in session.query(RCAFeedbackWeightModel).all():
                priors[row.candidate_id] = FeedbackPrior(
                    candidate_id=row.candidate_id,
                    positive_count=row.positive_count or 0,
                    negative_count=row.negative_count or 0,
                    weight_delta=(row.weight_delta or 0) / 1000,
                )
            return priors

    def record_rca_feedback(
        self, diagnosis_id: str, task_id: str, predicted_cause_id: str,
        feedback_label: str, corrected_cause_id: str | None = None,
        feedback_note: str | None = None,
    ) -> None:
        with self._write_session() as session:
            ts = now_utc()
            session.add(RCAFeedbackModel(
                diagnosis_id=diagnosis_id,
                task_id=task_id,
                predicted_cause_id=predicted_cause_id,
                feedback_label=feedback_label,
                corrected_cause_id=corrected_cause_id,
                feedback_note=feedback_note,
                created_at=ts,
            ))

            candidate_id = corrected_cause_id if feedback_label == "wrong" and corrected_cause_id else predicted_cause_id
            weight = session.get(RCAFeedbackWeightModel, candidate_id)
            if weight is None:
                weight = RCAFeedbackWeightModel(
                    candidate_id=candidate_id,
                    positive_count=0,
                    negative_count=0,
                    partial_count=0,
                    weight_delta=0,
                    updated_at=ts,
                )
                session.add(weight)

            if feedback_label == "correct":
                weight.positive_count += 1
            elif feedback_label == "partial":
                weight.partial_count += 1
            elif feedback_label == "wrong":
                weight.negative_count += 1

            weight.weight_delta = _feedback_delta(
                weight.positive_count, weight.partial_count, weight.negative_count,
            )
            weight.updated_at = ts


def _feedback_delta(positive: int, partial: int, negative: int) -> int:
    raw = positive * 60 + partial * 25 - negative * 80
    return max(-200, min(200, raw))
