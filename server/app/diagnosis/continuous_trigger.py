"""Promote deterministic continuous-profiling anomalies into AI diagnoses."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from server.app.database import new_session
from server.app.diagnosis.schemas import (
    CreateDiagnosisRequest,
    DiagnosisContext,
    DiagnosisMode,
    ServiceInstance,
    TimeRange,
)
from server.app.models import (
    ArtifactModel,
    ContinuousDiagnosisTriggerModel,
    TaskModel,
)


class ContinuousDiagnosisTrigger:
    """Exactly-once bridge from profiler facts to an AI diagnosis session."""

    def __init__(self, orchestrator) -> None:
        self.orchestrator = orchestrator

    def scan_once(self, limit: int = 20) -> int:
        session = new_session()
        try:
            rows = (
                session.query(ArtifactModel, TaskModel)
                .join(TaskModel, TaskModel.id == ArtifactModel.task_id)
                .outerjoin(
                    ContinuousDiagnosisTriggerModel,
                    ContinuousDiagnosisTriggerModel.task_id == TaskModel.id,
                )
                .filter(
                    ArtifactModel.artifact_type == "continuous_summary",
                    TaskModel.collector_type == "continuous_perf",
                    TaskModel.status == "DONE",
                    ContinuousDiagnosisTriggerModel.id.is_(None),
                )
                .order_by(ArtifactModel.id.asc())
                .limit(limit)
                .all()
            )
            candidates = [
                (artifact.to_dict(), task.to_dict(), task.agent.hostname)
                for artifact, task in rows
                if bool(
                    (artifact.meta_json or {})
                    .get("anomaly_detection", {})
                    .get("triggered")
                )
            ]
        finally:
            session.close()

        promoted = 0
        for artifact, task, hostname in candidates:
            if self._promote(artifact, task, hostname):
                promoted += 1
        return promoted

    def _promote(
        self,
        artifact: dict[str, Any],
        task: dict[str, Any],
        hostname: str,
    ) -> bool:
        score = (artifact.get("metadata") or {}).get("anomaly_detection") or {}
        trigger_id = f"ctrigger_{uuid4().hex}"
        now = datetime.now(timezone.utc)

        session = new_session()
        try:
            session.add(ContinuousDiagnosisTriggerModel(
                id=trigger_id,
                task_id=task["id"],
                artifact_id=artifact["id"],
                detector_version=str(score.get("detector_version", "unknown"))[:64],
                status="PROMOTING",
                score_json=score,
                created_at=now,
                updated_at=now,
            ))
            session.commit()
        except IntegrityError:
            session.rollback()
            return False
        finally:
            session.close()

        try:
            request = self._build_request(task, hostname, score, now)
            detail = self.orchestrator.create(
                request, creator_id="continuous-profiler",
            )
            diagnosis_id = detail["diagnosis_id"]
        except Exception as exc:
            self._finish(
                trigger_id,
                status="FAILED",
                error_message=f"{type(exc).__name__}: {exc}"[:2000],
            )
            return False

        self._finish(
            trigger_id,
            status="PROMOTED",
            diagnosis_id=diagnosis_id,
        )
        return True

    @staticmethod
    def _build_request(
        task: dict[str, Any],
        hostname: str,
        score: dict[str, Any],
        now: datetime,
    ) -> CreateDiagnosisRequest:
        params = task.get("request_params") or {}
        options = params.get("options") or {}
        end = task.get("finished_at") or now
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        start = end - timedelta(minutes=5)
        service_id = str(options.get("service_id") or task["name"])[:128]
        instance_id = str(
            options.get("instance_id") or f"{task['agent_id']}:{task['target_pid']}"
        )[:128]
        environment = str(options.get("environment") or "unknown")[:64]
        reason = str(score.get("reason") or "continuous_profile_shift")
        return CreateDiagnosisRequest(
            query=(
                f"持续性能采样检测到 {service_id} 的 CPU profile 异常"
                f"（{reason}），请基于异常窗口、历史基线和反证采集定位原因"
            ),
            context=DiagnosisContext(
                service_id=service_id,
                environment=environment,
                time_range=TimeRange(
                    start=start,
                    end=end,
                    source="request_context",
                ),
                instances=[
                    ServiceInstance(
                        service_id=service_id,
                        instance_id=instance_id,
                        host_id=str(options.get("host_id") or hostname)[:128],
                        agent_id=task["agent_id"],
                        pid=int(task["target_pid"]),
                        container_id=options.get("container_name"),
                        environment=environment,
                    )
                ],
            ),
            budget_profile="production_safe",
            diagnosis_mode=DiagnosisMode.LIVE,
        )

    @staticmethod
    def _finish(
        trigger_id: str,
        *,
        status: str,
        diagnosis_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        session = new_session()
        try:
            model = session.get(ContinuousDiagnosisTriggerModel, trigger_id)
            if model is None:
                return
            model.status = status
            model.diagnosis_id = diagnosis_id
            model.error_message = error_message
            model.updated_at = datetime.now(timezone.utc)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
