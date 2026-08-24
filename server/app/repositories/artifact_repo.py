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
    AnalysisJobInputArtifactModel,
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



class ArtifactMixin:
    def _add_artifacts_in_session(
        self,
        session: OrmSession,
        task_id: str,
        task_attempt_id: str,
        artifacts: list[dict[str, Any]],
        *,
        analysis_job_id: str | None = None,
    ) -> list[int]:
        attempt = (
            session.query(TaskAttemptModel)
            .filter(
                TaskAttemptModel.task_id == task_id,
                TaskAttemptModel.id == task_attempt_id,
            )
            .one_or_none()
        )
        if attempt is None:
            raise ValueError("TaskAttempt identity does not match task")
        ts = now_utc()
        models: list[ArtifactModel] = []
        for value in artifacts:
            artifact = prepare_artifact(task_id, value)
            model = ArtifactModel(
                task_id=task_id,
                task_attempt_id=task_attempt_id,
                analysis_job_id=analysis_job_id,
                artifact_type=artifact.get("artifact_type", "raw"),
                bucket=artifact.get("bucket", "mini-drop"),
                object_key=artifact.get("object_key", ""),
                filename=artifact.get("filename"),
                local_path=artifact.get("local_path"),
                content_type=artifact.get("content_type", "application/octet-stream"),
                size_bytes=artifact.get("size_bytes", 0),
                sha256=artifact.get("sha256"),
                manifest_json=artifact.get("manifest", {}),
                integrity_status=artifact.get("integrity_status", "LEGACY_UNVERIFIED"),
                integrity_reason=artifact.get("integrity_reason", ""),
                meta_json=artifact.get("metadata", {}),
                created_at=ts,
            )
            session.add(model)
            models.append(model)
        session.flush()
        return [int(model.id) for model in models]

    def add_attempt_artifacts(
        self,
        task_id: str,
        task_attempt_id: str,
        artifacts: list[dict[str, Any]],
    ) -> list[int]:
        with self._write_session() as session:
            return self._add_artifacts_in_session(
                session, task_id, task_attempt_id, artifacts
            )

    def add_artifacts(self, task_id: str, artifacts: list[dict[str, Any]]) -> list[int]:
        with self._write_session() as session:
            ts = now_utc()
            models: list[ArtifactModel] = []
            for art in artifacts:
                art = prepare_artifact(task_id, art)
                model = ArtifactModel(
                    task_id=task_id,
                    artifact_type=art.get("artifact_type", "raw"),
                    bucket=art.get("bucket", "mini-drop"),
                    object_key=art.get("object_key", ""),
                    filename=art.get("filename"),
                    local_path=art.get("local_path"),
                    content_type=art.get("content_type", "application/octet-stream"),
                    size_bytes=art.get("size_bytes", 0),
                    sha256=art.get("sha256"),
                    manifest_json=art.get("manifest", {}),
                    integrity_status=art.get("integrity_status", "LEGACY_UNVERIFIED"),
                    integrity_reason=art.get("integrity_reason", ""),
                    meta_json=art.get("metadata", {}),
                    created_at=ts,
                )
                session.add(model)
                models.append(model)
            session.flush()
            return [int(model.id) for model in models]

    def mark_artifact_integrity(
        self, artifact_id: int, status: str, reason: str,
    ) -> None:
        with self._write_session() as session:
            model = session.get(ArtifactModel, artifact_id)
            if model is None:
                raise ValueError(f"Artifact {artifact_id} 不存在")
            model.integrity_status = status[:32]
            model.integrity_reason = reason[:1000]

    def get_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        with self._read_session() as session:
            return [
                model.to_dict()
                for model in (
                    session.query(ArtifactModel)
                    .filter(ArtifactModel.task_id == task_id)
                    .order_by(ArtifactModel.id.asc())
                    .all()
                )
            ]

    def get_analysis_job_input_artifacts(
        self, analysis_job_id: str
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            job = session.get(AnalysisJobModel, analysis_job_id)
            if job is None or not job.task_attempt_id:
                return []
            return [
                artifact.to_dict()
                for artifact in (
                    session.query(ArtifactModel)
                    .join(
                        AnalysisJobInputArtifactModel,
                        AnalysisJobInputArtifactModel.artifact_id == ArtifactModel.id,
                    )
                    .filter(
                        AnalysisJobInputArtifactModel.analysis_job_id == analysis_job_id,
                        AnalysisJobInputArtifactModel.task_id == job.task_id,
                        AnalysisJobInputArtifactModel.task_attempt_id == job.task_attempt_id,
                        ArtifactModel.task_id == job.task_id,
                        ArtifactModel.task_attempt_id == job.task_attempt_id,
                    )
                    .order_by(AnalysisJobInputArtifactModel.id.asc())
                    .all()
                )
            ]

    @property
    def artifacts(self) -> dict[str, list[dict[str, Any]]]:
        return self._cached("artifacts", 2.0, self._query_all_artifacts)

    def _query_all_artifacts(self) -> dict[str, list[dict[str, Any]]]:
        s = new_session()
        try:
            result: dict[str, list[dict[str, Any]]] = {}
            for art in s.query(ArtifactModel).all():
                tid = art.task_id if art.task_id else ""
                result.setdefault(tid, []).append(art.to_dict())
            return result
        finally:
            s.close()
