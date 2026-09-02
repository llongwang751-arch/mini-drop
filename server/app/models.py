"""SQLAlchemy ORM 模型定义。

与 InMemoryRepository 的数据类结构对齐，
通过 SQLAlchemy 2.0 DeclarativeBase 映射。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
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
    # Host compatibility reports include distribution, kernel features and
    # collector availability.  TLinux capability reports legitimately exceed
    # the old 256-byte limit, so keep the payload as text instead of silently
    # truncating it or rejecting Agent registration.
    os_info = Column(Text, default="unknown")
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

class ProcessCandidateSnapshotModel(Base):
    """Append-only server receipt of one present process snapshot payload."""

    __tablename__ = "process_candidate_snapshots"
    __table_args__ = (
        CheckConstraint(
            "state IN ('complete-empty', 'complete-populated', 'partial', "
            "'failed', 'truncated')",
            name="ck_process_snapshot_state",
        ),
        CheckConstraint(
            "generation >= 0", name="ck_process_snapshot_generation"
        ),
        Index(
            "ix_process_snapshots_agent_received",
            "agent_id",
            "received_at",
            "id",
        ),
        UniqueConstraint(
            "agent_id", "id", name="uq_process_snapshot_agent_id"
        ),
    )

    id = Column(String(128), primary_key=True)
    agent_id = Column(
        String(128), ForeignKey("agents.id"), nullable=False
    )
    generation = Column(BigInteger, nullable=False)
    boot_id = Column(String(1024), nullable=False, default="")
    observed_at_unix_ms = Column(BigInteger, nullable=False, default=0)
    complete = Column(Boolean, nullable=False, default=False)
    truncated = Column(Boolean, nullable=False, default=False)
    error = Column(String(1024), nullable=False, default="")
    state = Column(String(32), nullable=False)
    authoritative = Column(Boolean, nullable=False, default=False)
    received_at = Column(DateTime(timezone=True), nullable=False)


class ProcessCandidateModel(Base):
    """Bounded candidate identity belonging to one immutable snapshot."""

    __tablename__ = "process_candidates"
    __table_args__ = (
        CheckConstraint("pid > 0", name="ck_process_candidate_pid"),
        CheckConstraint(
            "process_start_ticks > 0", name="ck_process_candidate_start_ticks"
        ),
        CheckConstraint(
            "pid_namespace_inode > 0", name="ck_process_candidate_namespace_inode"
        ),
        CheckConstraint(
            "namespace_pid > 0", name="ck_process_candidate_namespace_pid"
        ),
        UniqueConstraint(
            "snapshot_id",
            "pid",
            "process_start_ticks",
            "pid_namespace_inode",
            "namespace_pid",
            "executable_identity",
            name="uq_process_candidate_identity",
        ),
        Index("ix_process_candidates_snapshot_pid", "snapshot_id", "pid"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_id = Column(
        String(128),
        ForeignKey("process_candidate_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id = Column(String(128), ForeignKey("agents.id"), nullable=False)
    pid = Column(Integer, nullable=False)
    process_start_ticks = Column(BigInteger, nullable=False)
    pid_namespace_inode = Column(BigInteger, nullable=False)
    namespace_pid = Column(Integer, nullable=False)
    executable_identity = Column(String(1024), nullable=False)
    comm = Column(String(1024), nullable=False, default="")
    cgroup = Column(String(1024), nullable=False, default="")
    service_hint = Column(String(1024), nullable=False, default="")
    instance_hint = Column(String(1024), nullable=False, default="")
    collector_capabilities = Column(JSON, nullable=False, default=list)


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
    error_code = Column(String(64), nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    collection_status = Column(String(16), nullable=False, default="QUEUED")
    analysis_status = Column(String(16), nullable=False, default="PENDING")
    request_params = Column(JSON, default=dict)
    process_snapshot_id = Column(
        String(128),
        ForeignKey("process_candidate_snapshots.id"),
        nullable=True,
        index=True,
    )
    process_binding_json = Column(JSON, nullable=True)
    diagnosis_step_id = Column(String(128), nullable=True, unique=True, index=True)
    idempotency_key = Column(String(128), nullable=True)
    creator_id = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
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
            "error_code": self.error_code,
            "error_message": self.error_message,
            "collection_status": self.collection_status or "QUEUED",
            "analysis_status": self.analysis_status or "PENDING",
            "request_params": self.request_params or {},
            "process_snapshot_id": self.process_snapshot_id,
            "process_binding": self.process_binding_json,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
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
        UniqueConstraint("id", "task_id", name="uq_task_attempt_identity"),
    )

    id = Column(String(128), primary_key=True)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    attempt_no = Column(Integer, nullable=False)
    task_attempt_authority_sha256 = Column(String(64), nullable=True, unique=True)
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


class TaskUploadAuthorizationModel(Base):
    """Short-lived, task-attempt-scoped MinIO upload authorization."""

    __tablename__ = "task_upload_authorizations"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "task_attempt_id",
            "object_key",
            name="uq_task_upload_authorization_object",
        ),
        Index(
            "ix_task_upload_authorizations_task_expiry",
            "task_id",
            "task_attempt_id",
            "expires_at",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(
        String(128), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False,
    )
    task_attempt_id = Column(String(128), nullable=False)
    object_key = Column(String(512), nullable=False)
    put_url = Column(Text, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


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
        metadata = self.meta_json or {}
        return {
            "sequence": self.id,
            "task_id": self.task_id,
            "task_attempt_id": metadata.get("task_attempt_id"),
            "from_status": self.from_status,
            "to_status": self.to_status,
            "reason": self.reason,
            "actor": self.actor,
            "source": self.actor,
            "metadata": metadata,
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
    requested_time_range_json = Column(JSON, default=dict)
    effective_time_range_json = Column(JSON, default=dict)
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
            "requested_time_range": (
                self.requested_time_range_json or self.time_range_json or {}
            ),
            "effective_time_range": self.effective_time_range_json or {},
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


class DropInsightTargetDiscoveryModel(Base):
    """Durable, diagnosis-version-scoped process discovery authority."""

    __tablename__ = "drop_insight_target_discoveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('READY', 'AMBIGUOUS', 'EMPTY', 'STALE', "
            "'UNAVAILABLE', 'TRUNCATED', 'INVALIDATED')",
            name="ck_drop_insight_target_discovery_status",
        ),
        Index(
            "ix_drop_insight_target_discovery_scope",
            "diagnosis_id",
            "diagnosis_version",
            "created_at",
        ),
    )

    id = Column(String(128), primary_key=True)
    diagnosis_id = Column(
        String(128),
        ForeignKey("drop_insight_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    diagnosis_version = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False)
    service_filter = Column(String(128), nullable=True)
    environment_filter = Column(String(64), nullable=True)
    snapshot_state_json = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    invalidated_at = Column(DateTime(timezone=True), nullable=True)


class DropInsightTargetBindingModel(Base):
    """Opaque member of one persisted target-discovery result."""

    __tablename__ = "drop_insight_target_bindings"
    __table_args__ = (
        UniqueConstraint(
            "discovery_id", "id", name="uq_drop_insight_target_binding_membership"
        ),
        Index(
            "ix_drop_insight_target_bindings_discovery",
            "discovery_id",
            "id",
        ),
    )

    id = Column(String(128), primary_key=True)
    discovery_id = Column(
        String(128),
        ForeignKey("drop_insight_target_discoveries.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id = Column(String(128), ForeignKey("agents.id"), nullable=False)
    process_snapshot_id = Column(
        String(128), ForeignKey("process_candidate_snapshots.id"), nullable=False
    )
    pid = Column(Integer, nullable=False)
    process_binding_json = Column(JSON, nullable=False)
    display_json = Column(JSON, nullable=False, default=dict)


class DropInsightEventModel(Base):
    __tablename__ = "drop_insight_events"
    __table_args__ = (
        UniqueConstraint("diagnosis_id", "sequence", name="uq_drop_insight_event_sequence"),
        UniqueConstraint(
            "diagnosis_id",
            "effect_key",
            name="uq_drop_insight_event_effect",
        ),
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
    effect_key = Column(String(160), nullable=True)
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
    __table_args__ = (
        UniqueConstraint(
            "diagnosis_id",
            "effect_key",
            name="uq_drop_insight_hypothesis_effect",
        ),
    )

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
    effect_key = Column(String(160), nullable=True)
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
    __table_args__ = (
        UniqueConstraint(
            "diagnosis_id",
            "hypothesis_id",
            name="uq_drop_insight_report_identity",
        ),
        CheckConstraint(
            "effects_status IN ('PENDING', 'APPLYING', 'APPLIED')",
            name="ck_drop_insight_report_effects_status",
        ),
        CheckConstraint(
            "effects_phase IS NULL OR effects_phase IN "
            "('EXECUTION_STARTED', 'EFFECTS_COMPLETED')",
            name="ck_drop_insight_report_effects_phase",
        ),
        Index(
            "ix_drop_insight_reports_effects_lease",
            "effects_status",
            "effects_lease_expires_at",
        ),
    )

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
    effects_status = Column(String(32), nullable=False, default="PENDING")
    effects_phase = Column(String(32), nullable=True)
    effects_owner = Column(String(128), nullable=True)
    effects_lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    effects_fencing_token = Column(Integer, nullable=False, default=0)
    effects_applied_at = Column(DateTime(timezone=True), nullable=True)
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
            "effects_status": self.effects_status,
            "effects_phase": self.effects_phase,
            "effects_owner": self.effects_owner,
            "effects_lease_expires_at": self.effects_lease_expires_at,
            "effects_fencing_token": self.effects_fencing_token,
            "effects_applied_at": self.effects_applied_at,
            "created_at": self.created_at,
        }


class DropInsightToolCallModel(Base):
    __tablename__ = "drop_insight_tool_calls"
    __table_args__ = (
        UniqueConstraint(
            "diagnosis_id",
            "effect_key",
            name="uq_drop_insight_tool_call_effect",
        ),
    )

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
    terminal_processing_status = Column(String(32), nullable=False, default="NONE")
    terminal_processed_at = Column(DateTime(timezone=True), nullable=True)
    effect_key = Column(String(160), nullable=True)
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
            "terminal_processing_status": self.terminal_processing_status,
            "terminal_processed_at": self.terminal_processed_at,
            "requested_by": self.requested_by,
            "approved_by": self.approved_by,
            "approval_reason": self.approval_reason,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "executed_at": self.executed_at,
        }


class DiagnosticSkillModel(Base):
    """A versioned, evidence-gated diagnostic strategy learned from incidents."""

    __tablename__ = "diagnostic_skills"
    __table_args__ = (
        UniqueConstraint("family_key", "version", name="uq_diagnostic_skill_version"),
        CheckConstraint(
            "status IN ('CANDIDATE', 'ACTIVE', 'QUARANTINED', 'RETIRED')",
            name="ck_diagnostic_skill_status",
        ),
    )

    id = Column(String(128), primary_key=True)
    family_key = Column(String(256), nullable=False, index=True)
    category = Column(String(64), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="CANDIDATE", index=True)
    source_diagnosis_ids_json = Column(JSON, default=list)
    trigger_json = Column(JSON, default=dict)
    strategy_json = Column(JSON, default=dict)
    gate_metrics_json = Column(JSON, default=dict)
    parent_skill_id = Column(String(128), nullable=True, index=True)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=True)

    def to_dict(self) -> dict:
        return {
            "skill_id": self.id,
            "family_key": self.family_key,
            "category": self.category,
            "version": self.version,
            "status": self.status,
            "source_diagnosis_ids": self.source_diagnosis_ids_json or [],
            "trigger": self.trigger_json or {},
            "strategy": self.strategy_json or {},
            "gate_metrics": self.gate_metrics_json or {},
            "parent_skill_id": self.parent_skill_id,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "published_at": self.published_at,
        }


class DiagnosticSkillEvaluationModel(Base):
    __tablename__ = "diagnostic_skill_evaluations"

    id = Column(String(128), primary_key=True)
    skill_id = Column(
        String(128), ForeignKey("diagnostic_skills.id"), nullable=False, index=True
    )
    case_kind = Column(String(32), nullable=False)
    diagnosis_id = Column(String(128), nullable=True, index=True)
    passed = Column(Boolean, nullable=False)
    score = Column(Integer, nullable=False)
    details_json = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "evaluation_id": self.id,
            "skill_id": self.skill_id,
            "case_kind": self.case_kind,
            "diagnosis_id": self.diagnosis_id,
            "passed": self.passed,
            "score": self.score / 1000,
            "details": self.details_json or {},
            "created_at": self.created_at,
        }


class DiagnosticSkillActivationModel(Base):
    __tablename__ = "diagnostic_skill_activations"
    __table_args__ = (
        UniqueConstraint(
            "diagnosis_id",
            "skill_id",
            name="uq_diagnostic_skill_activation_diagnosis_skill",
        ),
    )

    id = Column(String(128), primary_key=True)
    skill_id = Column(
        String(128), ForeignKey("diagnostic_skills.id"), nullable=False, index=True
    )
    diagnosis_id = Column(
        String(128), ForeignKey("drop_insight_sessions.id"), nullable=False, index=True
    )
    match_score = Column(Integer, nullable=False)
    match_reason_json = Column(JSON, default=dict)
    baseline_tool = Column(String(128), nullable=False)
    selected_tool = Column(String(128), nullable=False)
    outcome = Column(String(32), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    def to_dict(self) -> dict:
        return {
            "activation_id": self.id,
            "skill_id": self.skill_id,
            "diagnosis_id": self.diagnosis_id,
            "match_score": self.match_score / 1000,
            "match_reason": self.match_reason_json or {},
            "baseline_tool": self.baseline_tool,
            "selected_tool": self.selected_tool,
            "outcome": self.outcome,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ArtifactModel(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint(
            "id", "task_id", "task_attempt_id",
            name="uq_artifact_attempt_identity",
        ),
        ForeignKeyConstraint(
            ["task_attempt_id", "task_id"],
            ["task_attempts.id", "task_attempts.task_id"],
            name="fk_artifacts_task_attempt_identity",
        ),
        ForeignKeyConstraint(
            ["analysis_job_id", "task_id", "task_attempt_id"],
            ["analysis_jobs.id", "analysis_jobs.task_id", "analysis_jobs.task_attempt_id"],
            name="fk_artifacts_analysis_job_identity",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    task_attempt_id = Column(String(128), nullable=True, index=True)
    analysis_job_id = Column(String(128), nullable=True, index=True)
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
            "task_attempt_id": self.task_attempt_id,
            "analysis_job_id": self.analysis_job_id,
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
        UniqueConstraint(
            "id", "task_id", "task_attempt_id",
            name="uq_analysis_job_attempt_identity",
        ),
        ForeignKeyConstraint(
            ["task_attempt_id", "task_id"],
            ["task_attempts.id", "task_attempts.task_id"],
            name="fk_analysis_jobs_task_attempt_identity",
        ),
    )

    id = Column(String(128), primary_key=True)
    task_id = Column(String(128), ForeignKey("tasks.id"), nullable=False, index=True)
    task_attempt_id = Column(String(128), nullable=True, index=True)
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


class AnalysisJobInputArtifactModel(Base):
    __tablename__ = "analysis_job_input_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["analysis_job_id", "task_id", "task_attempt_id"],
            ["analysis_jobs.id", "analysis_jobs.task_id", "analysis_jobs.task_attempt_id"],
            name="fk_analysis_job_inputs_job_identity",
        ),
        ForeignKeyConstraint(
            ["artifact_id", "task_id", "task_attempt_id"],
            ["artifacts.id", "artifacts.task_id", "artifacts.task_attempt_id"],
            name="fk_analysis_job_inputs_artifact_identity",
        ),
        UniqueConstraint(
            "analysis_job_id", "artifact_id",
            name="uq_analysis_job_input_artifact",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    analysis_job_id = Column(String(128), nullable=False, index=True)
    artifact_id = Column(Integer, nullable=False, index=True)
    task_id = Column(String(128), nullable=False)
    task_attempt_id = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


class AnalysisJobOutputArtifactModel(Base):
    __tablename__ = "analysis_job_output_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["analysis_job_id", "task_id", "task_attempt_id"],
            ["analysis_jobs.id", "analysis_jobs.task_id", "analysis_jobs.task_attempt_id"],
            name="fk_analysis_job_outputs_job_identity",
        ),
        ForeignKeyConstraint(
            ["artifact_id", "task_id", "task_attempt_id"],
            ["artifacts.id", "artifacts.task_id", "artifacts.task_attempt_id"],
            name="fk_analysis_job_outputs_artifact_identity",
        ),
        UniqueConstraint(
            "analysis_job_id", "artifact_id",
            name="uq_analysis_job_output_artifact",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    analysis_job_id = Column(String(128), nullable=False, index=True)
    artifact_id = Column(Integer, nullable=False, index=True)
    task_id = Column(String(128), nullable=False)
    task_attempt_id = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)


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
