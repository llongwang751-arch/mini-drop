"""SQLAlchemy ORM 模型定义。

与 InMemoryRepository 的数据类结构对齐，
通过 SQLAlchemy 2.0 DeclarativeBase 映射。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ── Agent ────────────────────────────────────────────────────────


class AgentModel(Base):
    __tablename__ = "agents"

    id = Column(String(128), primary_key=True)
    hostname = Column(String(256), nullable=False)
    ip_addr = Column(String(64), nullable=False)
    version = Column(String(32), default="0.1.0")
    os_info = Column(String(256), default="unknown")
    capabilities = Column(JSON, default=list)
    status = Column(String(16), default="ONLINE")
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "hostname": self.hostname,
            "ip_addr": self.ip_addr,
            "version": self.version,
            "os_info": self.os_info,
            "capabilities": self.capabilities or [],
            "status": self.status,
            "last_heartbeat_at": self.last_heartbeat_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ── Task ────────────────────────────────────────────────────────


class TaskModel(Base):
    __tablename__ = "tasks"

    id = Column(String(128), primary_key=True)
    name = Column(String(256), nullable=False)
    agent_id = Column(String(128), ForeignKey("agents.id"), nullable=False)
    target_pid = Column(Integer, nullable=False)
    collector_type = Column(String(32), nullable=False)
    sample_rate = Column(Integer, default=99)
    duration_sec = Column(Integer, default=15)
    status = Column(String(16), nullable=False)
    status_reason = Column(Text, default="")
    collection_status = Column(String(16), nullable=False, default="QUEUED")
    analysis_status = Column(String(16), nullable=False, default="NOT_STARTED")
    request_params = Column(JSON, default=dict)
    diagnosis_step_id = Column(String(128), nullable=True, unique=True, index=True)
    idempotency_key = Column(String(128), nullable=True)
    creator_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    deleted_by = Column(String(128), nullable=True)
    delete_reason = Column(Text, nullable=True)

    agent = relationship("AgentModel", lazy="selectin")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "agent_id": self.agent_id,
            "target_pid": self.target_pid,
            "collector_type": self.collector_type,
            "sample_rate": self.sample_rate,
            "duration_sec": self.duration_sec,
            "status": self.status,
            "status_reason": self.status_reason or "",
            "collection_status": self.collection_status or "QUEUED",
            "analysis_status": self.analysis_status or "NOT_STARTED",
            "request_params": self.request_params or {},
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "deleted_at": self.deleted_at,
        }


# ── 状态事件 ────────────────────────────────────────────────────


class TaskAttemptModel(Base):
    """One concrete execution of a logical task."""

    __tablename__ = "task_attempts"
    __table_args__ = (
        UniqueConstraint("task_id", "attempt_no", name="uq_task_attempt_no"),
    )

    id = Column(String(128), primary_key=True)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    attempt_no = Column(Integer, nullable=False)
    agent_id = Column(String(128), ForeignKey("agents.id"), nullable=False)
    status = Column(String(16), nullable=False)
    reason = Column(Text, default="")
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "attempt_no": self.attempt_no,
            "agent_id": self.agent_id,
            "status": self.status,
            "reason": self.reason or "",
            "lease_expires_at": self.lease_expires_at,
            "metadata": self.metadata_json or {},
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class StatusEventModel(Base):
    __tablename__ = "task_status_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    from_status = Column(String(16), nullable=True)
    to_status = Column(String(16), nullable=False)
    reason = Column(Text, nullable=False)
    actor = Column(String(16), nullable=False)
    meta_json = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "reason": self.reason,
            "actor": self.actor,
            "metadata": self.meta_json or {},
            "created_at": self.created_at,
        }


# ── 审计日志 ────────────────────────────────────────────────────


class AuditLogModel(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(32), nullable=False)
    message = Column(Text, nullable=False)
    agent_id = Column(String(128), nullable=True)
    task_id = Column(String(128), nullable=True)
    meta_json = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type,
            "message": self.message,
            "agent_id": self.agent_id,
            "task_id": self.task_id,
            "metadata": self.meta_json or {},
            "created_at": self.created_at,
        }


# ── 产物 ───────────────────────────────────────────────────────


class DropInsightSessionModel(Base):
    __tablename__ = "drop_insight_sessions"

    id = Column(String(128), primary_key=True)
    query = Column(Text, nullable=False)
    target_json = Column(JSON, default=dict)
    time_range_json = Column(JSON, default=dict)
    mode = Column(String(32), nullable=False)
    budget_json = Column(JSON, default=dict)
    status = Column(String(32), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    clarification_questions_json = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    # 软归档字段：删除后从列表隐藏，但保留全部证据与审计可追溯（同任务归档策略）。
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by = Column(String(128), nullable=True)
    delete_reason = Column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "diagnosis_id": self.id,
            "query": self.query,
            "target": self.target_json or {},
            "time_range": self.time_range_json or {},
            "mode": self.mode,
            "budget": self.budget_json or {},
            "status": self.status,
            "version": self.version,
            "clarification_questions": self.clarification_questions_json or [],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deleted_at": self.deleted_at,
            "deleted_by": self.deleted_by,
        }


class DropInsightEventModel(Base):
    __tablename__ = "drop_insight_events"
    __table_args__ = (
        UniqueConstraint("diagnosis_id", "sequence", name="uq_drop_insight_event_sequence"),
    )

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(
        String(128),
        ForeignKey("drop_insight_sessions.id"),
        nullable=False,
        index=True,
    )
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(64), nullable=False)
    actor = Column(String(32), nullable=False)
    payload_json = Column(JSON, default=dict)
    occurred_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "event_id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "actor": self.actor,
            "payload": self.payload_json or {},
            "occurred_at": self.occurred_at,
        }


class DropInsightHypothesisModel(Base):
    __tablename__ = "drop_insight_hypotheses"

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(
        String(128),
        ForeignKey("drop_insight_sessions.id"),
        nullable=False,
        index=True,
    )
    statement = Column(Text, nullable=False)
    expected_observations_json = Column(JSON, default=list)
    falsification_criteria_json = Column(JSON, default=list)
    status = Column(String(32), nullable=False, default="OPEN")
    source = Column(String(32), nullable=False, default="DETERMINISTIC_RULE")
    round_index = Column(Integer, nullable=False, default=1)
    parent_hypothesis_id = Column(String(128), nullable=True, index=True)
    generation_reason = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "hypothesis_id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "statement": self.statement,
            "expected_observations": self.expected_observations_json or [],
            "falsification_criteria": self.falsification_criteria_json or [],
            "status": self.status,
            "source": self.source,
            "round_index": self.round_index,
            "parent_hypothesis_id": self.parent_hypothesis_id,
            "generation_reason": self.generation_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class DropInsightFeedbackModel(Base):
    """Human correction for a v2 diagnosis conclusion.

    Feedback is stored independently from reports so a wrong conclusion can be
    preserved for audit while a later diagnostic round supersedes it.
    """

    __tablename__ = "drop_insight_feedback"

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(
        String(128), ForeignKey("drop_insight_sessions.id"), nullable=False, index=True
    )
    report_id = Column(
        String(128), ForeignKey("drop_insight_reports.id"), nullable=True, index=True
    )
    hypothesis_id = Column(
        String(128), ForeignKey("drop_insight_hypotheses.id"), nullable=True, index=True
    )
    feedback_label = Column(String(16), nullable=False)
    predicted_conclusion = Column(Text, nullable=False, default="")
    corrected_cause = Column(Text, nullable=True)
    feedback_note = Column(Text, nullable=True)
    requested_replan = Column(Boolean, nullable=False, default=False)
    revision_hypothesis_id = Column(String(128), nullable=True)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "feedback_id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "report_id": self.report_id,
            "hypothesis_id": self.hypothesis_id,
            "feedback_label": self.feedback_label,
            "predicted_conclusion": self.predicted_conclusion,
            "corrected_cause": self.corrected_cause,
            "feedback_note": self.feedback_note,
            "requested_replan": self.requested_replan,
            "revision_hypothesis_id": self.revision_hypothesis_id,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }


class DropInsightEvidenceModel(Base):
    __tablename__ = "drop_insight_evidence"

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(
        String(128),
        ForeignKey("drop_insight_sessions.id"),
        nullable=False,
        index=True,
    )
    hypothesis_id = Column(
        String(128),
        ForeignKey("drop_insight_hypotheses.id"),
        nullable=True,
        index=True,
    )
    role = Column(String(16), nullable=False)
    envelope_json = Column(JSON, nullable=False)
    classification_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "hypothesis_id": self.hypothesis_id,
            "role": self.role,
            "envelope": self.envelope_json or {},
            "classification": self.classification_json or {},
            "created_at": self.created_at,
        }


class DropInsightReportModel(Base):
    __tablename__ = "drop_insight_reports"

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(
        String(128),
        ForeignKey("drop_insight_sessions.id"),
        nullable=False,
        index=True,
    )
    hypothesis_id = Column(
        String(128),
        ForeignKey("drop_insight_hypotheses.id"),
        nullable=True,
        index=True,
    )
    conclusion = Column(Text, nullable=False)
    confidence = Column(Integer, nullable=False)
    evidence_refs_json = Column(JSON, default=list)
    counter_evidence_refs_json = Column(JSON, default=list)
    assumptions_json = Column(JSON, default=list)
    limitations_json = Column(JSON, default=list)
    next_actions_json = Column(JSON, default=list)
    claims_json = Column(JSON, default=list)
    verification_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "report_id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "hypothesis_id": self.hypothesis_id,
            "conclusion": self.conclusion,
            "confidence": self.confidence / 1000,
            "evidence_refs": self.evidence_refs_json or [],
            "counter_evidence_refs": self.counter_evidence_refs_json or [],
            "assumptions": self.assumptions_json or [],
            "limitations": self.limitations_json or [],
            "next_actions": self.next_actions_json or [],
            "claims": self.claims_json or [],
            "verification": self.verification_json or {},
            "created_at": self.created_at,
        }


class DropInsightToolCallModel(Base):
    __tablename__ = "drop_insight_tool_calls"

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(
        String(128),
        ForeignKey("drop_insight_sessions.id"),
        nullable=False,
        index=True,
    )
    hypothesis_id = Column(
        String(128),
        ForeignKey("drop_insight_hypotheses.id"),
        nullable=True,
        index=True,
    )
    tool_name = Column(String(128), nullable=False)
    arguments_json = Column(JSON, nullable=False)
    policy_decision = Column(String(32), nullable=False)
    policy_checks_json = Column(JSON, default=list)
    policy_reason = Column(Text, nullable=False)
    status = Column(String(32), nullable=False)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=True, index=True)
    result_json = Column(JSON, default=dict)
    budget_reservation_json = Column(JSON, default=dict)
    budget_settlement_json = Column(JSON, default=dict)
    budget_reservation_status = Column(String(32), nullable=False, default="NONE")
    requested_by = Column(String(128), nullable=False)
    approved_by = Column(String(128), nullable=True)
    approval_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    executed_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "tool_call_id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "hypothesis_id": self.hypothesis_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments_json or {},
            "policy_decision": self.policy_decision,
            "policy_checks": self.policy_checks_json or [],
            "policy_reason": self.policy_reason,
            "status": self.status,
            "task_id": self.task_id,
            "result": self.result_json or {},
            "budget_reservation": self.budget_reservation_json or {},
            "budget_settlement": self.budget_settlement_json or {},
            "budget_reservation_status": self.budget_reservation_status,
            "requested_by": self.requested_by,
            "approved_by": self.approved_by,
            "approval_reason": self.approval_reason,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "executed_at": self.executed_at,
        }


class ArtifactModel(Base):
    __tablename__ = "artifacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    artifact_type = Column(String(32), nullable=False)
    bucket = Column(String(64), default="mini-drop")
    object_key = Column(String(512), nullable=False)
    filename = Column(String(256), nullable=True)
    local_path = Column(String(512), nullable=True)
    content_type = Column(String(128), default="application/octet-stream")
    size_bytes = Column(Integer, default=0)
    sha256 = Column(String(64), nullable=True, index=True)
    manifest_json = Column(JSON, default=dict)
    integrity_status = Column(String(32), nullable=False, default="LEGACY_UNVERIFIED")
    integrity_reason = Column(Text, nullable=False, default="")
    meta_json = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "artifact_type": self.artifact_type,
            "bucket": self.bucket,
            "object_key": self.object_key,
            "filename": self.filename,
            "local_path": self.local_path,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "manifest": self.manifest_json or {},
            "integrity_status": self.integrity_status,
            "integrity_reason": self.integrity_reason,
            "metadata": self.meta_json or {},
        }


class AnalysisJobModel(Base):
    """Durable, lease-based analyzer execution."""

    __tablename__ = "analysis_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_analysis_job_idempotency_key"),
    )

    id = Column(String(128), primary_key=True)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    task_attempt_id = Column(
        String(128), ForeignKey("task_attempts.id"), nullable=True, index=True
    )
    analyzer_type = Column(String(64), nullable=False, index=True)
    analyzer_version = Column(String(64), nullable=False)
    input_checksum = Column(String(64), nullable=False)
    input_artifact_ids_json = Column(JSON, default=list)
    idempotency_key = Column(String(512), nullable=False)
    status = Column(String(32), nullable=False, index=True)
    status_reason = Column(Text, nullable=False, default="")
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    next_run_at = Column(DateTime(timezone=True), nullable=False, index=True)
    error_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)
    output_artifact_ids_json = Column(JSON, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "task_attempt_id": self.task_attempt_id,
            "analyzer_type": self.analyzer_type,
            "analyzer_version": self.analyzer_version,
            "input_checksum": self.input_checksum,
            "input_artifact_ids": self.input_artifact_ids_json or [],
            "status": self.status,
            "status_reason": self.status_reason or "",
            "lease_owner": self.lease_owner,
            "lease_expires_at": self.lease_expires_at,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "next_run_at": self.next_run_at,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "output_artifact_ids": self.output_artifact_ids_json or [],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class OutboxMessageModel(Base):
    """General transactional outbox for Task/Event/Dispatch publication.

    Domain writes enqueue a message in the same transaction (guide §9.6); a
    dispatcher claims unpublished rows with a lease, publishes idempotently,
    then acks. Failures back off and dead-letter after a bounded attempt count.
    """

    __tablename__ = "outbox_messages"

    id = Column(String(128), primary_key=True)
    aggregate_type = Column(String(64), nullable=False)
    aggregate_id = Column(String(128), nullable=False)
    event_type = Column(String(64), nullable=False)
    payload_json = Column(JSON, nullable=False, default=dict)
    status = Column(String(32), nullable=False, default="PENDING")
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=False)
    worker_lease_owner = Column(String(128), nullable=True)
    worker_lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class ScheduleModel(Base):
    """Immutable cron task template; the scheduler materializes tasks from it."""

    __tablename__ = "schedules"

    id = Column(String(128), primary_key=True)
    name = Column(String(256), nullable=False)
    cron_expression = Column(String(64), nullable=False)
    timezone = Column(String(64), nullable=False)
    task_template_json = Column(JSON, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=True)
    next_run_at = Column(DateTime(timezone=True), nullable=False)
    created_by = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class ScheduleRecordModel(Base):
    """One scheduler firing: which slot produced which task (dedup key)."""

    __tablename__ = "schedule_records"
    __table_args__ = (
        UniqueConstraint(
            "schedule_id",
            "scheduled_at",
            name="uq_schedule_record_slot",
        ),
    )

    id = Column(String(128), primary_key=True)
    schedule_id = Column(String(128), nullable=False, index=True)
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    task_id = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


class CompositeTaskModel(Base):
    """A parent task that aggregates child task outcomes by strategy."""

    __tablename__ = "composite_tasks"

    id = Column(String(128), primary_key=True)
    name = Column(String(256), nullable=False)
    strategy = Column(String(32), nullable=False)  # ALL_REQUIRED / BEST_EFFORT / QUORUM
    required_success_count = Column(Integer, nullable=True)
    status = Column(String(32), nullable=False)
    created_by = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class CompositeTaskItemModel(Base):
    """One child task of a composite, with its role and observed status."""

    __tablename__ = "composite_task_items"

    id = Column(String(128), primary_key=True)
    composite_id = Column(String(128), nullable=False, index=True)
    task_id = Column(String(128), nullable=True)
    role = Column(String(16), nullable=False)  # required / optional
    sort_order = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


class FixVerificationModel(Base):
    """Before/after fix verification: apply fix -> re-test -> VERIFIED/REJECTED."""

    __tablename__ = "fix_verifications"

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(String(128), nullable=False, index=True)
    fix_summary = Column(Text, nullable=True)
    before_task_id = Column(String(128), nullable=False)
    after_task_id = Column(String(128), nullable=False)
    outcome = Column(String(32), nullable=False)
    before_hotspot_json = Column(JSON, nullable=True)
    after_hotspot_json = Column(JSON, nullable=True)
    comparison_json = Column(JSON, nullable=True)
    created_by = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


# ── 智能归因 ───────────────────────────────────────────────────


class DiagnosisRunModel(Base):
    __tablename__ = "diagnosis_runs"

    id = Column(String(128), primary_key=True)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    status = Column(String(32), nullable=False)
    model_name = Column(String(64), nullable=False)
    summary = Column(Text, default="")
    validated = Column(Integer, default=0)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "status": self.status,
            "model_name": self.model_name,
            "summary": self.summary or "",
            "validated": bool(self.validated),
            "retry_count": self.retry_count,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class DiagnosisToolResultModel(Base):
    __tablename__ = "diagnosis_tool_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    diagnosis_id = Column(String(128), ForeignKey("diagnosis_runs.id"), nullable=False, index=True)
    tool_name = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    evidence_ref = Column(String(128), nullable=False)
    input_json = Column(JSON, default=dict)
    output_json = Column(JSON, default=dict)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "status": self.status,
            "evidence_ref": self.evidence_ref,
            "input": self.input_json or {},
            "output": self.output_json or {},
            "error_message": self.error_message,
            "created_at": self.created_at,
        }


class DiagnosisReportModel(Base):
    __tablename__ = "diagnosis_reports"

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(String(128), ForeignKey("diagnosis_runs.id"), nullable=False, index=True)
    report_json = Column(JSON, default=dict)
    ranked_causes_json = Column(JSON, default=list)
    confidence = Column(Integer, default=0)
    not_enough_evidence = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "report": self.report_json or {},
            "ranked_causes": self.ranked_causes_json or [],
            "confidence": (self.confidence or 0) / 1000,
            "not_enough_evidence": bool(self.not_enough_evidence),
            "created_at": self.created_at,
        }


class RepairPlanModel(Base):
    __tablename__ = "repair_plans"

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(String(128), ForeignKey("diagnosis_runs.id"), nullable=False, index=True)
    cause_id = Column(String(128), nullable=False)
    risk_level = Column(String(32), nullable=False)
    actions_json = Column(JSON, default=list)
    executed_actions_json = Column(JSON, default=list)
    requires_user_confirm = Column(Integer, default=1)
    status = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "cause_id": self.cause_id,
            "risk_level": self.risk_level,
            "actions": self.actions_json or [],
            "executed_actions": self.executed_actions_json or [],
            "requires_user_confirm": bool(self.requires_user_confirm),
            "status": self.status,
            "created_at": self.created_at,
        }


class RCAFeedbackModel(Base):
    __tablename__ = "rca_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    diagnosis_id = Column(String(128), ForeignKey("diagnosis_runs.id"), nullable=False, index=True)
    task_id = Column(String(128), nullable=False, index=True)
    predicted_cause_id = Column(String(128), nullable=False)
    feedback_label = Column(String(32), nullable=False)
    corrected_cause_id = Column(String(128), nullable=True)
    feedback_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


class RCAFeedbackWeightModel(Base):
    __tablename__ = "rca_feedback_weights"

    candidate_id = Column(String(128), primary_key=True)
    positive_count = Column(Integer, default=0)
    negative_count = Column(Integer, default=0)
    partial_count = Column(Integer, default=0)
    weight_delta = Column(Integer, default=0)
    updated_at = Column(DateTime(timezone=True), nullable=False)


# ── Agent 指标快照 ───────────────────────────────────────────────


class AgentMetricSnapshotModel(Base):
    """Agent 周期性资源开销快照，用于趋势分析和容量规划。"""

    __tablename__ = "agent_metric_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(String(128), ForeignKey("agents.id"), nullable=False, index=True)
    cpu_percent = Column(Integer, default=0)
    rss_mb = Column(Integer, default=0)
    read_kb_s = Column(Integer, default=0)
    write_kb_s = Column(Integer, default=0)
    children_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "cpu_percent": self.cpu_percent,
            "rss_mb": self.rss_mb,
            "read_kb_s": self.read_kb_s,
            "write_kb_s": self.write_kb_s,
            "children_count": self.children_count,
            "created_at": self.created_at,
        }


# ── AI 集群诊断控制层 ────────────────────────────────────────────


class ContinuousDiagnosisTriggerModel(Base):
    """Idempotency record for profiler anomaly -> AI diagnosis promotion."""

    __tablename__ = "continuous_diagnosis_triggers"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_continuous_diagnosis_trigger_task"),
    )

    id = Column(String(128), primary_key=True)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    artifact_id = Column(Integer, ForeignKey("artifacts.id"), nullable=False)
    detector_version = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, index=True)
    score_json = Column(JSON, default=dict)
    diagnosis_id = Column(
        String(128), ForeignKey("diagnosis_sessions.id"), nullable=True, index=True,
    )
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "trigger_id": self.id,
            "task_id": self.task_id,
            "artifact_id": self.artifact_id,
            "detector_version": self.detector_version,
            "status": self.status,
            "score": self.score_json or {},
            "diagnosis_id": self.diagnosis_id,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class TopologySnapshotModel(Base):
    """诊断创建时冻结的服务/实例/宿主机拓扑。"""

    __tablename__ = "topology_snapshots"

    id = Column(String(128), primary_key=True)
    effective_at = Column(DateTime(timezone=True), nullable=False)
    generated_at = Column(DateTime(timezone=True), nullable=False)
    nodes_json = Column(JSON, default=list)
    edges_json = Column(JSON, default=list)
    source_versions_json = Column(JSON, default=dict)
    confidence_summary_json = Column(JSON, default=dict)

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.id,
            "effective_at": self.effective_at,
            "generated_at": self.generated_at,
            "nodes": self.nodes_json or [],
            "edges": self.edges_json or [],
            "source_versions": self.source_versions_json or {},
            "confidence_summary": self.confidence_summary_json or {},
        }


class DiagnosisSessionModel(Base):
    """独立于单个采集 Task 的、可恢复的诊断工作流。"""

    __tablename__ = "diagnosis_sessions"

    id = Column(String(128), primary_key=True)
    creator_id = Column(String(128), nullable=False)
    raw_query = Column(Text, nullable=False)
    normalized_intent_json = Column(JSON, default=dict)
    target_scope_json = Column(JSON, default=dict)
    requested_time_range_json = Column(JSON, default=dict)
    effective_time_range_json = Column(JSON, default=dict)
    topology_snapshot_id = Column(
        String(128), ForeignKey("topology_snapshots.id"), nullable=True, index=True,
    )
    baseline_snapshot_id = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False)
    policy_profile = Column(String(64), nullable=False)
    risk_budget_json = Column(JSON, default=dict)
    resource_budget_json = Column(JSON, default=dict)
    budget_used_json = Column(JSON, default=dict)
    hypothesis_graph_json = Column(JSON, default=dict)
    evaluation_oracle_json = Column(JSON, default=dict)
    child_task_ids_json = Column(JSON, default=list)
    conclusion_versions_json = Column(JSON, default=list)
    model_version = Column(String(128), nullable=False)
    planner_version = Column(String(64), nullable=False)
    lease_owner = Column(String(128), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    row_version = Column(Integer, nullable=False, default=0)
    deadline_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "diagnosis_id": self.id,
            "creator_id": self.creator_id,
            "raw_query": self.raw_query,
            "normalized_intent": self.normalized_intent_json or {},
            "target_scope": self.target_scope_json or {},
            "requested_time_range": self.requested_time_range_json or {},
            "effective_time_range": self.effective_time_range_json or {},
            "topology_snapshot_id": self.topology_snapshot_id,
            "baseline_snapshot_id": self.baseline_snapshot_id,
            "status": self.status,
            "policy_profile": self.policy_profile,
            "risk_budget": self.risk_budget_json or {},
            "resource_budget": self.resource_budget_json or {},
            "budget_used": self.budget_used_json or {},
            "hypothesis_graph": self.hypothesis_graph_json or {},
            "evaluation_oracle": self.evaluation_oracle_json or {},
            "child_task_ids": self.child_task_ids_json or [],
            "conclusion_versions": self.conclusion_versions_json or [],
            "model_version": self.model_version,
            "planner_version": self.planner_version,
            "lease_owner": self.lease_owner,
            "lease_until": self.lease_until,
            "row_version": self.row_version,
            "deadline_at": self.deadline_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class DiagnosisEventModel(Base):
    __tablename__ = "diagnosis_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    diagnosis_id = Column(
        String(128), ForeignKey("diagnosis_sessions.id"), nullable=False, index=True,
    )
    event_type = Column(String(64), nullable=False)
    from_status = Column(String(32), nullable=True)
    to_status = Column(String(32), nullable=False)
    payload_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "event_type": self.event_type,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "payload": self.payload_json or {},
            "created_at": self.created_at,
        }


class ProbeExecutionModel(Base):
    """一次受控探针计划/审批/执行记录；step id 同时作为幂等键。"""

    __tablename__ = "diagnosis_probe_executions"

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(
        String(128), ForeignKey("diagnosis_sessions.id"), nullable=False, index=True,
    )
    probe_id = Column(String(128), nullable=False)
    target_json = Column(JSON, default=dict)
    parameters_json = Column(JSON, default=dict)
    reason = Column(Text, nullable=False)
    risk_level = Column(String(8), nullable=False)
    status = Column(String(32), nullable=False)
    requires_approval = Column(Integer, default=0)
    evidence_purpose = Column(String(16), nullable=False, default="VERIFY")
    round_index = Column(Integer, nullable=False, default=1)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=True, index=True)
    approved_by = Column(String(128), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    retry_count = Column(Integer, nullable=False, default=0)
    error_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "step_id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "probe_id": self.probe_id,
            "target": self.target_json or {},
            "parameters": self.parameters_json or {},
            "reason": self.reason,
            "risk_level": self.risk_level,
            "status": self.status,
            "requires_approval": bool(self.requires_approval),
            "evidence_purpose": self.evidence_purpose or "VERIFY",
            "round_index": self.round_index or 1,
            "task_id": self.task_id,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "retry_count": self.retry_count,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


class DiagnosisOutboxModel(Base):
    """Transactional intent to create the one Task belonging to a probe step."""

    __tablename__ = "diagnosis_task_outbox"

    id = Column(String(160), primary_key=True)
    diagnosis_id = Column(String(128), ForeignKey("diagnosis_sessions.id"), nullable=False, index=True)
    step_id = Column(String(128), ForeignKey("diagnosis_probe_executions.id"), nullable=False, unique=True)
    status = Column(String(32), nullable=False, default="PENDING")
    attempt = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class DiagnosisEvidenceModel(Base):
    """可追溯到 Task/Artifact 的不可变证据摘要。"""

    __tablename__ = "diagnosis_evidence"

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(
        String(128), ForeignKey("diagnosis_sessions.id"), nullable=False, index=True,
    )
    source_type = Column(String(32), nullable=False)
    source_system = Column(String(64), nullable=False)
    evidence_role = Column(String(32), nullable=False, default="incident")
    target_json = Column(JSON, default=dict)
    event_time_range_json = Column(JSON, default=dict)
    ingestion_time = Column(DateTime(timezone=True), nullable=False)
    query_or_probe = Column(String(256), nullable=False)
    raw_artifact_ref = Column(String(512), nullable=True)
    derived_artifact_ref = Column(String(512), nullable=True)
    derivation_version = Column(String(64), nullable=False)
    observed_value_json = Column(JSON, default=dict)
    baseline_value_json = Column(JSON, default=dict)
    anomaly_score_json = Column(JSON, default=dict)
    data_quality_json = Column(JSON, default=dict)
    integrity_hash = Column(String(80), nullable=False)
    claim_links_json = Column(JSON, default=list)

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "source_type": self.source_type,
            "source_system": self.source_system,
            "evidence_role": self.evidence_role,
            "target": self.target_json or {},
            "event_time_range": self.event_time_range_json or {},
            "ingestion_time": self.ingestion_time,
            "query_or_probe": self.query_or_probe,
            "raw_artifact_ref": self.raw_artifact_ref,
            "derived_artifact_ref": self.derived_artifact_ref,
            "derivation_version": self.derivation_version,
            "observed_value": self.observed_value_json or {},
            "baseline_value": self.baseline_value_json or {},
            "anomaly_score": self.anomaly_score_json or {},
            "data_quality": self.data_quality_json or {},
            "integrity_hash": self.integrity_hash,
            "claim_links": self.claim_links_json or [],
        }


class DiagnosisEvidenceSnapshotModel(Base):
    """一次采集轮次形成的不可变证据集合。

    Snapshot 只保存证据引用和采集上下文，不复制原始采集结果。这样既能
    追溯 incident/baseline/peer/verification 的时间窗口，又不会出现两份
    原始数据相互漂移。
    """

    __tablename__ = "diagnosis_evidence_snapshots"

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(
        String(128), ForeignKey("diagnosis_sessions.id"), nullable=False, index=True,
    )
    round_index = Column(Integer, nullable=False, default=1)
    evidence_role = Column(String(32), nullable=False, default="incident")
    captured_at = Column(DateTime(timezone=True), nullable=False)
    time_range_json = Column(JSON, default=dict)
    target_json = Column(JSON, default=dict)
    workload_identity_json = Column(JSON, default=dict)
    deployment_version = Column(String(128), nullable=True)
    host_fingerprint_json = Column(JSON, default=dict)
    collector = Column(String(64), nullable=False)
    collector_version = Column(String(64), nullable=True)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=True, index=True)
    attempt_id = Column(String(128), nullable=True)
    evidence_refs_json = Column(JSON, default=list)
    artifact_refs_json = Column(JSON, default=list)
    baseline_ref = Column(String(128), nullable=True)
    quality_json = Column(JSON, default=dict)
    integrity_hash = Column(String(80), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "round_index": self.round_index,
            "evidence_role": self.evidence_role,
            "captured_at": self.captured_at,
            "time_range": self.time_range_json or {},
            "target": self.target_json or {},
            "workload_identity": self.workload_identity_json or {},
            "deployment_version": self.deployment_version,
            "host_fingerprint": self.host_fingerprint_json or {},
            "collector": self.collector,
            "collector_version": self.collector_version,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "evidence_refs": self.evidence_refs_json or [],
            "artifact_refs": self.artifact_refs_json or [],
            "baseline_ref": self.baseline_ref,
            "quality": self.quality_json or {},
            "integrity_hash": self.integrity_hash,
            "created_at": self.created_at,
        }


class DiagnosisNodeRunModel(Base):
    """显式诊断流水线节点的可恢复运行记录。"""

    __tablename__ = "diagnosis_node_runs"
    __table_args__ = (UniqueConstraint("diagnosis_id", "node_name", name="uq_diagnosis_node_name"),)

    id = Column(String(256), primary_key=True)
    diagnosis_id = Column(
        String(128), ForeignKey("diagnosis_sessions.id"), nullable=False, index=True,
    )
    node_name = Column(String(64), nullable=False, index=True)
    sequence = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False)
    attempt = Column(Integer, nullable=False, default=0)
    input_refs_json = Column(JSON, default=list)
    output_refs_json = Column(JSON, default=list)
    metrics_json = Column(JSON, default=dict)
    error_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)
    implementation_version = Column(String(64), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "node_run_id": self.id,
            "diagnosis_id": self.diagnosis_id,
            "node_name": self.node_name,
            "sequence": self.sequence,
            "status": self.status,
            "attempt": self.attempt,
            "input_refs": self.input_refs_json or [],
            "output_refs": self.output_refs_json or [],
            "metrics": self.metrics_json or {},
            "error_code": self.error_code,
            "error_message": self.error_message,
            "implementation_version": self.implementation_version,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "updated_at": self.updated_at,
        }
