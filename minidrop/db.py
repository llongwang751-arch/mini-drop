import json
import threading
import time
import uuid
from pathlib import Path

from sqlalchemy import Float, Index, Integer, String, Text, create_engine, select, update
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


VALID_TRANSITIONS = {
    "PENDING": {"RUNNING", "FAILED"},
    "RUNNING": {"UPLOADING", "FAILED"},
    "UPLOADING": {"DONE", "FAILED"},
    "DONE": set(),
    "FAILED": set(),
}


class Base(DeclarativeBase):
    pass


class AgentModel(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(64))
    online: Mapped[int] = mapped_column(Integer)
    last_seen: Mapped[float] = mapped_column(Float)
    metadata_json: Mapped[str] = mapped_column("metadata", Text)


class TaskModel(Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("idx_tasks_agent_status", "agent_id", "status"),)
    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64))
    pid: Mapped[int] = mapped_column(Integer)
    duration: Mapped[int] = mapped_column(Integer)
    rate: Mapped[int] = mapped_column(Integer)
    collector: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float)
    raw_data: Mapped[str | None] = mapped_column(Text().with_variant(LONGTEXT, "mysql"), nullable=True)
    result: Mapped[str | None] = mapped_column(Text().with_variant(LONGTEXT, "mysql"), nullable=True)
    continuous: Mapped[int] = mapped_column(Integer, default=0)


