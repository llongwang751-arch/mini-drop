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



class AnalysisJobMixin:
    def enqueue_analysis_job(
        self,
        task_id: str,
        *,
        analyzer_type: str,
        analyzer_version: str,
        input_checksum: str,
        input_artifact_ids: list[int] | None = None,
        max_retries: int = 3,
    ) -> AnalysisJobModel:
        """Create one idempotent analyzer execution for a collected artifact set."""

        key = f"{task_id}:{analyzer_type}:{analyzer_version}:{input_checksum}"
        with self._write_session() as session:
            existing = (
                session.query(AnalysisJobModel)
                .filter(AnalysisJobModel.idempotency_key == key)
                .first()
            )
            if existing is not None:
                return existing
            ts = now_utc()
            attempts = (
                session.query(TaskAttemptModel)
                .filter(TaskAttemptModel.task_id == task_id)
                .order_by(TaskAttemptModel.attempt_no.desc())
                .first()
            )
            job = AnalysisJobModel(
                id=f"analysis_{uuid4().hex}",
                task_id=task_id,
                task_attempt_id=attempts.id if attempts else None,
                analyzer_type=analyzer_type,
                analyzer_version=analyzer_version,
                input_checksum=input_checksum,
                input_artifact_ids_json=list(input_artifact_ids or []),
                idempotency_key=key,
                status="PENDING",
                status_reason="采集产物已持久化，等待 Analyzer Worker",
                retry_count=0,
                max_retries=max(0, int(max_retries)),
                next_run_at=ts,
                created_at=ts,
                updated_at=ts,
            )
            try:
                with session.begin_nested():
                    session.add(job)
                    session.flush()
            except IntegrityError:
                # Another API replica won the idempotency race.
                winner = (
                    session.query(AnalysisJobModel)
                    .filter(AnalysisJobModel.idempotency_key == key)
                    .first()
                )
                if winner is None:
                    raise
                return winner
            task = session.get(TaskModel, task_id)
            if task is not None:
                task.analysis_status = AnalysisStatus.QUEUED.value
            self._write_audit(
                session,
                "ANALYSIS_JOB_ENQUEUED",
                task_id=task_id,
                message=f"分析任务已入队: {analyzer_type}@{analyzer_version}",
                metadata={"analysis_job_id": job.id, "input_checksum": input_checksum},
            )
            record_analysis_job("PENDING", analyzer_type)
            return job

    def list_analysis_jobs(
        self, *, task_id: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[AnalysisJobModel]:
        with self._read_session() as session:
            query = session.query(AnalysisJobModel)
            if task_id:
                query = query.filter(AnalysisJobModel.task_id == task_id)
            if status:
                query = query.filter(AnalysisJobModel.status == status)
            return query.order_by(AnalysisJobModel.created_at.desc()).limit(max(1, min(limit, 500))).all()

    def get_analysis_job(self, job_id: str) -> AnalysisJobModel | None:
        with self._read_session() as session:
            return session.get(AnalysisJobModel, job_id)

    def analysis_job_counts(self) -> list[tuple[str, str, int]]:
        """Return durable state counts for cross-process Prometheus gauges."""

        with self._read_session() as session:
            rows = (
                session.query(
                    AnalysisJobModel.status,
                    AnalysisJobModel.analyzer_type,
                    func.count(AnalysisJobModel.id),
                )
                .group_by(AnalysisJobModel.status, AnalysisJobModel.analyzer_type)
                .all()
            )
            return [
                (str(status), str(analyzer_type), int(count))
                for status, analyzer_type, count in rows
            ]

    def claim_analysis_job(
        self, worker_id: str, *, lease_sec: int = 60
    ) -> AnalysisJobModel | None:
        """Lease one due job. Expired leases are recovered before claiming."""

        with self._write_session() as session:
            ts = now_utc()
            expired = (
                session.query(AnalysisJobModel)
                .filter(
                    AnalysisJobModel.status == "RUNNING",
                    AnalysisJobModel.lease_expires_at < ts,
                )
                .with_for_update(skip_locked=True)
                .all()
            )
            for job in expired:
                job.retry_count += 1
                job.lease_owner = None
                job.lease_expires_at = None
                job.error_code = "LEASE_EXPIRED"
                job.error_message = "Analyzer worker lease expired"
                job.status_reason = "Worker 租约过期，任务自动恢复"
                job.updated_at = ts
                if job.retry_count > job.max_retries:
                    job.status = "DEAD_LETTER"
                    job.status_reason = "Worker 租约多次过期，任务进入死信"
                    job.finished_at = ts
                    task = session.get(TaskModel, job.task_id)
                    if task is not None and task.status == TaskStatus.ANALYZING.value:
                        self._transition_task_in_session(
                            session,
                            job.task_id,
                            TaskStatus.FAILED,
                            "分析失败: LEASE_EXPIRED",
                            Actor.ANALYZER,
                            {"analysis_job_id": job.id},
                        )
                else:
                    job.status = "RETRYING"
                    job.next_run_at = ts
                    task = session.get(TaskModel, job.task_id)
                    if task is not None:
                        task.analysis_status = AnalysisStatus.RETRYING.value

            session.flush()
            job = (
                session.query(AnalysisJobModel)
                .filter(
                    AnalysisJobModel.status.in_(("PENDING", "RETRYING")),
                    AnalysisJobModel.next_run_at <= ts,
                )
                .order_by(AnalysisJobModel.created_at.asc())
                .with_for_update(skip_locked=True)
                .first()
            )
            if job is None:
                return None
            job.status = "RUNNING"
            job.status_reason = f"Worker {worker_id} 已领取"
            job.lease_owner = worker_id
            job.lease_expires_at = ts + timedelta(seconds=max(5, lease_sec))
            job.started_at = job.started_at or ts
            job.updated_at = ts
            task = session.get(TaskModel, job.task_id)
            if task is not None:
                task.analysis_status = AnalysisStatus.RUNNING.value
            record_analysis_job("RUNNING", job.analyzer_type)
            return job

    def renew_analysis_job_lease(
        self, job_id: str, worker_id: str, *, lease_sec: int = 60
    ) -> bool:
        with self._write_session() as session:
            job = session.get(AnalysisJobModel, job_id)
            if job is None or job.status != "RUNNING" or job.lease_owner != worker_id:
                return False
            ts = now_utc()
            job.lease_expires_at = ts + timedelta(seconds=max(5, lease_sec))
            job.updated_at = ts
            return True

    def complete_analysis_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        output_artifacts: list[dict[str, Any]] | None = None,
        output_artifact_ids: list[int] | None = None,
        reason: str = "Analyzer 已生成可视化结果",
    ) -> AnalysisJobModel:
        """Persist analyzer outputs and finish the parent task atomically."""

        with self._write_session() as session:
            job = session.get(AnalysisJobModel, job_id)
            if job is None:
                raise ValueError(f"分析任务 {job_id} 不存在")
            if job.status == "SUCCEEDED":
                return job
            if job.status != "RUNNING" or job.lease_owner != worker_id:
                raise ValueError("分析任务租约不属于当前 Worker")
            ts = now_utc()
            ids = list(output_artifact_ids or [])
            for artifact in output_artifacts or []:
                analyzer_verified = (
                    artifact.get("integrity_status") == "VERIFIED"
                    and bool(artifact.get("sha256"))
                )
                artifact = prepare_artifact(job.task_id, artifact)
                if analyzer_verified:
                    artifact["integrity_status"] = "VERIFIED"
                    artifact["integrity_reason"] = (
                        "Analyzer verified local bytes before temporary workspace cleanup"
                    )
                if artifact.get("object_key"):
                    artifact["local_path"] = None
                model = ArtifactModel(
                    task_id=job.task_id,
                    artifact_type=artifact.get("artifact_type", "analysis"),
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
                session.flush()
                ids.append(int(model.id))
            job.status = "SUCCEEDED"
            job.status_reason = reason
            job.output_artifact_ids_json = ids
            job.lease_owner = None
            job.lease_expires_at = None
            job.error_code = None
            job.error_message = None
            job.updated_at = ts
            job.finished_at = ts
            record_analysis_job("SUCCEEDED", job.analyzer_type)
            if job.started_at is not None:
                observe_analysis_job_duration(
                    (
                        ts.replace(tzinfo=None)
                        - job.started_at.replace(tzinfo=None)
                    ).total_seconds()
                )
            task = session.get(TaskModel, job.task_id)
            if task is not None and task.status == TaskStatus.ANALYZING.value:
                self._transition_task_in_session(
                    session, job.task_id, TaskStatus.DONE, reason, Actor.ANALYZER,
                    {"analysis_job_id": job.id, "analyzer_version": job.analyzer_version},
                )
            self._write_audit(
                session,
                "ANALYSIS_JOB_SUCCEEDED",
                task_id=job.task_id,
                message=reason,
                metadata={"analysis_job_id": job.id, "output_artifact_ids": ids},
            )
            return job

    def fail_analysis_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        error_code: str,
        error_message: str,
        retry_delay_sec: int = 5,
    ) -> AnalysisJobModel:
        with self._write_session() as session:
            job = session.get(AnalysisJobModel, job_id)
            if job is None:
                raise ValueError(f"分析任务 {job_id} 不存在")
            if job.status != "RUNNING" or job.lease_owner != worker_id:
                raise ValueError("分析任务租约不属于当前 Worker")
            ts = now_utc()
            job.retry_count += 1
            job.error_code = error_code[:128]
            job.error_message = error_message[:2000]
            job.status_reason = f"{error_code}: {error_message[:500]}"
            job.lease_owner = None
            job.lease_expires_at = None
            job.updated_at = ts
            if job.retry_count > job.max_retries:
                job.status = "DEAD_LETTER"
                job.status_reason = f"超过最大重试次数: {job.error_code}"
                job.finished_at = ts
                task = session.get(TaskModel, job.task_id)
                if task is not None and task.status == TaskStatus.ANALYZING.value:
                    self._transition_task_in_session(
                        session,
                        job.task_id,
                        TaskStatus.FAILED,
                        f"分析失败: {job.error_code}",
                        Actor.ANALYZER,
                        {"analysis_job_id": job.id},
                    )
                record_analysis_job("DEAD_LETTER", job.analyzer_type)
            else:
                job.status = "RETRYING"
                job.status_reason = f"分析失败，等待第 {job.retry_count + 1} 次执行"
                delay = max(0, retry_delay_sec) * (2 ** max(0, job.retry_count - 1))
                job.next_run_at = ts + timedelta(seconds=delay)
                task = session.get(TaskModel, job.task_id)
                if task is not None:
                    task.analysis_status = AnalysisStatus.RETRYING.value
                record_analysis_job("RETRYING", job.analyzer_type)
            return job

    def replay_analysis_job(self, job_id: str) -> AnalysisJobModel:
        with self._write_session() as session:
            job = session.get(AnalysisJobModel, job_id)
            if job is None:
                raise ValueError(f"分析任务 {job_id} 不存在")
            if job.status not in {"DEAD_LETTER", "RETRYING"}:
                raise ValueError("仅失败或待重试的分析任务可重放")
            ts = now_utc()
            job.status = "PENDING"
            job.status_reason = "人工重放，等待 Analyzer Worker"
            job.retry_count = 0
            job.next_run_at = ts
            job.finished_at = None
            job.error_code = None
            job.error_message = None
            job.updated_at = ts
            task = session.get(TaskModel, job.task_id)
            if task is not None:
                task.analysis_status = AnalysisStatus.QUEUED.value
            if (
                task is not None
                and task.status == TaskStatus.FAILED.value
                and (task.status_reason or "").startswith("分析失败:")
            ):
                # Administrative replay is the only supported terminal-state
                # reopen. It is recorded explicitly instead of weakening the
                # normal collection state machine for every caller.
                self._transition_task_in_session(
                    session,
                    job.task_id,
                    TaskStatus.ANALYZING,
                    "人工重放死信分析任务",
                    Actor.ANALYZER,
                    {"analysis_job_id": job.id, "administrative_replay": True},
                )
            self._write_audit(
                session,
                "ANALYSIS_JOB_REPLAYED",
                task_id=job.task_id,
                message="人工重放分析任务",
                metadata={"analysis_job_id": job.id},
            )
            return job
