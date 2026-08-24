"""
内存存储层：Agent 注册、任务管理、状态迁移和审计日志。

gRPC 服务和 HTTP API 共享同一个 Repository 实例。
当前阶段使用 Python dict 存储，后续引入 PostgreSQL 时替换实现即可。
"""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from server.app.schemas import AgentRegistration, CreateTaskRequest
from server.app.artifact_integrity import prepare_artifact
from server.app.prometheus_metrics import record_task_transition
from server.app.task_attempt_authority import (
    AuthorizedTaskAttempt,
    TaskDispatch,
    generate_task_attempt_authority,
    task_attempt_authority_sha256,
    verify_task_attempt_authority,
)
from server.app.process_attestation import (
    PROCESS_SNAPSHOT_MAX_AGE,
    ProcessCandidateInput,
    ProcessIdentityBinding,
    ProcessSnapshotState,
    ResolvedProcessCandidate,
    ResolvedProcessSnapshot,
    binding_matches_candidate,
    candidate_identity_complete,
    normalize_process_candidate_snapshot,
)
from server.app.state_machine import (
    Actor,
    StatusEvent,
    TaskStatus,
    build_status_event,
    now_utc,
)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------


@dataclass
class AgentRecord:
    """Agent 注册信息与在线状态。"""

    id: str
    hostname: str
    ip_addr: str
    version: str
    os_info: str
    capabilities: list[str]
    status: str  # "ONLINE" | "OFFLINE"
    last_heartbeat_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass
class TaskRecord:
    """任务主表记录。"""

    id: str
    name: str
    agent_id: str
    target_pid: int
    collector_type: str
    sample_rate: int
    duration_sec: int
    status: TaskStatus
    status_reason: str
    request_params: dict[str, Any]
    created_at: datetime
    process_snapshot_id: str | None = None
    process_binding_json: dict[str, Any] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass
class TaskAttemptRecord:
    id: str
    task_id: str
    attempt_no: int
    agent_id: str
    status: TaskStatus
    reason: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    lease_expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    task_attempt_authority_sha256: str | None = field(default=None, repr=False)


@dataclass
class AuditLog:
    """审计事件。Agent 上下线和任务创建/失败时写入。"""

    event_type: str
    message: str
    agent_id: str | None = None
    task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=now_utc)


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


def _same_task_request(a: dict, b: dict) -> bool:
    """Canonical JSON equality (key order independent)."""
    if a is None or b is None:
        return a == b
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(
        b, sort_keys=True, default=str
    )


def _coerce_process_binding(value: Any) -> ProcessIdentityBinding:
    if isinstance(value, ProcessIdentityBinding):
        return value
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return ProcessIdentityBinding.from_mapping(value)


