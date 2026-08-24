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
from datetime import datetime, timedelta, timezone
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
    ProcessCandidateModel,
    ProcessCandidateSnapshotModel,
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
from server.app.process_attestation import (
    PROCESS_SNAPSHOT_MAX_AGE,
    ProcessCandidateInput,
    ProcessIdentityBinding,
    ProcessSnapshotState,
    ResolvedProcessCandidate,
    ResolvedProcessSnapshot,
    binding_matches_candidate,
    normalize_process_candidate_snapshot,
)
from server.app.state_machine import (
    AnalysisStatus,
    Actor,
    CollectionStatus,
    StatusEvent,
    TaskStatus,
    build_status_event,
    now_utc,
)
from server.app.task_attempt_authority import (
    TaskDispatch,
    generate_task_attempt_authority,
    task_attempt_authority_sha256,
)


def _coerce_process_binding(value: Any) -> ProcessIdentityBinding:
    if isinstance(value, ProcessIdentityBinding):
        return value
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return ProcessIdentityBinding.from_mapping(value)


def _absent_process_snapshot(agent_id: str) -> ResolvedProcessSnapshot:
    return ResolvedProcessSnapshot(
        agent_id=agent_id,
        snapshot_id=None,
        generation=None,
        received_at=None,
        observed_at_unix_ms=None,
        boot_id="",
        state=ProcessSnapshotState.ABSENT,
        authoritative=False,
    )


