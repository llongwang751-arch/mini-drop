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

    def heartbeat(self, agent_id: str, ip_addr: str) -> TaskModel | None:
        with self._write_session() as session:
            agent = session.get(AgentModel, agent_id)
            if agent is None:
                return None

            agent.status = "ONLINE"
            agent.last_heartbeat_at = now_utc()
            agent.updated_at = now_utc()

            task = (
                session.query(TaskModel)
                .filter(
                    TaskModel.agent_id == agent_id,
                    TaskModel.status == TaskStatus.PENDING.value,
                    TaskModel.deleted_at.is_(None),
                )
                .order_by(TaskModel.created_at.asc())
                .first()
            )
            if task is None:
                return None

            self._transition_task_in_session(
                session, task.id, TaskStatus.RUNNING,
                "Agent 心跳拉取待执行任务", Actor.SERVER,
            )
            task.status = TaskStatus.RUNNING.value
            return task

    def heartbeat_only(self, agent_id: str, ip_addr: str) -> None:
        """Update heartbeat timestamp without dispatching a new task."""
        with self._write_session() as session:
            agent = session.get(AgentModel, agent_id)
            if agent is None:
                return
            agent.ip_addr = ip_addr or agent.ip_addr
            agent.status = "ONLINE"
            agent.last_heartbeat_at = now_utc()
            agent.updated_at = now_utc()

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