class TransitionModel(Base):
    __tablename__ = "transitions"
    __table_args__ = (Index("idx_transitions_task", "task_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(12))
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float)


class AuditModel(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[float] = mapped_column(Float)


def _database_url(value):
    if "://" in value:
        return value
    Path(value).parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{Path(value).resolve().as_posix()}"


class Store:
    def __init__(self, database_url="sqlite:///data/minidrop.db"):
        self.database_url = _database_url(database_url)
        kwargs = {"pool_pre_ping": True}
        if self.database_url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        if self.database_url == "sqlite:///:memory:":
            kwargs["poolclass"] = StaticPool
        self.engine = create_engine(self.database_url, **kwargs)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        self.lock = threading.RLock()
        self._create_tables()

    def _create_tables(self):
        attempts = 30 if self.database_url.startswith("mysql") else 1
        for attempt in range(attempts):
            try:
                Base.metadata.create_all(self.engine)
                return
            except OperationalError:
                if attempt == attempts - 1:
                    raise
                time.sleep(2)

    @staticmethod
    def _task(model, transitions=None, payloads=False):
        item = {column.name: getattr(model, column.name) for column in TaskModel.__table__.columns}
        if payloads:
            for key in ("raw_data", "result"):
                item[key] = json.loads(item[key]) if item[key] else None
        if transitions is not None:
            item["transitions"] = [
                {column.name: getattr(row, column.name) for column in TransitionModel.__table__.columns}
                for row in transitions
            ]
        return item

    @staticmethod
    def _agent(model):
        return {
            "id": model.id, "hostname": model.hostname, "version": model.version,
            "online": model.online, "last_seen": model.last_seen, "metadata": model.metadata_json,
        }

    def create_task(self, agent_id, pid, duration, rate, collector, continuous=False):
        task_id, now = uuid.uuid4().hex[:12], time.time()
        with self.lock, self.Session.begin() as session:
            session.add(TaskModel(id=task_id, agent_id=agent_id, pid=pid, duration=duration, rate=rate,
                                  collector=collector, status="PENDING", reason="task accepted",
                                  created_at=now, updated_at=now, continuous=int(continuous)))
            session.add(TransitionModel(task_id=task_id, from_status=None, to_status="PENDING",
                                        reason="task accepted", created_at=now))
        return self.get_task(task_id)

    def transition(self, task_id, status, reason):
        if not reason or not reason.strip():
            raise ValueError("transition reason is required")
        with self.lock, self.Session.begin() as session:
            task = session.get(TaskModel, task_id)
            if not task:
                raise KeyError(task_id)
            old = task.status
            if status not in VALID_TRANSITIONS[old]:
                raise ValueError(f"invalid transition {old} -> {status}")
            now = time.time()
            task.status, task.reason, task.updated_at = status, reason, now
            session.add(TransitionModel(task_id=task_id, from_status=old, to_status=status,
                                        reason=reason, created_at=now))
        return self.get_task(task_id)

    def heartbeat(self, agent_id, hostname, version, metadata):
        now = time.time()
        with self.lock, self.Session.begin() as session:
            agent = session.get(AgentModel, agent_id)
            recovered = not agent or not agent.online
            if not agent:
                agent = AgentModel(id=agent_id, hostname=hostname, version=version, online=1,
                                   last_seen=now, metadata_json=json.dumps(metadata))
                session.add(agent)
            else:
                agent.hostname, agent.version, agent.online = hostname, version, 1
                agent.last_seen, agent.metadata_json = now, json.dumps(metadata)
            if recovered:
                session.add(AuditModel(kind="AGENT_ONLINE", subject=agent_id,
                                       message="agent connected or recovered", created_at=now))

    def mark_offline(self, cutoff=30):
        now = time.time()
        with self.lock, self.Session.begin() as session:
            agents = session.scalars(
                select(AgentModel).where(AgentModel.online == 1, AgentModel.last_seen < now - cutoff)
            ).all()
            for agent in agents:
                agent.online = 0
                session.add(AuditModel(kind="AGENT_OFFLINE", subject=agent.id,
                                       message=f"no heartbeat for {cutoff}s", created_at=now))
        return len(agents)

    def claim_task(self, agent_id):
        with self.lock, self.Session.begin() as session:
            task = session.scalars(
                select(TaskModel).where(TaskModel.agent_id == agent_id, TaskModel.status == "PENDING")
                .order_by(TaskModel.created_at).with_for_update(skip_locked=True).limit(1)
            ).first()
            task_id = task.id if task else None
        return self.transition(task_id, "RUNNING", "claimed by agent") if task_id else None

    def set_payload(self, task_id, field, payload):
        if field not in {"raw_data", "result"}:
            raise ValueError("invalid payload field")
        with self.lock, self.Session.begin() as session:
            task = session.get(TaskModel, task_id)
            if not task:
                raise KeyError(task_id)
            setattr(task, field, json.dumps(payload))
            task.updated_at = time.time()

    def get_task(self, task_id):
        with self.Session() as session:
            task = session.get(TaskModel, task_id)
            if not task:
                return None
            transitions = session.scalars(
                select(TransitionModel).where(TransitionModel.task_id == task_id).order_by(TransitionModel.id)
            ).all()
            return self._task(task, transitions, payloads=True)

    def list_tasks(self):
        with self.Session() as session:
            return [self._task(task) for task in session.scalars(select(TaskModel).order_by(TaskModel.created_at.desc()))]

    def continuous_window(self, agent_id, start=None, end=None):
        end, start = end or time.time(), start or (end or time.time()) - 300
        with self.Session() as session:
            tasks = session.scalars(
                select(TaskModel).where(TaskModel.agent_id == agent_id, TaskModel.continuous == 1,
                                        TaskModel.status == "DONE", TaskModel.updated_at.between(start, end))
                .order_by(TaskModel.updated_at)
            ).all()
            rows = [self._task(task, payloads=True) for task in tasks]
        for row in rows:
            row["raw_data"] = None
        return rows

    def stop_continuous(self, task_id):
        with self.lock, self.Session.begin() as session:
            task = session.get(TaskModel, task_id)
            if not task:
                raise KeyError(task_id)
            result = session.execute(
                update(TaskModel).where(TaskModel.agent_id == task.agent_id, TaskModel.pid == task.pid,
                                        TaskModel.collector == task.collector, TaskModel.continuous == 1)
                .values(continuous=0, updated_at=time.time())
            )
        return {"stopped": result.rowcount, "task_id": task_id}

    def list_agents(self):
        with self.Session() as session:
            return [self._agent(agent) for agent in session.scalars(select(AgentModel).order_by(AgentModel.id))]

    def list_audit(self):
        with self.Session() as session:
            rows = session.scalars(select(AuditModel).order_by(AuditModel.id.desc()).limit(100)).all()
            return [{column.name: getattr(row, column.name) for column in AuditModel.__table__.columns} for row in rows]