class InMemoryRepository:
    """线程安全的内存存储实现。"""

    def __init__(self) -> None:
        self.agents: dict[str, AgentRecord] = {}
        self.tasks: dict[str, TaskRecord] = {}
        # (creator_id, idempotency_key) -> {"task_id", "params"} replay guard.
        self._idempotency: dict[tuple[str, str], dict] = {}
        self.task_attempts: dict[str, list[TaskAttemptRecord]] = {}
        self.events: list[StatusEvent] = []
        self.audit_logs: list[AuditLog] = []
        self.artifacts: dict[str, list[dict[str, Any]]] = {}
        self.agent_metrics: dict[str, dict[str, Any]] = {}
        self._process_snapshots: dict[str, list[ResolvedProcessSnapshot]] = {}

        # Compatibility queue retained for Control.CreateTask callers. Dispatch
        # key = agent.ip_addr, value = deque of task_id
        self._task_queues: dict[str, deque[str]] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Agent
    # ------------------------------------------------------------------

    def register_agent(
        self, agent_id: str, hostname: str, ip_addr: str,
        version: str = "0.1.0", os_info: str = "unknown",
        capabilities: list[str] | None = None,
    ) -> AgentRecord:
        """注册或更新 Agent 信息。从 OFFLINE 恢复时写入审计日志。"""
        with self._lock:
            caps = list(capabilities or [])
            existing = self.agents.get(agent_id)
            timestamp = now_utc()

            if existing is not None and existing.status == "OFFLINE":
                self._append_audit(
                    event_type="AGENT_ONLINE",
                    agent_id=agent_id,
                    message=f"{agent_id} 恢复在线",
                )

            record = AgentRecord(
                id=agent_id,
                hostname=hostname,
                ip_addr=ip_addr,
                version=version,
                os_info=os_info,
                capabilities=caps,
                status="ONLINE",
                last_heartbeat_at=timestamp,
                created_at=existing.created_at if existing else timestamp,
                updated_at=timestamp,
            )
            self.agents[agent_id] = record

            # 确保任务队列存在
            if ip_addr not in self._task_queues:
                self._task_queues[ip_addr] = deque()

            return record

    def heartbeat(
        self,
        agent_id: str,
        ip_addr: str,
        *,
        received_at: datetime | None = None,
    ) -> TaskDispatch | None:
        """Record a heartbeat and dispatch this exact Agent's oldest valid task."""
        with self._lock:
            agent = self.agents.get(agent_id)
            if agent is None:
                return None

            timestamp = received_at or now_utc()
            agent.ip_addr = ip_addr or agent.ip_addr
            agent.status = "ONLINE"
            agent.last_heartbeat_at = timestamp
            agent.updated_at = timestamp

            pending = sorted(
                (
                    task for task in self.tasks.values()
                    if task.agent_id == agent_id
                    and task.status == TaskStatus.PENDING
                ),
                key=lambda task: (task.created_at, task.id),
            )
            for task in pending:
                if task.process_binding_json is not None and not self.validate_process_binding(
                    task.process_binding_json,
                    agent_id=agent_id,
                    target_pid=task.target_pid,
                    now=timestamp,
                ):
                    continue
                authority = generate_task_attempt_authority()
                task, attempt = self._transition_task_in_lock(
                    task.id,
                    TaskStatus.RUNNING,
                    "Agent 心跳拉取待执行任务",
                    Actor.SERVER,
                    task_attempt_authority_sha256=task_attempt_authority_sha256(authority),
                )
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
        """只记录心跳，不派发任务。用于 Agent 忙碌时保持在线。"""
        with self._lock:
            agent = self.agents.get(agent_id)
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
        """Atomically append one bounded present snapshot; absence is not persisted."""
        with self._lock:
            normalized = normalize_process_candidate_snapshot(snapshot)
            if normalized.state == ProcessSnapshotState.ABSENT:
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
            if agent_id not in self.agents:
                raise ValueError(f"Agent {agent_id} 不存在")
            payload = normalized.snapshot
            assert payload is not None
            timestamp = received_at or now_utc()
            snapshot_id = f"psnap_{uuid4().hex}"
            candidates = tuple(
                ResolvedProcessCandidate(
                    agent_id=agent_id,
                    snapshot_id=snapshot_id,
                    snapshot_generation=payload.generation,
                    snapshot_received_at=timestamp,
                    boot_id=payload.boot_id,
                    candidate=candidate,
                )
                for candidate in payload.candidates
                if candidate_identity_complete(candidate)
            )
            resolved = ResolvedProcessSnapshot(
                agent_id=agent_id,
                snapshot_id=snapshot_id,
                generation=payload.generation,
                received_at=timestamp,
                observed_at_unix_ms=payload.observed_at_unix_ms,
                boot_id=payload.boot_id,
                state=normalized.state,
                authoritative=normalized.authoritative,
                candidates=candidates,
                error=payload.error,
            )
            self._process_snapshots.setdefault(agent_id, []).append(resolved)
            return resolved

    def get_process_candidate_snapshot(
        self, agent_id: str
    ) -> ResolvedProcessSnapshot:
        with self._lock:
            snapshots = self._process_snapshots.get(agent_id, ())
            if not snapshots:
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
            return max(
                snapshots,
                key=lambda item: (
                    (item.received_at or datetime.min.replace(tzinfo=now_utc().tzinfo)).timestamp(),
                    item.snapshot_id or "",
                ),
            )

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
        matches = self.resolve_process_candidates(agent_id, pid=pid)
        return matches[0] if len(matches) == 1 else None

    def validate_process_binding(
        self,
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
        snapshot = self.get_process_candidate_snapshot(value.agent_id)
        if not snapshot.authoritative or not snapshot.is_fresh(
            now=now, max_age=PROCESS_SNAPSHOT_MAX_AGE
        ):
            return False
        return any(
            binding_matches_candidate(value, candidate)
            for candidate in snapshot.candidates
        )

    def mark_offline_agents(self, timeout_sec: int = 30) -> list[AgentRecord]:
        """将超时未心跳的 Agent 标记为 OFFLINE。"""
        with self._lock:
            cutoff = now_utc() - timedelta(seconds=timeout_sec)
            changed: list[AgentRecord] = []
            for agent in self.agents.values():
                if agent.status == "ONLINE" and agent.last_heartbeat_at < cutoff:
                    agent.status = "OFFLINE"
                    agent.updated_at = now_utc()
                    changed.append(agent)
                    self._append_audit(
                        event_type="AGENT_OFFLINE",
                        agent_id=agent.id,
                        message=f"{agent.id} 心跳超时 {timeout_sec}s，标记为离线",
                    )
            return changed

    def find_agent_by_ip(self, ip_addr: str) -> AgentRecord | None:
        """按 IP 查询 Agent。Control.CreateTask 通过 target_ip 定位任务队列。"""
        with self._lock:
            for agent in self.agents.values():
                if agent.ip_addr == ip_addr:
                    return agent
            return None

    # ------------------------------------------------------------------
    # Task
    # ------------------------------------------------------------------

    def create_task(
        self,
        payload: CreateTaskRequest,
        *,
        idempotency_key: str | None = None,
        creator_id: str | None = None,
    ) -> TaskRecord:
        """创建任务，写入 PENDING 状态，加入对应 Agent IP 的队列。

        Honors (creator_id, idempotency_key) replay like the SQL repository so
        the gRPC Control entry is idempotent (guide §6.12).
        """
        if idempotency_key and creator_id:
            cached = self._idempotency.get((creator_id, idempotency_key))
            if cached is not None:
                existing = self.tasks.get(cached["task_id"])
                if existing is not None:
                    if _same_task_request(cached["params"], payload.model_dump(mode="json")):
                        return existing
                    raise ValueError(
                        f"Idempotency-Key 已用于不同参数的请求: {idempotency_key}"
                    )
        with self._lock:
            timestamp = now_utc()
            hex_suffix = uuid4().hex[:6]
            task_id = f"task_{timestamp.strftime('%Y%m%d_%H%M%S')}_{hex_suffix}"
            agent = self.agents.get(payload.agent_id)
            if agent is None:
                raise ValueError(f"Agent {payload.agent_id} 不存在")
            binding_json = None
            process_snapshot_id = None
            if payload.process_binding is not None:
                binding = _coerce_process_binding(payload.process_binding)
                if not self.validate_process_binding(
                    binding,
                    agent_id=payload.agent_id,
                    target_pid=payload.target_pid,
                    now=timestamp,
                ):
                    raise ValueError("进程身份绑定与 Agent 最新快照不匹配或已过期")
                binding_json = binding.to_dict()
                process_snapshot_id = binding.process_snapshot_id

            task = TaskRecord(
                id=task_id,
                name=payload.name,
                agent_id=payload.agent_id,
                target_pid=payload.target_pid,
                collector_type=payload.collector_type,
                sample_rate=payload.sample_rate,
                duration_sec=payload.duration_sec,
                status=TaskStatus.PENDING,
                status_reason="Web 请求创建任务",
                request_params=payload.model_dump(mode="json"),
                created_at=timestamp,
                process_snapshot_id=process_snapshot_id,
                process_binding_json=binding_json,
            )
            self.tasks[task_id] = task

            # 状态事件
            self.events.append(
                build_status_event(
                    task_id, None, TaskStatus.PENDING,
                    "Web 请求创建任务", Actor.WEB,
                    payload.model_dump(mode="json"),
                )
            )
            record_task_transition("NONE", TaskStatus.PENDING.value)

            # 审计日志
            self._append_audit(
                event_type="TASK_CREATED",
                task_id=task_id,
                message=f"任务 {task_id} 已创建",
                metadata=payload.model_dump(mode="json"),
            )

            # 加入目标 Agent IP 的任务队列
            agent = self.agents.get(payload.agent_id)
            if agent is not None:
                ip = agent.ip_addr
                if ip not in self._task_queues:
                    self._task_queues[ip] = deque()
                self._task_queues[ip].append(task_id)

            if idempotency_key and creator_id:
                self._idempotency[(creator_id, idempotency_key)] = {
                    "task_id": task_id,
                    "params": payload.model_dump(mode="json"),
                }

            return task

    def get_task_by_diagnosis_step_id(self, step_id: str) -> TaskRecord | None:
        with self._lock:
            return next((
                task for task in self.tasks.values()
                if task.request_params.get("options", {}).get("diagnosis_step_id") == step_id
            ), None)

    def transition_task(
        self, task_id: str, to_status: TaskStatus,
        reason: str, actor: Actor,
        metadata: dict[str, Any] | None = None,
        *,
        task_attempt_id: str | None = None,
    ) -> TaskRecord:
        """对指定任务执行一次状态迁移。"""
        with self._lock:
            if task_attempt_id is not None:
                attempts = self.task_attempts.get(task_id, [])
                if not any(item.id == task_attempt_id for item in attempts):
                    raise ValueError("TaskAttempt identity does not match task")
            task, _ = self._transition_task_in_lock(
                task_id, to_status, reason, actor, metadata,
            )
            return task

    def authorize_task_attempt_result(
        self,
        task_id: str,
        task_attempt_authority: str,
    ) -> AuthorizedTaskAttempt | None:
        attempts = list(reversed(self.task_attempts.get(task_id, [])))
        for attempt in attempts:
            if verify_task_attempt_authority(
                task_attempt_authority,
                attempt.task_attempt_authority_sha256,
            ):
                task = self.tasks.get(task_id)
                return (
                    AuthorizedTaskAttempt(task=task, task_attempt=attempt)
                    if task is not None
                    else None
                )
        if (
            not task_attempt_authority
            and len(attempts) == 1
            and attempts[0].task_attempt_authority_sha256 is None
        ):
            task = self.tasks.get(task_id)
            return (
                AuthorizedTaskAttempt(task=task, task_attempt=attempts[0])
                if task is not None
                else None
            )
        return None

    def _transition_task_in_lock(
        self,
        task_id: str,
        to_status: TaskStatus,
        reason: str,
        actor: Actor,
        metadata: dict[str, Any] | None = None,
        *,
        task_attempt_authority_sha256: str | None = None,
    ) -> tuple[TaskRecord, TaskAttemptRecord | None]:
        if task_id not in self.tasks:
            raise ValueError(f"任务不存在: {task_id}")
        task = self.tasks[task_id]
        event = build_status_event(
            task_id, task.status, to_status, reason, actor, metadata,
        )
        self.events.append(event)
        record_task_transition(task.status.value, to_status.value)
        task.status = to_status
        task.status_reason = reason
        attempt: TaskAttemptRecord | None = None
        if to_status == TaskStatus.RUNNING:
            if task.started_at is None:
                task.started_at = now_utc()
            attempts = self.task_attempts.setdefault(task_id, [])
            started_at = now_utc()
            attempt = TaskAttemptRecord(
                id=f"attempt_{uuid4().hex}",
                task_id=task_id,
                attempt_no=len(attempts) + 1,
                agent_id=task.agent_id,
                status=TaskStatus.RUNNING,
                reason=reason,
                created_at=started_at,
                started_at=started_at,
                lease_expires_at=started_at + timedelta(seconds=task.duration_sec + 30),
                metadata=metadata or {},
                task_attempt_authority_sha256=task_attempt_authority_sha256,
            )
            attempts.append(attempt)
        elif self.task_attempts.get(task_id):
            attempt = self.task_attempts[task_id][-1]
            attempt.status = to_status
            attempt.reason = reason
            attempt.metadata.update(metadata or {})
            if to_status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
                attempt.finished_at = now_utc()
        if to_status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED):
            task.finished_at = now_utc()
        return task, attempt

    def cancel_task(
        self,
        task_id: str,
        reason: str,
        actor: Actor = Actor.WEB,
    ) -> TaskRecord:
        """Cancel an active task and record both status and audit evidence."""
        with self._lock:
            if task_id not in self.tasks:
                raise ValueError(f"任务不存在: {task_id}")
            task = self.tasks[task_id]
            event = build_status_event(
                task_id,
                task.status,
                TaskStatus.CANCELLED,
                reason,
                actor,
                {"previous_status": task.status.value},
            )
            self.events.append(event)
            record_task_transition(task.status.value, TaskStatus.CANCELLED.value)
            task.status = TaskStatus.CANCELLED
            task.status_reason = reason
            task.finished_at = now_utc()
            if self.task_attempts.get(task_id):
                attempt = self.task_attempts[task_id][-1]
                attempt.status = TaskStatus.CANCELLED
                attempt.reason = reason
                attempt.finished_at = now_utc()
            self._append_audit(
                event_type="TASK_CANCELLED",
                task_id=task_id,
                message=f"任务 {task_id} 已取消",
                metadata={"reason": reason, "actor": actor.value},
            )
            return task

    def get_task_attempts(self, task_id: str) -> list[TaskAttemptRecord]:
        return list(self.task_attempts.get(task_id, []))

    def get_task(self, task_id: str) -> TaskRecord | None:
        """按 ID 查询任务。"""
        return self.tasks.get(task_id)

    def get_tasks(self) -> list[TaskRecord]:
        """返回所有任务的列表。"""
        return list(self.tasks.values())

    def get_task_events(self, task_id: str) -> list[StatusEvent]:
        """返回指定任务的所有状态迁移事件。"""
        return [e for e in self.events if e.task_id == task_id]

    def record_agent_metrics(self, agent_id: str, metrics: dict[str, Any]) -> None:
        with self._lock:
            self.agent_metrics[agent_id] = dict(metrics)

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    def add_artifacts(self, task_id: str, artifacts: list[dict[str, Any]]) -> None:
        """追加采集产物元数据。"""
        with self._lock:
            prepared = [prepare_artifact(task_id, item) for item in artifacts]
            self.artifacts.setdefault(task_id, []).extend(prepared)

    def add_attempt_artifacts(
        self,
        task_id: str,
        task_attempt_id: str,
        artifacts: list[dict[str, Any]],
    ) -> None:
        attempts = self.task_attempts.get(task_id, [])
        if not any(item.id == task_attempt_id for item in attempts):
            raise ValueError("TaskAttempt identity does not match task")
        with self._lock:
            prepared = []
            for item in artifacts:
                artifact = prepare_artifact(task_id, item)
                artifact["task_attempt_id"] = task_attempt_id
                prepared.append(artifact)
            self.artifacts.setdefault(task_id, []).extend(prepared)

    def get_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        """查询产物列表。"""
        return self.artifacts.get(task_id, [])

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def _append_audit(
        self, event_type: str, message: str,
        agent_id: str | None = None, task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """写入审计日志。非线程安全，调用方需持有 _lock。"""
        self.audit_logs.append(
            AuditLog(
                event_type=event_type,
                message=message,
                agent_id=agent_id,
                task_id=task_id,
                metadata=metadata or {},
            )
        )

    def get_audit_logs(self) -> list[AuditLog]:
        """返回审计日志列表。"""
        return list(self.audit_logs)

    # ------------------------------------------------------------------
    # 序列化辅助
    # ------------------------------------------------------------------

    def as_dict(self, value: Any) -> dict[str, Any]:
        """将数据类或枚举转换为纯 dict。"""
        if isinstance(value, TaskAttemptRecord):
            return {
                "id": value.id,
                "task_id": value.task_id,
                "attempt_no": value.attempt_no,
                "agent_id": value.agent_id,
                "status": value.status,
                "reason": value.reason,
                "created_at": value.created_at,
                "started_at": value.started_at,
                "finished_at": value.finished_at,
                "lease_expires_at": value.lease_expires_at,
                "metadata": dict(value.metadata),
            }
        if isinstance(value, (AgentRecord, TaskRecord, AuditLog, StatusEvent)):
            return asdict(value)
        return value
