"""诊断控制层持久化访问。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError

from server.app.database import new_session
from server.app.models import (
    AnalysisJobModel,
    ArtifactModel,
    ContinuousDiagnosisTriggerModel,
    DiagnosisEventModel,
    DiagnosisEvidenceModel,
    DiagnosisEvidenceSnapshotModel,
    DiagnosisNodeRunModel,
    DiagnosisOutboxModel,
    DiagnosisArtifactOutboxModel,
    DiagnosisEvaluationModel,
    FrozenDiagnosisArtifactModel,
    DiagnosisSessionModel,
    ProbeExecutionModel,
    TaskAttemptModel,
    TopologySnapshotModel,
)
from server.app.diagnosis.pipeline import PIPELINE_NODES, PIPELINE_VERSION, node_run_id
from server.app.evaluation.artifacts import artifact_hash, canonical_artifact_json
from server.app.evaluation.schemas import FrozenDiagnosisArtifact


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_before(value: datetime, reference: datetime) -> bool:
    """Compare database timestamps consistently when SQLite drops timezone info."""
    if value.tzinfo is None and reference.tzinfo is not None:
        value = value.replace(tzinfo=timezone.utc)
    elif value.tzinfo is not None and reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return value < reference


def _canonicalize_snapshot(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {
            str(key): _canonicalize_snapshot(item)
            for key, item in sorted(value.items())
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize_snapshot(item) for item in value]
    return value


def _snapshot_integrity_hash(snapshot: dict[str, Any]) -> str:
    canonical = {
        key: _canonicalize_snapshot(value)
        for key, value in sorted(snapshot.items())
        if key != "integrity_hash"
    }
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _same_snapshot(left: dict[str, Any], right: dict[str, Any]) -> bool:
    ignored = {"task_attempt_id"}
    return _canonicalize_snapshot({
        key: value for key, value in left.items() if key not in ignored
    }) == _canonicalize_snapshot({
        key: value for key, value in right.items() if key not in ignored
    })


class DiagnosisStore:
    def list_continuous_triggers(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return profiler anomaly promotions, newest first."""
        session = new_session()
        try:
            rows = (
                session.query(ContinuousDiagnosisTriggerModel)
                .order_by(ContinuousDiagnosisTriggerModel.created_at.desc())
                .offset(max(offset, 0))
                .limit(min(max(limit, 1), 1000))
                .all()
            )
            return [row.to_dict() for row in rows]
        finally:
            session.close()

    def count_continuous_triggers(self) -> int:
        session = new_session()
        try:
            return session.query(ContinuousDiagnosisTriggerModel).count()
        finally:
            session.close()

    def create_topology_snapshot(self, snapshot: dict[str, Any]) -> None:
        session = new_session()
        try:
            session.add(TopologySnapshotModel(
                id=snapshot["snapshot_id"],
                effective_at=snapshot["effective_at"],
                generated_at=snapshot["generated_at"],
                nodes_json=snapshot.get("nodes", []),
                edges_json=snapshot.get("edges", []),
                source_versions_json=snapshot.get("source_versions", {}),
                confidence_summary_json=snapshot.get("confidence_summary", {}),
            ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_session(self, data: dict[str, Any]) -> None:
        now = utcnow()
        session = new_session()
        try:
            model = DiagnosisSessionModel(
                id=data["diagnosis_id"],
                case_id=data.get("case_id"),
                creator_id=data["creator_id"],
                raw_query=data["raw_query"],
                normalized_intent_json=data.get("normalized_intent", {}),
                target_scope_json=data.get("target_scope", {}),
                requested_time_range_json=data.get("requested_time_range", {}),
                effective_time_range_json=data.get("effective_time_range", {}),
                topology_snapshot_id=data.get("topology_snapshot_id"),
                baseline_snapshot_id=data.get("baseline_snapshot_id"),
                status=data["status"],
                policy_profile=data["policy_profile"],
                risk_budget_json=data.get("risk_budget", {}),
                resource_budget_json=data.get("resource_budget", {}),
                budget_used_json=data.get("budget_used", {}),
                hypothesis_graph_json=data.get("hypothesis_graph", {}),
                child_task_ids_json=data.get("child_task_ids", []),
                conclusion_versions_json=data.get("conclusion_versions", []),
                model_version=data["model_version"],
                planner_version=data["planner_version"],
                row_version=0,
                deadline_at=data["deadline_at"],
                created_at=now,
                updated_at=now,
            )
            session.add(model)
            # PostgreSQL 会严格检查子表外键；先显式写入父会话，再在同一事务
            # 中写事件和流水线节点。SQLite 默认外键关闭，测试环境曾掩盖此问题。
            session.flush()
            session.add(DiagnosisEventModel(
                diagnosis_id=model.id,
                event_type="diagnosis_created",
                from_status=None,
                to_status=model.status,
                payload_json={},
                created_at=now,
            ))
            for sequence, node_name in enumerate(PIPELINE_NODES, start=1):
                session.add(DiagnosisNodeRunModel(
                    id=node_run_id(model.id, node_name),
                    diagnosis_id=model.id,
                    node_name=node_name,
                    sequence=sequence,
                    status="PENDING",
                    attempt=0,
                    input_refs_json=[],
                    output_refs_json=[],
                    metrics_json={},
                    implementation_version=PIPELINE_VERSION,
                    updated_at=now,
                ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def initialize_pipeline(self, diagnosis_id: str) -> None:
        now = utcnow()
        session = new_session()
        try:
            for sequence, node_name in enumerate(PIPELINE_NODES, start=1):
                key = node_run_id(diagnosis_id, node_name)
                if session.get(DiagnosisNodeRunModel, key) is None:
                    session.add(DiagnosisNodeRunModel(
                        id=key,
                        diagnosis_id=diagnosis_id,
                        node_name=node_name,
                        sequence=sequence,
                        status="PENDING",
                        attempt=0,
                        input_refs_json=[],
                        output_refs_json=[],
                        metrics_json={},
                        implementation_version=PIPELINE_VERSION,
                        updated_at=now,
                    ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_pipeline_node(
        self,
        diagnosis_id: str,
        node_name: str,
        status: str,
        *,
        input_refs: list[str] | None = None,
        output_refs: list[str] | None = None,
        metrics: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        allowed_statuses = {"PENDING", "RUNNING", "WAITING", "COMPLETED", "SKIPPED", "FAILED"}
        if status not in allowed_statuses:
            raise ValueError(f"非法节点状态: {status}")
        session = new_session()
        try:
            model = session.get(DiagnosisNodeRunModel, node_run_id(diagnosis_id, node_name))
            if model is None:
                raise ValueError(f"诊断节点不存在: {diagnosis_id}/{node_name}")
            now = utcnow()
            if status == "RUNNING" and model.status != "RUNNING":
                model.attempt += 1
                model.started_at = now
                model.finished_at = None
                model.error_code = None
                model.error_message = None
            if status in {"COMPLETED", "SKIPPED", "FAILED"}:
                model.finished_at = now
            model.status = status
            if input_refs is not None:
                model.input_refs_json = list(dict.fromkeys(input_refs))
            if output_refs is not None:
                model.output_refs_json = list(dict.fromkeys(output_refs))
            if metrics is not None:
                model.metrics_json = metrics
            model.error_code = error_code
            model.error_message = (error_message or "")[:2000] or None
            model.updated_at = now
            session.commit()
            return model.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_pipeline_nodes(self, diagnosis_id: str) -> list[dict[str, Any]]:
        session = new_session()
        try:
            rows = (
                session.query(DiagnosisNodeRunModel)
                .filter(DiagnosisNodeRunModel.diagnosis_id == diagnosis_id)
                .order_by(DiagnosisNodeRunModel.sequence.asc())
                .all()
            )
            return [row.to_dict() for row in rows]
        finally:
            session.close()

    def get_session(self, diagnosis_id: str) -> dict[str, Any] | None:
        session = new_session()
        try:
            model = session.get(DiagnosisSessionModel, diagnosis_id)
            return model.to_dict() if model else None
        finally:
            session.close()

    def list_sessions(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        session = new_session()
        try:
            rows = (
                session.query(DiagnosisSessionModel)
                .order_by(DiagnosisSessionModel.created_at.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            return [row.to_dict() for row in rows]
        finally:
            session.close()

    def list_active_sessions(self, terminal_statuses: set[str], limit: int = 100) -> list[dict[str, Any]]:
        session = new_session()
        try:
            rows = (
                session.query(DiagnosisSessionModel)
                .filter(~DiagnosisSessionModel.status.in_(terminal_statuses))
                .order_by(DiagnosisSessionModel.updated_at.asc())
                .limit(limit)
                .all()
            )
            return [row.to_dict() for row in rows]
        finally:
            session.close()

    def list_terminal_sessions_missing_artifact(
        self,
        terminal_statuses: set[str],
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        session = new_session()
        try:
            rows = (
                session.query(DiagnosisSessionModel)
                .outerjoin(
                    FrozenDiagnosisArtifactModel,
                    FrozenDiagnosisArtifactModel.diagnosis_id
                    == DiagnosisSessionModel.id,
                )
                .filter(
                    DiagnosisSessionModel.status.in_(terminal_statuses),
                    FrozenDiagnosisArtifactModel.id.is_(None),
                )
                .order_by(DiagnosisSessionModel.updated_at.asc())
                .limit(limit)
                .all()
            )
            return [row.to_dict() for row in rows]
        finally:
            session.close()

    def count_sessions(self) -> int:
        session = new_session()
        try:
            return session.query(DiagnosisSessionModel).count()
        finally:
            session.close()

    def update_session(self, diagnosis_id: str, *, expected_version: int | None = None, **fields: Any) -> dict[str, Any]:
        column_map = {
            "normalized_intent": "normalized_intent_json",
            "target_scope": "target_scope_json",
            "effective_time_range": "effective_time_range_json",
            "budget_used": "budget_used_json",
            "hypothesis_graph": "hypothesis_graph_json",
            "child_task_ids": "child_task_ids_json",
            "conclusion_versions": "conclusion_versions_json",
            "baseline_snapshot_id": "baseline_snapshot_id",
            "status": "status",
            "lease_owner": "lease_owner",
            "lease_until": "lease_until",
        }
        unknown = set(fields) - set(column_map)
        if unknown:
            raise ValueError(f"不允许更新诊断字段: {sorted(unknown)}")
        session = new_session()
        try:
            model = session.get(DiagnosisSessionModel, diagnosis_id)
            if model is None:
                raise ValueError(f"诊断 {diagnosis_id} 不存在")
            version = model.row_version
            if expected_version is not None and version != expected_version:
                raise RuntimeError("diagnosis session CAS conflict")
            values = {column_map[key]: value for key, value in fields.items()}
            values.update({"row_version": version + 1, "updated_at": utcnow()})
            changed = (
                session.query(DiagnosisSessionModel)
                .filter(DiagnosisSessionModel.id == diagnosis_id, DiagnosisSessionModel.row_version == version)
                .update(values, synchronize_session=False)
            )
            if changed != 1:
                raise RuntimeError("diagnosis session CAS conflict")
            session.commit()
            session.expire_all()
            return session.get(DiagnosisSessionModel, diagnosis_id).to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def transition(
        self,
        diagnosis_id: str,
        to_status: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = new_session()
        try:
            model = session.get(DiagnosisSessionModel, diagnosis_id)
            if model is None:
                raise ValueError(f"诊断 {diagnosis_id} 不存在")
            previous = model.status
            previous_version = model.row_version
            changed = (
                session.query(DiagnosisSessionModel)
                .filter(
                    DiagnosisSessionModel.id == diagnosis_id,
                    DiagnosisSessionModel.status == previous,
                    DiagnosisSessionModel.row_version == previous_version,
                )
                .update({
                    "status": to_status, "row_version": previous_version + 1, "updated_at": utcnow(),
                }, synchronize_session=False)
            )
            if changed != 1:
                raise RuntimeError("diagnosis transition CAS conflict")
            session.add(DiagnosisEventModel(
                diagnosis_id=diagnosis_id,
                event_type=event_type,
                from_status=previous,
                to_status=to_status,
                payload_json=payload or {},
                created_at=utcnow(),
            ))
            session.commit()
            session.expire_all()
            return session.get(DiagnosisSessionModel, diagnosis_id).to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def record_event(
        self,
        diagnosis_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        session = new_session()
        try:
            model = session.get(DiagnosisSessionModel, diagnosis_id)
            if model is None:
                raise ValueError(f"诊断 {diagnosis_id} 不存在")
            session.add(DiagnosisEventModel(
                diagnosis_id=diagnosis_id,
                event_type=event_type,
                from_status=model.status,
                to_status=model.status,
                payload_json=payload or {},
                created_at=utcnow(),
            ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def acquire_lease(self, diagnosis_id: str, owner: str, ttl_seconds: int = 30) -> bool:
        """短租约避免多个 API 实例同时推进同一会话。"""
        now = utcnow()
        session = new_session()
        try:
            model = session.get(DiagnosisSessionModel, diagnosis_id)
            if model is None:
                return False
            lease_until = model.lease_until
            if lease_until is not None and lease_until.tzinfo is None:
                lease_until = lease_until.replace(tzinfo=timezone.utc)
            changed = (
                session.query(DiagnosisSessionModel)
                .filter(
                    DiagnosisSessionModel.id == diagnosis_id,
                    DiagnosisSessionModel.row_version == model.row_version,
                    or_(DiagnosisSessionModel.lease_until.is_(None), DiagnosisSessionModel.lease_until <= now,
                        DiagnosisSessionModel.lease_owner == owner),
                )
                .update({
                    "lease_owner": owner,
                    "lease_until": now + timedelta(seconds=ttl_seconds),
                    "row_version": model.row_version + 1,
                    "updated_at": now,
                }, synchronize_session=False)
            )
            session.commit()
            return changed == 1
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def release_lease(self, diagnosis_id: str, owner: str) -> None:
        session = new_session()
        try:
            model = session.get(DiagnosisSessionModel, diagnosis_id)
            if model is not None:
                session.query(DiagnosisSessionModel).filter(
                    DiagnosisSessionModel.id == diagnosis_id,
                    DiagnosisSessionModel.lease_owner == owner,
                    DiagnosisSessionModel.row_version == model.row_version,
                ).update({
                    "lease_owner": None, "lease_until": None,
                    "row_version": model.row_version + 1, "updated_at": utcnow(),
                }, synchronize_session=False)
                session.commit()
        finally:
            session.close()

    def add_probe(self, probe: dict[str, Any]) -> dict[str, Any]:
        session = new_session()
        try:
            existing = session.get(ProbeExecutionModel, probe["step_id"])
            if existing is not None:
                return existing.to_dict()
            now = utcnow()
            model = ProbeExecutionModel(
                id=probe["step_id"],
                diagnosis_id=probe["diagnosis_id"],
                probe_id=probe["probe_id"],
                target_json=probe.get("target", {}),
                parameters_json=probe.get("parameters", {}),
                reason=probe["reason"],
                risk_level=probe["risk_level"],
                status=probe["status"],
                task_id=probe.get("task_id"),
                requires_approval=1 if probe.get("requires_approval") else 0,
                evidence_purpose=probe.get("evidence_purpose", "VERIFY"),
                round_index=int(probe.get("round_index", 1)),
                created_at=now,
                updated_at=now,
            )
            session.add(model)
            session.commit()
            return model.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_probe(self, step_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {"status", "task_id", "approved_by", "approved_at", "retry_count", "error_code", "error_message"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"不允许更新探针字段: {sorted(unknown)}")
        session = new_session()
        try:
            model = session.get(ProbeExecutionModel, step_id)
            if model is None:
                raise ValueError(f"探针步骤 {step_id} 不存在")
            for key, value in fields.items():
                setattr(model, key, value)
            model.updated_at = utcnow()
            session.commit()
            return model.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def enqueue_probe(self, step_id: str) -> dict[str, Any]:
        """Atomically mark a ready/approved step scheduled and create one outbox row."""
        session = new_session()
        try:
            probe = session.query(ProbeExecutionModel).filter(ProbeExecutionModel.id == step_id).with_for_update().first()
            if probe is None:
                raise ValueError(f"探针步骤 {step_id} 不存在")
            outbox = session.query(DiagnosisOutboxModel).filter(DiagnosisOutboxModel.step_id == step_id).first()
            if outbox is None:
                now = utcnow()
                outbox = DiagnosisOutboxModel(
                    id=f"outbox:{step_id}", diagnosis_id=probe.diagnosis_id, step_id=step_id,
                    status="PENDING", attempt=0, created_at=now, updated_at=now,
                )
                session.add(outbox)
            if probe.status in {"READY", "APPROVED", "PLANNED"}:
                probe.status = "SCHEDULED"
                probe.updated_at = utcnow()
            session.commit()
            return probe.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_pending_outbox(self, diagnosis_id: str, limit: int = 100) -> list[dict[str, Any]]:
        session = new_session()
        try:
            rows = (
                session.query(DiagnosisOutboxModel)
                .filter(DiagnosisOutboxModel.diagnosis_id == diagnosis_id, DiagnosisOutboxModel.status == "PENDING")
                .order_by(DiagnosisOutboxModel.created_at.asc()).limit(limit).all()
            )
            return [{"outbox_id": row.id, "step_id": row.step_id, "attempt": row.attempt} for row in rows]
        finally:
            session.close()

    def complete_outbox(self, outbox_id: str, error: str | None = None) -> None:
        session = new_session()
        try:
            row = session.get(DiagnosisOutboxModel, outbox_id)
            if row is not None:
                row.attempt += 1
                row.status = "FAILED" if error else "COMPLETED"
                row.last_error = (error or "")[:2000] or None
                row.updated_at = utcnow()
                session.commit()
        finally:
            session.close()

    def get_probe(self, step_id: str) -> dict[str, Any] | None:
        session = new_session()
        try:
            model = session.get(ProbeExecutionModel, step_id)
            return model.to_dict() if model else None
        finally:
            session.close()

    def list_probes(self, diagnosis_id: str) -> list[dict[str, Any]]:
        session = new_session()
        try:
            rows = (
                session.query(ProbeExecutionModel)
                .filter(ProbeExecutionModel.diagnosis_id == diagnosis_id)
                .order_by(
                    ProbeExecutionModel.created_at.asc(),
                    ProbeExecutionModel.id.asc(),
                )
                .all()
            )
            return [row.to_dict() for row in rows]
        finally:
            session.close()

    def add_evidence(self, evidence: dict[str, Any]) -> dict[str, Any]:
        session = new_session()
        try:
            existing = session.get(DiagnosisEvidenceModel, evidence["evidence_id"])
            if existing is not None:
                return existing.to_dict()
            model = DiagnosisEvidenceModel(
                id=evidence["evidence_id"],
                diagnosis_id=evidence["diagnosis_id"],
                source_type=evidence["source_type"],
                source_system=evidence["source_system"],
                evidence_role=evidence.get("evidence_role", "incident"),
                target_json=evidence.get("target", {}),
                event_time_range_json=evidence.get("event_time_range", {}),
                ingestion_time=evidence.get("ingestion_time", utcnow()),
                query_or_probe=evidence["query_or_probe"],
                raw_artifact_ref=evidence.get("raw_artifact_ref"),
                derived_artifact_ref=evidence.get("derived_artifact_ref"),
                derivation_version=evidence.get("derivation_version", "v1"),
                observed_value_json=evidence.get("observed_value", {}),
                baseline_value_json=evidence.get("baseline_value", {}),
                anomaly_score_json=evidence.get("anomaly_score", {}),
                data_quality_json=evidence.get("data_quality", {}),
                integrity_hash=evidence["integrity_hash"],
                claim_links_json=evidence.get("claim_links", []),
            )
            session.add(model)
            session.commit()
            return model.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_evidence(self, diagnosis_id: str) -> list[dict[str, Any]]:
        session = new_session()
        try:
            rows = (
                session.query(DiagnosisEvidenceModel)
                .filter(DiagnosisEvidenceModel.diagnosis_id == diagnosis_id)
                .order_by(
                    DiagnosisEvidenceModel.ingestion_time.asc(),
                    DiagnosisEvidenceModel.id.asc(),
                )
                .all()
            )
            return [row.to_dict() for row in rows]
        finally:
            session.close()

    def add_evidence_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Resolve provenance and append one immutable evidence snapshot."""
        session = new_session()
        try:
            finalized = dict(snapshot)
            task_id = finalized.get("task_id")
            if task_id:
                artifact_ids = {
                    int(value)
                    for value in finalized.pop("artifact_ids", [])
                    if value is not None and str(value).strip()
                }
                if artifact_ids:
                    owned_ids = {
                        row.id
                        for row in session.query(ArtifactModel).filter(
                            ArtifactModel.id.in_(artifact_ids),
                            ArtifactModel.task_id == task_id,
                        ).all()
                    }
                    if owned_ids != artifact_ids:
                        raise ValueError(
                            f"snapshot artifacts do not belong to task {task_id}"
                        )

                successful_jobs = session.query(AnalysisJobModel).filter(
                    AnalysisJobModel.task_id == task_id,
                    AnalysisJobModel.status.in_(("SUCCEEDED", "DONE")),
                ).all()
                relevant_jobs = []
                for job in successful_jobs:
                    job_artifact_ids = {
                        int(value)
                        for value in (
                            list(job.input_artifact_ids_json or [])
                            + list(job.output_artifact_ids_json or [])
                        )
                        if value is not None and str(value).strip()
                    }
                    if artifact_ids.intersection(job_artifact_ids):
                        relevant_jobs.append(job)

                job_attempt_ids = {
                    job.task_attempt_id
                    for job in relevant_jobs
                    if job.task_attempt_id
                }
                if len(job_attempt_ids) > 1:
                    raise ValueError(
                        f"conflicting analysis job attempt lineage for task {task_id}"
                    )
                if job_attempt_ids:
                    attempt = session.get(TaskAttemptModel, next(iter(job_attempt_ids)))
                else:
                    attempts = session.query(TaskAttemptModel).filter(
                        TaskAttemptModel.task_id == task_id,
                        TaskAttemptModel.status.in_(("SUCCEEDED", "DONE")),
                    ).all()
                    if len(attempts) != 1:
                        raise ValueError(
                            "task-backed snapshot requires one unambiguous successful "
                            f"attempt for task {task_id}; found {len(attempts)}"
                        )
                    attempt = attempts[0]

                if attempt is None or attempt.task_id != task_id:
                    raise ValueError(
                        f"snapshot attempt does not belong to task {task_id}"
                    )
                if attempt.status not in {"SUCCEEDED", "DONE"}:
                    raise ValueError(
                        f"snapshot attempt {attempt.id} is not successful"
                    )
                if relevant_jobs and any(
                    job.task_attempt_id is None for job in relevant_jobs
                ):
                    raise ValueError(
                        f"analysis job attempt lineage is missing for task {task_id}"
                    )

                started_at = attempt.started_at or attempt.created_at
                if started_at is None or attempt.finished_at is None:
                    raise ValueError(
                        f"successful attempt {attempt.id} lacks terminal timestamps"
                    )
                round_index = int(finalized.get("round_index", 1))
                evidence_role = finalized.get("evidence_role", "incident")
                identity = (
                    f"{finalized['diagnosis_id']}:{task_id}:{attempt.id}:"
                    f"{round_index}:{evidence_role}"
                )
                finalized["snapshot_id"] = (
                    "snap_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
                )
                finalized["attempt_id"] = attempt.id
                finalized["captured_at"] = attempt.finished_at
                time_range = dict(finalized.get("time_range", {}))
                time_range["start"] = _canonicalize_snapshot(started_at)
                time_range["end"] = _canonicalize_snapshot(attempt.finished_at)
                finalized["time_range"] = time_range
                finalized["created_at"] = attempt.finished_at
            else:
                finalized.pop("artifact_ids", None)
                finalized.setdefault("snapshot_id", snapshot["snapshot_id"])
                finalized.setdefault("captured_at", utcnow())
                finalized.setdefault("created_at", utcnow())

            finalized["evidence_refs"] = sorted(set(finalized.get("evidence_refs", [])))
            finalized["artifact_refs"] = sorted(set(finalized.get("artifact_refs", [])))
            finalized["integrity_hash"] = _snapshot_integrity_hash(finalized)

            existing = session.get(
                DiagnosisEvidenceSnapshotModel,
                finalized["snapshot_id"],
            )
            if existing is not None:
                existing_data = existing.to_dict()
                if not _same_snapshot(existing_data, finalized):
                    raise ValueError(
                        f"evidence snapshot identity conflict: {finalized['snapshot_id']}"
                    )
                return existing_data

            model = DiagnosisEvidenceSnapshotModel(
                id=finalized["snapshot_id"],
                diagnosis_id=finalized["diagnosis_id"],
                round_index=int(finalized.get("round_index", 1)),
                evidence_role=finalized.get("evidence_role", "incident"),
                captured_at=finalized["captured_at"],
                time_range_json=finalized.get("time_range", {}),
                target_json=finalized.get("target", {}),
                workload_identity_json=finalized.get("workload_identity", {}),
                deployment_version=finalized.get("deployment_version"),
                host_fingerprint_json=finalized.get("host_fingerprint", {}),
                collector=finalized["collector"],
                collector_version=finalized.get("collector_version"),
                task_id=task_id,
                attempt_id=finalized.get("attempt_id"),
                evidence_refs_json=finalized.get("evidence_refs", []),
                artifact_refs_json=finalized.get("artifact_refs", []),
                baseline_ref=finalized.get("baseline_ref"),
                quality_json=finalized.get("quality", {}),
                integrity_hash=finalized["integrity_hash"],
                created_at=finalized["created_at"],
            )
            session.add(model)
            session.commit()
            return model.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_evidence_snapshots(self, diagnosis_id: str) -> list[dict[str, Any]]:
        session = new_session()
        try:
            rows = (
                session.query(DiagnosisEvidenceSnapshotModel)
                .filter(DiagnosisEvidenceSnapshotModel.diagnosis_id == diagnosis_id)
                .order_by(
                    DiagnosisEvidenceSnapshotModel.round_index.asc(),
                    DiagnosisEvidenceSnapshotModel.captured_at.asc(),
                    DiagnosisEvidenceSnapshotModel.id.asc(),
                )
                .all()
            )
            return [row.to_dict() for row in rows]
        finally:
            session.close()

    def get_topology(self, snapshot_id: str | None) -> dict[str, Any] | None:
        if not snapshot_id:
            return None
        session = new_session()
        try:
            model = session.get(TopologySnapshotModel, snapshot_id)
            return model.to_dict() if model else None
        finally:
            session.close()

    def freeze_diagnosis_artifact(self, diagnosis_id: str) -> dict[str, Any]:
        """Freeze diagnosis-owned terminal output for evaluator consumption.

        The artifact is rebuilt from an explicit allowlist and is immutable once
        persisted. Evaluator state and arbitrary session metadata never enter it.
        """
        detail = self.get_detail(diagnosis_id)
        if detail is None:
            raise ValueError(f"诊断 {diagnosis_id} 不存在")
        terminal_status = detail.get("status")
        supported_statuses = {
            "COMPLETED", "INSUFFICIENT_EVIDENCE", "PARTIAL_COMPLETED",
            "BUDGET_EXHAUSTED", "TOPOLOGY_UNAVAILABLE", "USER_CANCELED", "FAILED",
        }
        if terminal_status not in supported_statuses:
            raise ValueError(f"诊断尚未进入可冻结终态: {terminal_status}")
        conclusion = detail.get("latest_conclusion")
        if not isinstance(conclusion, dict):
            raise ValueError("诊断缺少最终结论")
        verification = conclusion.get("verification")
        if not isinstance(verification, dict) or verification.get("status") != "passed":
            raise ValueError("最终结论尚未通过验证")

        artifact = FrozenDiagnosisArtifact.model_validate({
            "schema_version": "diagnosis-artifact-v1",
            "diagnosis_id": diagnosis_id,
            "case_id": detail.get("case_id"),
            "terminal_status": terminal_status,
            "normalized_intent": detail.get("normalized_intent", {}),
            "target_scope": detail.get("target_scope", {}),
            "requested_time_range": detail.get("requested_time_range", {}),
            "effective_time_range": detail.get("effective_time_range", {}),
            "topology": detail.get("topology_snapshot"),
            "conclusion": conclusion,
            "evidence": detail.get("evidence", []),
            "evidence_snapshots": detail.get("evidence_snapshots", []),
            "probes": detail.get("probes", []),
            "budget": {
                "risk": detail.get("risk_budget", {}),
                "resource": detail.get("resource_budget", {}),
                "used": detail.get("budget_used", {}),
            },
            "model_version": detail.get("model_version") or "unknown",
            "planner_version": detail.get("planner_version") or "unknown",
        })
        canonical_json = canonical_artifact_json(artifact)
        digest = artifact_hash(canonical_json)
        artifact_id = f"artifact:{diagnosis_id}"
        now = utcnow()
        session = new_session()
        try:
            def ensure_outbox(
                winner: FrozenDiagnosisArtifactModel,
                cause: Exception | None = None,
            ) -> dict[str, Any]:
                outbox = session.query(DiagnosisArtifactOutboxModel).filter(
                    DiagnosisArtifactOutboxModel.artifact_id == winner.id
                ).first()
                if outbox is not None:
                    if (
                        outbox.diagnosis_id != diagnosis_id
                        or outbox.artifact_hash != digest
                    ):
                        error = ValueError(
                            f"冻结产物通知完整性冲突: {diagnosis_id}"
                        )
                        if cause is not None:
                            raise error from cause
                        raise error
                    return winner.to_dict()

                recovery_now = utcnow()
                session.add(DiagnosisArtifactOutboxModel(
                    id=f"artifact-outbox:{winner.id}",
                    diagnosis_id=diagnosis_id,
                    artifact_id=winner.id,
                    artifact_hash=digest,
                    status="PENDING",
                    attempts=0,
                    next_attempt_at=recovery_now,
                    created_at=recovery_now,
                    updated_at=recovery_now,
                ))
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    recovered = session.query(DiagnosisArtifactOutboxModel).filter(
                        DiagnosisArtifactOutboxModel.artifact_id == winner.id
                    ).first()
                    if recovered is None:
                        raise
                    if (
                        recovered.diagnosis_id != diagnosis_id
                        or recovered.artifact_hash != digest
                    ):
                        error = ValueError(
                            f"冻结产物通知完整性冲突: {diagnosis_id}"
                        )
                        if cause is not None:
                            raise error from cause
                        raise error
                return winner.to_dict()

            existing = session.get(FrozenDiagnosisArtifactModel, artifact_id)
            if existing is not None:
                if existing.artifact_hash != digest or existing.canonical_json != canonical_json:
                    raise ValueError(f"冻结产物完整性冲突: {diagnosis_id}")
                return ensure_outbox(existing)
            by_diagnosis = (
                session.query(FrozenDiagnosisArtifactModel)
                .filter(FrozenDiagnosisArtifactModel.diagnosis_id == diagnosis_id)
                .first()
            )
            if by_diagnosis is not None:
                if by_diagnosis.artifact_hash != digest or by_diagnosis.canonical_json != canonical_json:
                    raise ValueError(f"冻结产物完整性冲突: {diagnosis_id}")
                return ensure_outbox(by_diagnosis)
            row = FrozenDiagnosisArtifactModel(
                id=artifact_id,
                diagnosis_id=diagnosis_id,
                schema_version=artifact.schema_version,
                terminal_status=artifact.terminal_status,
                canonical_json=canonical_json,
                artifact_hash=digest,
                created_at=now,
            )
            session.add(row)
            session.flush()
            session.add(DiagnosisArtifactOutboxModel(
                id=f"artifact-outbox:{artifact_id}",
                diagnosis_id=diagnosis_id,
                artifact_id=artifact_id,
                artifact_hash=digest,
                status="PENDING",
                attempts=0,
                next_attempt_at=now,
                created_at=now,
                updated_at=now,
            ))
            session.commit()
            return row.to_dict()
        except IntegrityError as exc:
            session.rollback()
            winner = session.get(FrozenDiagnosisArtifactModel, artifact_id)
            if winner is None:
                winner = (
                    session.query(FrozenDiagnosisArtifactModel)
                    .filter(FrozenDiagnosisArtifactModel.diagnosis_id == diagnosis_id)
                    .first()
                )
            if winner is None:
                raise
            if winner.artifact_hash != digest or winner.canonical_json != canonical_json:
                raise ValueError(f"冻结产物完整性冲突: {diagnosis_id}") from exc

            return ensure_outbox(winner, exc)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_frozen_diagnosis_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        session = new_session()
        try:
            row = session.get(FrozenDiagnosisArtifactModel, artifact_id)
            if row is None:
                return None
            result = row.to_dict()
            recomputed = artifact_hash(canonical_artifact_json(result["payload"]))
            if recomputed != row.artifact_hash:
                raise ValueError(f"冻结产物完整性校验失败: {artifact_id}")
            return result
        finally:
            session.close()

    @staticmethod
    def _artifact_outbox_dict(row: DiagnosisArtifactOutboxModel) -> dict[str, Any]:
        return {
            "outbox_id": row.id,
            "diagnosis_id": row.diagnosis_id,
            "artifact_id": row.artifact_id,
            "artifact_hash": row.artifact_hash,
            "status": row.status,
            "attempts": row.attempts or 0,
            "next_attempt_at": row.next_attempt_at,
            "worker_lease_owner": row.worker_lease_owner,
            "worker_lease_expires_at": row.worker_lease_expires_at,
            "last_error": row.last_error,
            "published_at": row.published_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def list_pending_artifact_outbox(self, limit: int = 100) -> list[dict[str, Any]]:
        session = new_session()
        try:
            rows = (
                session.query(DiagnosisArtifactOutboxModel)
                .filter(DiagnosisArtifactOutboxModel.status == "PENDING")
                .order_by(
                    DiagnosisArtifactOutboxModel.created_at.asc(),
                    DiagnosisArtifactOutboxModel.id.asc(),
                )
                .limit(max(1, int(limit)))
                .all()
            )
            return [self._artifact_outbox_dict(row) for row in rows]
        finally:
            session.close()

    @staticmethod
    def _artifact_outbox_integrity_code(
        row: DiagnosisArtifactOutboxModel,
        artifact: FrozenDiagnosisArtifactModel | None,
    ) -> str | None:
        if artifact is None:
            return "INTEGRITY_ARTIFACT_NOT_FOUND"
        try:
            validated = FrozenDiagnosisArtifact.model_validate_json(
                artifact.canonical_json
            )
            canonical = canonical_artifact_json(validated)
        except Exception:
            return "INTEGRITY_ARTIFACT_MALFORMED"
        if artifact_hash(canonical) != artifact.artifact_hash:
            return "INTEGRITY_ARTIFACT_HASH_INVALID"
        if (
            artifact.diagnosis_id != row.diagnosis_id
            or artifact.artifact_hash != row.artifact_hash
        ):
            return "INTEGRITY_ARTIFACT_LINEAGE_MISMATCH"
        return None

    def claim_artifact_outbox(
        self,
        worker_id: str,
        limit: int = 10,
        *,
        lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Claim due artifact notifications, including expired worker leases."""
        if not worker_id.strip():
            raise ValueError("artifact outbox worker_id is required")
        now = now or utcnow()
        session = new_session()
        try:
            claim_limit = max(1, int(limit))
            lease_expires_at = now + timedelta(
                seconds=max(1, int(lease_seconds))
            )
            eligibility = or_(
                and_(
                    DiagnosisArtifactOutboxModel.status.in_(["PENDING", "FAILED"]),
                    DiagnosisArtifactOutboxModel.next_attempt_at <= now,
                ),
                and_(
                    DiagnosisArtifactOutboxModel.status == "DISPATCHING",
                    DiagnosisArtifactOutboxModel.worker_lease_expires_at < now,
                ),
            )
            claimed_ids: list[str] = []
            seen_ids: set[str] = set()
            while len(claimed_ids) < claim_limit:
                query = session.query(DiagnosisArtifactOutboxModel).filter(
                    eligibility
                )
                if seen_ids:
                    query = query.filter(
                        DiagnosisArtifactOutboxModel.id.notin_(seen_ids)
                    )
                rows = (
                    query.order_by(
                        DiagnosisArtifactOutboxModel.created_at.asc(),
                        DiagnosisArtifactOutboxModel.id.asc(),
                    )
                    .limit(claim_limit - len(claimed_ids))
                    .with_for_update(skip_locked=True)
                    .all()
                )
                if not rows:
                    break
                seen_ids.update(row.id for row in rows)
                for row in rows:
                    artifact = session.get(
                        FrozenDiagnosisArtifactModel, row.artifact_id
                    )
                    integrity_code = self._artifact_outbox_integrity_code(
                        row, artifact
                    )
                    if integrity_code is not None:
                        session.query(DiagnosisArtifactOutboxModel).filter(
                            DiagnosisArtifactOutboxModel.id == row.id,
                            eligibility,
                        ).update({
                            "status": "DEAD_LETTER",
                            "last_error": integrity_code,
                            "worker_lease_owner": None,
                            "worker_lease_expires_at": None,
                            "updated_at": now,
                        }, synchronize_session=False)
                        continue
                    changed = session.query(DiagnosisArtifactOutboxModel).filter(
                        DiagnosisArtifactOutboxModel.id == row.id,
                        eligibility,
                    ).update({
                        "status": "DISPATCHING",
                        "worker_lease_owner": worker_id,
                        "worker_lease_expires_at": lease_expires_at,
                        "updated_at": now,
                    }, synchronize_session=False)
                    if changed == 1:
                        claimed_ids.append(row.id)
            session.commit()
            if not claimed_ids:
                return []
            session.expire_all()
            claimed = session.query(DiagnosisArtifactOutboxModel).filter(
                DiagnosisArtifactOutboxModel.id.in_(claimed_ids),
                DiagnosisArtifactOutboxModel.status == "DISPATCHING",
                DiagnosisArtifactOutboxModel.worker_lease_owner == worker_id,
            ).all()
            claimed_by_id = {row.id: row for row in claimed}
            return [
                self._artifact_outbox_dict(claimed_by_id[outbox_id])
                for outbox_id in claimed_ids
                if outbox_id in claimed_by_id
            ]
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def mark_artifact_outbox_published(
        self,
        outbox_id: str,
        worker_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Acknowledge a claimed notification; repeated acknowledgments are safe."""
        now = now or utcnow()
        session = new_session()
        try:
            row = session.get(DiagnosisArtifactOutboxModel, outbox_id)
            if row is None:
                return None
            if row.status == "PUBLISHED":
                return self._artifact_outbox_dict(row)
            if row.status != "DISPATCHING" or row.worker_lease_owner != worker_id:
                raise ValueError(f"artifact outbox lease owner mismatch: {outbox_id}")
            if row.worker_lease_expires_at is None or _is_before(
                row.worker_lease_expires_at, now
            ):
                raise ValueError(f"artifact outbox lease expired: {outbox_id}")
            row.status = "PUBLISHED"
            row.published_at = now
            row.updated_at = now
            row.worker_lease_owner = None
            row.worker_lease_expires_at = None
            session.commit()
            return self._artifact_outbox_dict(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def fail_artifact_outbox(
        self,
        outbox_id: str,
        worker_id: str,
        error: str,
        *,
        max_attempts: int = 5,
        now: datetime | None = None,
    ) -> str:
        """Record a claimed delivery failure and schedule retry or dead-letter."""
        now = now or utcnow()
        session = new_session()
        try:
            row = session.get(DiagnosisArtifactOutboxModel, outbox_id)
            if row is None:
                return "UNKNOWN"
            if row.status == "PUBLISHED":
                return "PUBLISHED"
            if row.status != "DISPATCHING" or row.worker_lease_owner != worker_id:
                raise ValueError(f"artifact outbox lease owner mismatch: {outbox_id}")
            if row.worker_lease_expires_at is None or _is_before(
                row.worker_lease_expires_at, now
            ):
                raise ValueError(f"artifact outbox lease expired: {outbox_id}")
            row.attempts = (row.attempts or 0) + 1
            row.last_error = (error or "")[:2000]
            row.updated_at = now
            row.worker_lease_owner = None
            row.worker_lease_expires_at = None
            if row.attempts >= max(1, int(max_attempts)):
                row.status = "DEAD_LETTER"
            else:
                row.status = "FAILED"
                row.next_attempt_at = now + timedelta(
                    seconds=min(3600, (2 ** row.attempts) * 5)
                )
            session.commit()
            return row.status
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_detail(self, diagnosis_id: str) -> dict[str, Any] | None:
        item = self.get_session(diagnosis_id)
        if item is None:
            return None
        session = new_session()
        try:
            events = (
                session.query(DiagnosisEventModel)
                .filter(DiagnosisEventModel.diagnosis_id == diagnosis_id)
                .order_by(DiagnosisEventModel.id.asc())
                .all()
            )
            item["events"] = [event.to_dict() for event in events]
        finally:
            session.close()
        item["topology_snapshot"] = self.get_topology(item.get("topology_snapshot_id"))
        item["probes"] = self.list_probes(diagnosis_id)
        coverage_status = {
            "READY": "QUEUED", "PLANNED": "PLANNED", "SCHEDULED": "SCHEDULED",
            "RUNNING": "RUNNING", "COMPLETED": "COMPLETED", "FAILED": "FAILED",
            "TIMED_OUT": "TIMED_OUT", "UNAVAILABLE": "UNAVAILABLE",
            "REJECTED": "REJECTED", "REJECTED_POLICY": "REJECTED",
            "WAITING_APPROVAL": "WAITING_APPROVAL", "SKIPPED": "SKIPPED",
        }
        item["coverage"] = [{
            "target": probe.get("target", {}).get("instance_id"),
            "requirement": probe.get("probe_id"),
            "status": coverage_status.get(probe.get("status"), probe.get("status")),
            "step_id": probe.get("step_id"),
            "task_id": probe.get("task_id"),
            "error_code": probe.get("error_code"),
        } for probe in item["probes"]]
        item["evidence"] = self.list_evidence(diagnosis_id)
        item["evidence_snapshots"] = self.list_evidence_snapshots(diagnosis_id)
        pipeline_nodes = self.list_pipeline_nodes(diagnosis_id)
        if not pipeline_nodes:
            # create_all 只会创建新表；为升级前的历史会话补一组明确标记的节点，
            # 避免前端把“没有历史节点数据”误判为仍在执行。
            self.initialize_pipeline(diagnosis_id)
            for node_name in PIPELINE_NODES:
                self.update_pipeline_node(
                    diagnosis_id, node_name, "SKIPPED",
                    metrics={"reason": "legacy_session_without_node_history"},
                )
            pipeline_nodes = self.list_pipeline_nodes(diagnosis_id)
        item["pipeline_nodes"] = pipeline_nodes
        conclusions = item.get("conclusion_versions", [])
        item["latest_conclusion"] = conclusions[-1] if conclusions else None
        return item
