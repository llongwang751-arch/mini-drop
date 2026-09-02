"""Pydantic 数据模型：HTTP API 的请求与响应结构。

对外 Go API 与内部 Python Worker 共享这些任务参数模型。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from server.app.state_machine import TaskStatus

CollectorType = Literal[
    "perf_cpu", "ebpf_io", "pyspy", "continuous_perf", "java_async",
    "go_pprof", "memory_smaps", "sys_metrics", "database_lock",
]
MIN_TASK_DURATION_SEC = 1
MAX_TASK_DURATION_SEC = 120
MIN_SAMPLE_RATE = 1
MAX_SAMPLE_RATE = 999


# ── 通用 ──────────────────────────────────────────────────────


class APIResponse(BaseModel):
    """所有 HTTP 端点的统一返回结构。"""

    code: int = 0
    message: str = "ok"
    data: Any = None


# ── Agent ─────────────────────────────────────────────────────


class AgentRegistration(BaseModel):
    """Agent 注册请求（与 gRPC RegisterAgentRequest 字段对齐）。"""

    agent_id: str
    hostname: str
    ip_addr: str
    version: str = "0.1.0"
    os_info: str = "unknown"
    capabilities: list[CollectorType] = Field(default_factory=list)


class AgentMetrics(BaseModel):
    """Agent 每次心跳上报的自身资源指标。"""

    cpu_percent: float = 0.0
    rss_mb: float = 0.0
    read_kb_s: float = 0.0
    write_kb_s: float = 0.0
    children_count: int = 0


# ── 任务 ──────────────────────────────────────────────────────


class ProcessIdentityBindingRequest(BaseModel):
    """Immutable Agent-scoped identity selected from a server-received snapshot."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., min_length=1, max_length=128)
    pid: int = Field(..., ge=1)
    boot_id: str = Field(..., min_length=1, max_length=1024)
    process_start_ticks: int = Field(..., ge=1)
    pid_namespace_inode: int = Field(..., ge=1)
    namespace_pid: int = Field(..., ge=1)
    executable_identity: str = Field(..., min_length=1, max_length=1024)
    process_snapshot_id: str = Field(..., min_length=1, max_length=128)
    snapshot_generation: int = Field(..., ge=1)
    snapshot_received_at: datetime


class CreateTaskRequest(BaseModel):
    """Web 创建任务的请求体。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    agent_id: str
    target_pid: int
    collector_type: CollectorType
    sample_rate: int = 99
    duration_sec: int = 15
    options: dict[str, Any] = Field(default_factory=dict)
    process_binding: Optional[ProcessIdentityBindingRequest] = None


class CancelTaskRequest(BaseModel):
    """Request body for cancelling an active collection task."""

    reason: str = Field(default="用户请求取消任务", min_length=1, max_length=512)


class TaskView(BaseModel):
    """返回给前端的任务摘要。"""

    id: str
    name: str
    agent_id: str
    target_pid: int
    collector_type: str
    sample_rate: int
    duration_sec: int
    status: str  # TaskStatus.value
    status_reason: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    collection_status: str = "QUEUED"
    analysis_status: str = "PENDING"
    request_params: dict[str, Any]
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class AgentView(BaseModel):
    """返回给前端的 Agent 摘要。"""

    id: str
    hostname: str
    ip_addr: str
    version: str
    os_info: str
    capabilities: list[str]
    status: str
    last_heartbeat_at: datetime
    created_at: datetime
    updated_at: datetime


class AuditLogView(BaseModel):
    """返回给前端的审计事件。"""

    event_type: str
    message: str
    agent_id: Optional[str] = None
    task_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
