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



class DiagnosisMixin:
    def create_diagnosis_run(self, task_id: str, model_name: str) -> str:
        with self._write_session() as session:
            ts = now_utc()
            diagnosis_id = f"diag_{ts.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
            session.add(DiagnosisRunModel(
                id=diagnosis_id,
                task_id=task_id,
                status="RUNNING",
                model_name=model_name,
                created_at=ts,
            ))
            return diagnosis_id

    def finish_diagnosis_run(
        self, diagnosis_id: str, status: str, summary: str,
        validated: bool, retry_count: int,
    ) -> None:
        with self._write_session() as session:
            run = session.get(DiagnosisRunModel, diagnosis_id)
            if run is None:
                raise ValueError(f"诊断 {diagnosis_id} 不存在")
            run.status = status
            run.summary = summary
            run.validated = 1 if validated else 0
            run.retry_count = retry_count
            run.finished_at = now_utc()

    def add_diagnosis_tool_result(
        self, diagnosis_id: str, tool_name: str, status: str,
        evidence_ref: str, input_json: dict[str, Any],
        output_json: dict[str, Any], error_message: str | None = None,
    ) -> None:
        with self._write_session() as session:
            session.add(DiagnosisToolResultModel(
                diagnosis_id=diagnosis_id,
                tool_name=tool_name,
                status=status,
                evidence_ref=evidence_ref,
                input_json=_json_safe(input_json),
                output_json=_json_safe(output_json),
                error_message=error_message,
                created_at=now_utc(),
            ))

    def add_diagnosis_report(
        self, diagnosis_id: str, report_json: dict[str, Any],
        ranked_causes: list[dict[str, Any]], confidence: float,
        not_enough_evidence: bool,
    ) -> str:
        with self._write_session() as session:
            report_id = f"report_{uuid4().hex[:10]}"
            session.add(DiagnosisReportModel(
                id=report_id,
                diagnosis_id=diagnosis_id,
                report_json=_json_safe(report_json),
                ranked_causes_json=_json_safe(ranked_causes),
                confidence=int(max(0.0, min(confidence, 1.0)) * 1000),
                not_enough_evidence=1 if not_enough_evidence else 0,
                created_at=now_utc(),
            ))
            return report_id

    def add_repair_plan(
        self, diagnosis_id: str, plan_id: str, cause_id: str,
        risk_level: str, actions: list[dict[str, Any]],
        executed_actions: list[dict[str, Any]],
        requires_user_confirm: bool, status: str,
    ) -> None:
        with self._write_session() as session:
            session.add(RepairPlanModel(
                id=plan_id,
                diagnosis_id=diagnosis_id,
                cause_id=cause_id,
                risk_level=risk_level,
                actions_json=_json_safe(actions),
                executed_actions_json=_json_safe(executed_actions),
                requires_user_confirm=1 if requires_user_confirm else 0,
                status=status,
                created_at=now_utc(),
            ))

    def get_diagnosis(self, diagnosis_id: str) -> dict[str, Any] | None:
        with self._read_session() as session:
            run = session.get(DiagnosisRunModel, diagnosis_id)
            if run is None:
                return None
            report = (
                session.query(DiagnosisReportModel)
                .filter(DiagnosisReportModel.diagnosis_id == diagnosis_id)
                .order_by(DiagnosisReportModel.created_at.desc())
                .first()
            )
            plan = (
                session.query(RepairPlanModel)
                .filter(RepairPlanModel.diagnosis_id == diagnosis_id)
                .order_by(RepairPlanModel.created_at.desc())
                .first()
            )
            tools = (
                session.query(DiagnosisToolResultModel)
                .filter(DiagnosisToolResultModel.diagnosis_id == diagnosis_id)
                .order_by(DiagnosisToolResultModel.id.asc())
                .all()
            )
            return {
                "run": run.to_dict(),
                "report": report.to_dict() if report else None,
                "repair_plan": plan.to_dict() if plan else None,
                "tool_results": [tool.to_dict() for tool in tools],
            }

    def list_diagnoses_for_task(self, task_id: str) -> list[dict[str, Any]]:
        with self._read_session() as session:
            runs = (
                session.query(DiagnosisRunModel)
                .filter(DiagnosisRunModel.task_id == task_id)
                .order_by(DiagnosisRunModel.created_at.desc())
                .all()
            )
            return [run.to_dict() for run in runs]

    def list_diagnoses(self, limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        """Return legacy RCA runs with their evidence/report projection."""

        with self._read_session() as session:
            runs = (
                session.query(DiagnosisRunModel)
                .order_by(DiagnosisRunModel.created_at.desc())
                .offset(max(offset, 0))
                .limit(max(1, min(limit, 1000)))
                .all()
            )
            items: list[dict[str, Any]] = []
            for run in runs:
                reports = (
                    session.query(DiagnosisReportModel)
                    .filter(DiagnosisReportModel.diagnosis_id == run.id)
                    .order_by(DiagnosisReportModel.created_at.asc())
                    .all()
                )
                tools = (
                    session.query(DiagnosisToolResultModel)
                    .filter(DiagnosisToolResultModel.diagnosis_id == run.id)
                    .order_by(DiagnosisToolResultModel.id.asc())
                    .all()
                )
                items.append({
                    "run": run.to_dict(),
                    "reports": [report.to_dict() for report in reports],
                    "tool_results": [tool.to_dict() for tool in tools],
                })
            return items


def _json_safe(value: Any):
    return json.loads(json.dumps(value, default=str))