class AgentMixin:
    def register_agent(
        self, agent_id: str, hostname: str, ip_addr: str,
        version: str = "0.1.0", os_info: str = "unknown",
        capabilities: list[str] | None = None,
    ) -> AgentModel:
        caps = list(capabilities or [])
        ts = now_utc()

        with self._write_session() as session:
            existing = session.get(AgentModel, agent_id)
            if existing is not None and existing.status == "OFFLINE":
                self._write_audit(
                    session, "AGENT_ONLINE", agent_id,
                    f"{agent_id} 恢复在线",
                )

            if existing is not None:
                existing.hostname = hostname
                existing.ip_addr = ip_addr
                existing.version = version
                existing.os_info = os_info
                existing.capabilities = caps
                existing.status = "ONLINE"
                existing.last_heartbeat_at = ts
                existing.updated_at = ts
                agent = existing
            else:
                agent = AgentModel(
                    id=agent_id, hostname=hostname, ip_addr=ip_addr,
                    version=version, os_info=os_info, capabilities=caps,
                    status="ONLINE", last_heartbeat_at=ts,
                    created_at=ts, updated_at=ts,
                )
                session.add(agent)

            # 保持与 InMemoryRepository 接口一致（SqlRepository.heartbeat 直接查 DB，不使用此队列）
            if ip_addr not in self._task_queues:
                self._task_queues[ip_addr] = deque()

            notify_agent_status(agent_id, "ONLINE", ip_addr)
            return agent

    def heartbeat(
        self,
        agent_id: str,
        ip_addr: str,
        *,
        received_at: datetime | None = None,
    ) -> TaskDispatch | None:
        with self._write_session() as session:
            agent = session.get(AgentModel, agent_id)
            if agent is None:
                return None

            timestamp = received_at or now_utc()
            agent.ip_addr = ip_addr or agent.ip_addr
            agent.status = "ONLINE"
            agent.last_heartbeat_at = timestamp
            agent.updated_at = timestamp

            query = (
                session.query(TaskModel)
                .filter(
                    TaskModel.agent_id == agent_id,
                    TaskModel.status == TaskStatus.PENDING.value,
                    TaskModel.deleted_at.is_(None),
                )
                .order_by(TaskModel.created_at.asc(), TaskModel.id.asc())
            )
            if session.bind is not None and session.bind.dialect.name != "sqlite":
                query = query.with_for_update(skip_locked=True)
            for task in query.all():
                if task.process_binding_json is not None and not self._validate_process_binding_in_session(
                    session,
                    task.process_binding_json,
                    agent_id=agent_id,
                    target_pid=task.target_pid,
                    now=timestamp,
                ):
                    continue
                authority = generate_task_attempt_authority()
                attempt = self._transition_task_in_session(
                    session, task.id, TaskStatus.RUNNING,
                    "Agent 心跳拉取待执行任务", Actor.SERVER,
                    task_attempt_authority_sha256=task_attempt_authority_sha256(authority),
                )
                task.status = TaskStatus.RUNNING.value
                assert attempt is not None
                return TaskDispatch(
                    task=task,
                    task_attempt_id=attempt.id,
                    task_attempt_authority=authority,
                )
            return None

    def heartbeat_only(
        self,
        agent_id: str,
        ip_addr: str,
        *,
        received_at: datetime | None = None,
    ) -> None:
        """Update heartbeat timestamp without dispatching a new task."""
        with self._write_session() as session:
            agent = session.get(AgentModel, agent_id)
            if agent is None:
                return
            timestamp = received_at or now_utc()
            agent.ip_addr = ip_addr or agent.ip_addr
            agent.status = "ONLINE"
            agent.last_heartbeat_at = timestamp
            agent.updated_at = timestamp

    def record_process_candidate_snapshot(
        self,
        agent_id: str,
        snapshot: Any | None,
        *,
        received_at: datetime | None = None,
    ) -> ResolvedProcessSnapshot:
        normalized = normalize_process_candidate_snapshot(snapshot)
        if normalized.state == ProcessSnapshotState.ABSENT:
            return _absent_process_snapshot(agent_id)
        payload = normalized.snapshot
        assert payload is not None
        timestamp = received_at or now_utc()
        snapshot_id = f"psnap_{uuid4().hex}"
        with self._write_session() as session:
            if session.get(AgentModel, agent_id) is None:
                raise ValueError(f"Agent {agent_id} 不存在")
            row = ProcessCandidateSnapshotModel(
                id=snapshot_id,
                agent_id=agent_id,
                generation=payload.generation,
                boot_id=payload.boot_id,
                observed_at_unix_ms=payload.observed_at_unix_ms,
                complete=payload.complete,
                truncated=payload.truncated,
                error=payload.error,
                state=normalized.state.value,
                authoritative=normalized.authoritative,
                received_at=timestamp,
            )
            session.add(row)
            for candidate in payload.candidates:
                if candidate.pid <= 0 or candidate.process_start_ticks <= 0 or candidate.pid_namespace_inode <= 0 or candidate.namespace_pid <= 0:
                    continue
                session.add(ProcessCandidateModel(
                    snapshot_id=snapshot_id,
                    agent_id=agent_id,
                    pid=candidate.pid,
                    process_start_ticks=candidate.process_start_ticks,
                    pid_namespace_inode=candidate.pid_namespace_inode,
                    namespace_pid=candidate.namespace_pid,
                    executable_identity=candidate.executable_identity,
                    comm=candidate.comm,
                    cgroup=candidate.cgroup,
                    service_hint=candidate.service_hint,
                    instance_hint=candidate.instance_hint,
                    collector_capabilities=list(candidate.collector_capabilities),
                ))
            session.flush()
            return self._resolved_process_snapshot_in_session(session, row)

    def get_process_candidate_snapshot(
        self, agent_id: str
    ) -> ResolvedProcessSnapshot:
        with self._read_session() as session:
            return self._latest_process_snapshot_in_session(session, agent_id)

    def resolve_process_candidates(
        self, agent_id: str, *, pid: int | None = None
    ) -> tuple[ResolvedProcessCandidate, ...]:
        snapshot = self.get_process_candidate_snapshot(agent_id)
        if not snapshot.authoritative:
            return ()
        return tuple(
            candidate for candidate in snapshot.candidates
            if pid is None or candidate.candidate.pid == pid
        )

    def resolve_process_candidate(
        self, agent_id: str, pid: int
    ) -> ResolvedProcessCandidate | None:
        candidates = self.resolve_process_candidates(agent_id, pid=pid)
        return candidates[0] if len(candidates) == 1 else None

    def validate_process_binding(
        self,
        binding: ProcessIdentityBinding | dict[str, Any] | Any,
        *,
        agent_id: str | None = None,
        target_pid: int | None = None,
        now: datetime | None = None,
    ) -> bool:
        with self._read_session() as session:
            return self._validate_process_binding_in_session(
                session,
                binding,
                agent_id=agent_id,
                target_pid=target_pid,
                now=now,
            )

    def _validate_process_binding_in_session(
        self,
        session: OrmSession,
        binding: ProcessIdentityBinding | dict[str, Any] | Any,
        *,
        agent_id: str | None = None,
        target_pid: int | None = None,
        now: datetime | None = None,
    ) -> bool:
        try:
            value = _coerce_process_binding(binding)
        except (KeyError, TypeError, ValueError):
            return False
        if agent_id is not None and value.agent_id != agent_id:
            return False
        if target_pid is not None and value.pid != target_pid:
            return False
        snapshot = self._latest_process_snapshot_in_session(session, value.agent_id)
        if not snapshot.authoritative or not snapshot.is_fresh(
            now=now, max_age=PROCESS_SNAPSHOT_MAX_AGE
        ):
            return False
        return any(
            binding_matches_candidate(value, candidate)
            for candidate in snapshot.candidates
        )

    def _latest_process_snapshot_in_session(
        self, session: OrmSession, agent_id: str
    ) -> ResolvedProcessSnapshot:
        row = (
            session.query(ProcessCandidateSnapshotModel)
            .filter(ProcessCandidateSnapshotModel.agent_id == agent_id)
            .order_by(
                ProcessCandidateSnapshotModel.received_at.desc(),
                ProcessCandidateSnapshotModel.id.desc(),
            )
            .first()
        )
        if row is None:
            return _absent_process_snapshot(agent_id)
        return self._resolved_process_snapshot_in_session(session, row)

    @staticmethod
    def _resolved_process_snapshot_in_session(
        session: OrmSession, row: ProcessCandidateSnapshotModel
    ) -> ResolvedProcessSnapshot:
        candidate_rows = (
            session.query(ProcessCandidateModel)
            .filter(ProcessCandidateModel.snapshot_id == row.id)
            .order_by(ProcessCandidateModel.id.asc())
            .all()
        )
        received_at = row.received_at
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
        candidates = tuple(
            ResolvedProcessCandidate(
                agent_id=row.agent_id,
                snapshot_id=row.id,
                snapshot_generation=row.generation,
                snapshot_received_at=received_at,
                boot_id=row.boot_id,
                candidate=ProcessCandidateInput(
                    pid=candidate.pid,
                    process_start_ticks=candidate.process_start_ticks,
                    pid_namespace_inode=candidate.pid_namespace_inode,
                    namespace_pid=candidate.namespace_pid,
                    executable_identity=candidate.executable_identity,
                    comm=candidate.comm,
                    cgroup=candidate.cgroup,
                    service_hint=candidate.service_hint,
                    instance_hint=candidate.instance_hint,
                    collector_capabilities=tuple(candidate.collector_capabilities or ()),
                ),
            )
            for candidate in candidate_rows
        )
        return ResolvedProcessSnapshot(
            agent_id=row.agent_id,
            snapshot_id=row.id,
            generation=row.generation,
            received_at=received_at,
            observed_at_unix_ms=row.observed_at_unix_ms,
            boot_id=row.boot_id,
            state=ProcessSnapshotState(row.state),
            authoritative=bool(row.authoritative),
            candidates=candidates,
            error=row.error or "",
        )

    def mark_offline_agents(self, timeout_sec: int = 30) -> list[AgentModel]:
        with self._write_session() as session:
            cutoff = now_utc() - timedelta(seconds=timeout_sec)
            changed = (
                session.query(AgentModel)
                .filter(
                    AgentModel.status == "ONLINE",
                    AgentModel.last_heartbeat_at < cutoff,
                )
                .all()
            )
            for agent in changed:
                agent.status = "OFFLINE"
                agent.updated_at = now_utc()
                self._write_audit(
                    session, "AGENT_OFFLINE", agent.id,
                    f"{agent.id} 心跳超时 {timeout_sec}s，标记为离线",
                )
                notify_agent_status(agent.id, "OFFLINE", agent.ip_addr)
            return changed

    @property
    def agents(self) -> dict[str, AgentModel]:
        """返回 {agent_id: AgentModel} 字典（兼容旧接口的 dict 访问）。

        2 秒 TTL 缓存，避免高频场景下每请求查全表。
        """
        return self._cached("agents", 2.0, lambda: self._query_all_agents())

    def _query_all_agents(self) -> dict[str, AgentModel]:
        s = new_session()
        try:
            return {a.id: a for a in s.query(AgentModel).all()}
        finally:
            s.close()

    def find_agent_by_ip(self, ip_addr: str) -> AgentModel | None:
        with self._read_session() as session:
            return session.query(AgentModel).filter(AgentModel.ip_addr == ip_addr).first()

    def record_agent_metrics(self, agent_id: str, metrics: dict[str, Any]) -> None:
        with self._lock:
            self.agent_metrics[agent_id] = dict(metrics)

    def persist_agent_metric_snapshots(self) -> int:
        """将内存中的 agent metrics 批量写入数据库快照表。

        每次调用对所有在线 agent 生成一条快照记录，用于趋势分析。
        返回写入的快照数量。
        """
        with self._write_session() as session:
            ts = now_utc()
            count = 0
            for agent_id, metrics in self.agent_metrics.items():
                self_data = metrics.get("self", {})
                session.add(AgentMetricSnapshotModel(
                    agent_id=agent_id,
                    cpu_percent=int(self_data.get("cpu_percent", 0) or 0),
                    rss_mb=int(self_data.get("rss_mb", 0) or 0),
                    read_kb_s=int(self_data.get("read_kb_s", 0) or 0),
                    write_kb_s=int(self_data.get("write_kb_s", 0) or 0),
                    children_count=int(self_data.get("children_count", 0) or 0),
                    created_at=ts,
                ))
                count += 1
            return count

    def get_agent_metric_history(self, agent_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """查询指定 Agent 的历史指标快照。"""
        with self._read_session() as session:
            rows = (
                session.query(AgentMetricSnapshotModel)
                .filter(AgentMetricSnapshotModel.agent_id == agent_id)
                .order_by(AgentMetricSnapshotModel.created_at.desc())
                .limit(limit)
                .all()
            )
            return [row.to_dict() for row in rows]
