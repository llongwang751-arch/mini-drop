"""SQLAlchemy repository tests for the retained collection pipeline."""

from datetime import datetime, timedelta, timezone

import pytest

from server.app.database import init_db, reset_engine
from server.app.process_attestation import (
    MAX_PROCESS_CANDIDATES,
    MAX_PROCESS_CAPABILITIES,
    MAX_PROCESS_TEXT_LENGTH,
    ProcessSnapshotState,
)
from server.app.schemas import CreateTaskRequest
from server.app.sql_repository import SqlRepository
from server.app.state_machine import (
    Actor,
    AnalysisStatus,
    CollectionStatus,
    TaskStatus,
    now_utc,
)


@pytest.fixture(autouse=True)
def _patch_db_url(monkeypatch):
    """测试统一使用 SQLite :memory:。"""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    from server.app.models import Base
    from server.app.database import _get_engine
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture(name="repo")
def repo_fixture() -> SqlRepository:
    """每次测试用全新的 repo + 空库。"""
    return SqlRepository()


class TestAgentPersistence:
    """Agent 注册与心跳持久化。"""

    AGENT_ID = "agent_pg_01"
    IP = "10.0.1.1"

    def test_register_agent_persists(self, repo: SqlRepository):
        repo.register_agent(self.AGENT_ID, "pg-host", self.IP)
        agents = repo.agents
        assert self.AGENT_ID in agents
        assert agents[self.AGENT_ID].status == "ONLINE"

    def test_registered_agent_survives_repo_reload(self, repo: SqlRepository):
        """新 repo 实例能从 DB 读到上一个实例写入的数据。"""
        repo.register_agent(self.AGENT_ID, "pg-host", self.IP)

        # 新 repo 实例（模拟重启）
        repo2 = SqlRepository()
        agents = repo2.agents
        assert self.AGENT_ID in agents
        assert agents[self.AGENT_ID].hostname == "pg-host"

    def test_offline_recovery_writes_audit(self, repo: SqlRepository):
        repo.register_agent(self.AGENT_ID, "h", self.IP)
        # 手动标记离线
        from server.app.database import new_session
        from server.app.models import AgentModel
        session = new_session()
        agent = session.get(AgentModel, self.AGENT_ID)
        if agent:
            agent.status = "OFFLINE"
            session.commit()
        session.close()

        # 重新注册
        repo.register_agent(self.AGENT_ID, "h", self.IP)
        logs = repo.audit_logs
        online_logs = [l for l in logs if l.event_type == "AGENT_ONLINE"]
        assert len(online_logs) == 1

    def test_heartbeat_updates_timestamp(self, repo: SqlRepository):
        repo.register_agent(self.AGENT_ID, "h", self.IP)
        agent = repo.agents[self.AGENT_ID]
        before = agent.last_heartbeat_at

        import time
        time.sleep(0.01)
        repo.heartbeat(self.AGENT_ID, self.IP)

        agent2 = repo.agents[self.AGENT_ID]
        assert agent2.last_heartbeat_at > before

    def test_mark_offline_after_timeout(self, repo: SqlRepository):
        repo.register_agent("off_agent", "off-host", "10.0.99.1")
        # 将心跳时间改到 31 秒前
        from server.app.database import new_session
        from server.app.models import AgentModel
        session = new_session()
        agent = session.get(AgentModel, "off_agent")
        agent.last_heartbeat_at = now_utc() - timedelta(seconds=31)
        session.commit()
        session.close()

        changed = repo.mark_offline_agents(timeout_sec=30)
        assert len(changed) == 1
        assert changed[0].status == "OFFLINE"

        audit = repo.audit_logs
        offline_logs = [l for l in audit if l.event_type == "AGENT_OFFLINE"]
        assert len(offline_logs) == 1

    def test_find_agent_by_ip(self, repo: SqlRepository):
        repo.register_agent("ip_agent", "ip-host", "10.0.5.5")
        found = repo.find_agent_by_ip("10.0.5.5")
        assert found is not None
        assert found.id == "ip_agent"


class TestTaskPersistence:
    """任务 CRUD 与状态迁移持久化。"""

    AGENT_ID = "agent_task"
    IP = "10.0.2.1"

    def test_create_task_persists(self, repo: SqlRepository):
        repo.register_agent(self.AGENT_ID, "h", self.IP)
        task = repo.create_task(CreateTaskRequest(
            name="pg-task", agent_id=self.AGENT_ID,
            target_pid=100, collector_type="perf_cpu",
        ))
        assert task.status == TaskStatus.PENDING.value
        assert task.id

    def test_active_task_cannot_be_archived(self, repo: SqlRepository):
        repo.register_agent(self.AGENT_ID, "h", self.IP)
        task = repo.create_task(CreateTaskRequest(
            name="active-archive-guard", agent_id=self.AGENT_ID,
            target_pid=100, collector_type="perf_cpu",
        ))
        with pytest.raises(ValueError, match="PENDING"):
            repo.delete_task(task.id)
        assert task.id in SqlRepository().tasks

    def test_archive_hides_task_but_retains_artifact_and_ai_evidence(self, repo: SqlRepository):
        from server.app.database import new_session
        from server.app.models import ArtifactModel, TaskModel

        repo.register_agent(self.AGENT_ID, "h", self.IP)
        task = repo.create_task(CreateTaskRequest(
            name="retain-evidence", agent_id=self.AGENT_ID,
            target_pid=101, collector_type="perf_cpu",
        ))
        repo.cancel_task(task.id, "test terminal state", Actor.WEB)
        repo.add_artifacts(task.id, [{
            "artifact_type": "raw", "bucket": "mini-drop",
            "object_key": f"tasks/{task.id}/perf.data",
        }])
        assert repo.delete_task(task.id) is True
        assert task.id not in SqlRepository().tasks
        assert repo.get_task(task.id) is None

        session = new_session()
        try:
            stored = session.get(TaskModel, task.id)
            assert stored is not None
            assert stored.deleted_at is not None
            assert session.query(ArtifactModel).filter_by(task_id=task.id).count() == 1
        finally:
            session.close()
        assert any(
            log.event_type == "TASK_ARCHIVED" and log.task_id == task.id
            for log in repo.audit_logs
        )

    def test_create_task_flushes_task_before_status_event(self, repo: SqlRepository, monkeypatch):
        repo.register_agent(self.AGENT_ID, "h", self.IP)

        from sqlalchemy.orm import Session

        original_flush = Session.flush
        flushed = False

        def spy_flush(self, *args, **kwargs):
            nonlocal flushed
            flushed = True
            return original_flush(self, *args, **kwargs)

        original_write_event = repo._write_event

        def assert_task_flushed_before_event(*args, **kwargs):
            assert flushed
            return original_write_event(*args, **kwargs)

        monkeypatch.setattr(Session, "flush", spy_flush)
        monkeypatch.setattr(repo, "_write_event", assert_task_flushed_before_event)

        task = repo.create_task(CreateTaskRequest(
            name="pg-fk-order", agent_id=self.AGENT_ID,
            target_pid=101, collector_type="perf_cpu",
        ))

        assert task.status == TaskStatus.PENDING.value

    def test_create_task_requires_registered_agent(self, repo: SqlRepository):
        with pytest.raises(ValueError, match="Agent missing_agent 不存在"):
            repo.create_task(CreateTaskRequest(
                name="missing-agent", agent_id="missing_agent",
                target_pid=100, collector_type="perf_cpu",
            ))

    def test_task_survives_repo_reload(self, repo: SqlRepository):
        repo.register_agent(self.AGENT_ID, "h", self.IP)
        task = repo.create_task(CreateTaskRequest(
            name="survive", agent_id=self.AGENT_ID,
            target_pid=200, collector_type="perf_cpu",
        ))
        task_id = task.id

        repo2 = SqlRepository()
        tasks = repo2.tasks
        assert task_id in tasks

    def test_transition_task_persists_event(self, repo: SqlRepository):
        repo.register_agent(self.AGENT_ID, "h", self.IP)
        task = repo.create_task(CreateTaskRequest(
            name="event-test", agent_id=self.AGENT_ID,
            target_pid=300, collector_type="perf_cpu",
        ))

        repo.transition_task(task.id, TaskStatus.RUNNING, "心跳拉取", Actor.SERVER)
        events = repo.events
        assert len(events) == 2  # PENDING + RUNNING

    def test_transition_rejects_illegal_status(self, repo: SqlRepository):
        repo.register_agent(self.AGENT_ID, "h", self.IP)
        task = repo.create_task(CreateTaskRequest(
            name="bad-transition", agent_id=self.AGENT_ID,
            target_pid=1, collector_type="perf_cpu",
        ))
        with pytest.raises(ValueError, match="非法的状态迁移"):
            repo.transition_task(task.id, TaskStatus.DONE, "直接跳过", Actor.SERVER)

    def test_heartbeat_returns_pending_task(self, repo: SqlRepository):
        repo.register_agent(self.AGENT_ID, "h", self.IP)
        task = repo.create_task(CreateTaskRequest(
            name="heartbeat-test", agent_id=self.AGENT_ID,
            target_pid=400, collector_type="perf_cpu",
        ))

        pulled = repo.heartbeat(self.AGENT_ID, self.IP)
        assert pulled is not None
        assert pulled.id == task.id
        assert pulled.status == TaskStatus.RUNNING.value

    def test_pending_task_survives_repo_reload_before_heartbeat(self, repo: SqlRepository):
        repo.register_agent(self.AGENT_ID, "h", self.IP)
        task = repo.create_task(CreateTaskRequest(
            name="queued-before-reload", agent_id=self.AGENT_ID,
            target_pid=401, collector_type="perf_cpu",
        ))

        repo2 = SqlRepository()
        pulled = repo2.heartbeat(self.AGENT_ID, self.IP)

        assert pulled is not None
        assert pulled.id == task.id
        assert pulled.status == TaskStatus.RUNNING.value


def _process_candidate(pid: int = 4242, **overrides):
    value = {
        "pid": pid,
        "process_start_ticks": 101,
        "pid_namespace_inode": 202,
        "namespace_pid": pid,
        "executable_identity": "sha256:executable",
        "comm": "worker",
        "cgroup": "/service/worker",
        "service_hint": "worker.service",
        "instance_hint": "instance-a",
        "collector_capabilities": ["perf_cpu", "ebpf_io"],
    }
    value.update(overrides)
    return value


def _process_snapshot(*, candidates=None, **overrides):
    value = {
        "generation": 1,
        "boot_id": "boot-a",
        "observed_at_unix_ms": 1,
        "complete": True,
        "truncated": False,
        "candidates": [_process_candidate()] if candidates is None else candidates,
        "error": "",
    }
    value.update(overrides)
    return value


def _register_process_agent(repo, agent_id="process-agent", ip="10.0.20.1"):
    repo.register_agent(agent_id, "process-host", ip)
    return agent_id, ip


class TestProcessAttestation:
    @pytest.mark.parametrize(
        ("snapshot", "state", "authoritative"),
        [
            (_process_snapshot(candidates=[]), ProcessSnapshotState.COMPLETE_EMPTY, True),
            (_process_snapshot(), ProcessSnapshotState.COMPLETE_POPULATED, True),
            (_process_snapshot(complete=False), ProcessSnapshotState.PARTIAL, False),
            (_process_snapshot(error="collector failed"), ProcessSnapshotState.FAILED, False),
            (_process_snapshot(truncated=True), ProcessSnapshotState.TRUNCATED, False),
            (
                _process_snapshot(
                    candidates=[_process_candidate(), _process_candidate()]
                ),
                ProcessSnapshotState.PARTIAL,
                False,
            ),
        ],
    )
    def test_sql_preserves_snapshot_states(
        self, repo: SqlRepository, snapshot, state, authoritative
    ):
        agent_id, _ = _register_process_agent(repo)
        resolved = repo.record_process_candidate_snapshot(agent_id, snapshot)
        assert resolved.state == state
        assert resolved.authoritative is authoritative

    def test_absent_snapshot_is_not_persisted_or_refreshed(
        self, repo: SqlRepository
    ):
        agent_id, _ = _register_process_agent(repo)
        received_at = datetime(2026, 8, 24, tzinfo=timezone.utc)
        stored = repo.record_process_candidate_snapshot(
            agent_id, _process_snapshot(), received_at=received_at
        )
        absent = repo.record_process_candidate_snapshot(
            agent_id, None, received_at=received_at + timedelta(seconds=10)
        )
        assert absent.state == ProcessSnapshotState.ABSENT
        latest = repo.get_process_candidate_snapshot(agent_id)
        assert latest.snapshot_id == stored.snapshot_id
        assert latest.received_at == received_at

    def test_newest_server_receipt_wins_not_client_time_or_generation(
        self, repo: SqlRepository
    ):
        agent_id, _ = _register_process_agent(repo)
        base = datetime(2026, 8, 24, tzinfo=timezone.utc)
        older = repo.record_process_candidate_snapshot(
            agent_id,
            _process_snapshot(generation=99, observed_at_unix_ms=9_999_999),
            received_at=base,
        )
        newer = repo.record_process_candidate_snapshot(
            agent_id,
            _process_snapshot(generation=1, observed_at_unix_ms=1),
            received_at=base + timedelta(seconds=1),
        )
        assert older.snapshot_id != newer.snapshot_id
        assert repo.get_process_candidate_snapshot(agent_id).snapshot_id == newer.snapshot_id

    def test_binding_freshness_uses_server_receipt_with_exact_boundary(
        self, repo: SqlRepository
    ):
        agent_id, _ = _register_process_agent(repo)
        received_at = datetime(2026, 8, 24, tzinfo=timezone.utc)
        snapshot = repo.record_process_candidate_snapshot(
            agent_id,
            _process_snapshot(observed_at_unix_ms=0),
            received_at=received_at,
        )
        binding = snapshot.candidates[0].binding()
        assert repo.validate_process_binding(
            binding, now=received_at + timedelta(seconds=15)
        )
        assert not repo.validate_process_binding(
            binding, now=received_at + timedelta(seconds=15, microseconds=1)
        )
        assert not repo.validate_process_binding(
            binding, now=received_at - timedelta(microseconds=1)
        )

    def test_later_snapshot_reaffirms_same_immutable_process_binding(
        self, repo: SqlRepository
    ):
        agent_id, _ = _register_process_agent(repo)
        timestamp = now_utc()
        first = repo.record_process_candidate_snapshot(
            agent_id, _process_snapshot(generation=1), received_at=timestamp
        )
        binding = first.candidates[0].binding()
        second = repo.record_process_candidate_snapshot(
            agent_id,
            _process_snapshot(generation=2),
            received_at=timestamp + timedelta(seconds=5),
        )

        assert first.snapshot_id != second.snapshot_id
        assert repo.validate_process_binding(
            binding, now=timestamp + timedelta(seconds=5)
        )

    def test_snapshot_retention_prunes_unreferenced_heartbeat_history(
        self, repo: SqlRepository, monkeypatch
    ):
        from server.app.database import new_session
        from server.app.models import ProcessCandidateSnapshotModel

        monkeypatch.setenv("MINI_DROP_PROCESS_SNAPSHOT_RETENTION_PER_AGENT", "2")
        agent_id, _ = _register_process_agent(repo)
        timestamp = now_utc()
        for generation in range(1, 5):
            repo.record_process_candidate_snapshot(
                agent_id,
                _process_snapshot(generation=generation),
                received_at=timestamp + timedelta(seconds=generation),
            )

        session = new_session()
        try:
            rows = (
                session.query(ProcessCandidateSnapshotModel)
                .filter(ProcessCandidateSnapshotModel.agent_id == agent_id)
                .order_by(ProcessCandidateSnapshotModel.generation.asc())
                .all()
            )
            assert [row.generation for row in rows] == [3, 4]
        finally:
            session.close()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("agent_id", "other-agent"),
            ("pid", 9999),
            ("boot_id", "boot-b"),
            ("process_start_ticks", 999),
            ("pid_namespace_inode", 999),
            ("namespace_pid", 999),
            ("executable_identity", "sha256:changed"),
        ],
    )
    def test_binding_rejects_every_changed_identity_field(
        self, repo: SqlRepository, field, value
    ):
        agent_id, _ = _register_process_agent(repo)
        now = now_utc()
        snapshot = repo.record_process_candidate_snapshot(
            agent_id, _process_snapshot(), received_at=now
        )
        binding = snapshot.candidates[0].binding().to_dict()
        binding[field] = value
        assert not repo.validate_process_binding(
            binding, agent_id=agent_id, target_pid=4242, now=now
        )

    def test_bound_task_persists_binding_and_dispatch_gate(
        self, repo: SqlRepository
    ):
        agent_id, ip = _register_process_agent(repo)
        now = now_utc()
        snapshot = repo.record_process_candidate_snapshot(
            agent_id, _process_snapshot(), received_at=now
        )
        binding = snapshot.candidates[0].binding().to_dict()
        task = repo.create_task(CreateTaskRequest(
            name="bound-task",
            agent_id=agent_id,
            target_pid=4242,
            collector_type="perf_cpu",
            process_binding=binding,
        ))
        assert task.process_snapshot_id == snapshot.snapshot_id
        assert task.process_binding_json == binding
        persisted_request_binding = task.request_params["process_binding"]
        assert {
            key: value for key, value in persisted_request_binding.items()
            if key != "snapshot_received_at"
        } == {
            key: value for key, value in binding.items()
            if key != "snapshot_received_at"
        }
        assert datetime.fromisoformat(
            persisted_request_binding["snapshot_received_at"].replace("Z", "+00:00")
        ) == datetime.fromisoformat(binding["snapshot_received_at"])
        assert repo.heartbeat(
            agent_id, ip, received_at=now + timedelta(seconds=15)
        ).id == task.id

    def test_stale_or_rebooted_binding_leaves_task_pending_without_running_event(
        self, repo: SqlRepository
    ):
        from server.app.database import new_session
        from server.app.models import StatusEventModel, TaskAttemptModel, TaskModel

        agent_id, ip = _register_process_agent(repo)
        now = now_utc()
        snapshot = repo.record_process_candidate_snapshot(
            agent_id, _process_snapshot(), received_at=now
        )
        binding = snapshot.candidates[0].binding().to_dict()
        task = repo.create_task(CreateTaskRequest(
            name="gated-task",
            agent_id=agent_id,
            target_pid=4242,
            collector_type="perf_cpu",
            process_binding=binding,
        ))
        repo.record_process_candidate_snapshot(
            agent_id,
            _process_snapshot(generation=2, boot_id="boot-after-reboot"),
            received_at=now + timedelta(seconds=1),
        )
        assert repo.heartbeat(
            agent_id, ip, received_at=now + timedelta(seconds=2)
        ) is None
        session = new_session()
        try:
            assert session.get(TaskModel, task.id).status == TaskStatus.PENDING.value
            assert session.query(StatusEventModel).filter_by(
                task_id=task.id, to_status=TaskStatus.RUNNING.value
            ).count() == 0
            assert session.query(TaskAttemptModel).filter_by(task_id=task.id).count() == 0
        finally:
            session.close()

    def test_task_creation_rejects_stale_binding(self, repo: SqlRepository):
        agent_id, _ = _register_process_agent(repo)
        received_at = now_utc() - timedelta(seconds=16)
        snapshot = repo.record_process_candidate_snapshot(
            agent_id, _process_snapshot(), received_at=received_at
        )
        with pytest.raises(ValueError, match="最新快照"):
            repo.create_task(CreateTaskRequest(
                name="stale-bound-task",
                agent_id=agent_id,
                target_pid=4242,
                collector_type="perf_cpu",
                process_binding=snapshot.candidates[0].binding().to_dict(),
            ))

    def test_same_ip_dispatch_is_agent_scoped(self, repo: SqlRepository):
        shared_ip = "10.0.20.99"
        repo.register_agent("agent-a", "a", shared_ip)
        repo.register_agent("agent-b", "b", shared_ip)
        task_a = repo.create_task(CreateTaskRequest(
            name="a", agent_id="agent-a", target_pid=1, collector_type="perf_cpu"
        ))
        task_b = repo.create_task(CreateTaskRequest(
            name="b", agent_id="agent-b", target_pid=1, collector_type="perf_cpu"
        ))
        assert repo.heartbeat("agent-b", shared_ip).id == task_b.id
        assert repo.get_task(task_a.id).status == TaskStatus.PENDING.value

    def test_wire_metadata_is_sanitized_and_bounded_fail_closed(
        self, repo: SqlRepository
    ):
        agent_id, _ = _register_process_agent(repo)
        candidates = [
            _process_candidate(
                pid=5000 + index,
                process_start_ticks=9000 + index,
                namespace_pid=5000 + index,
                comm="worker\x00\n" + ("x" * MAX_PROCESS_TEXT_LENGTH),
                collector_capabilities=[
                    f"capability-{capability_index}"
                    for capability_index in range(MAX_PROCESS_CAPABILITIES + 1)
                ],
            )
            for index in range(MAX_PROCESS_CANDIDATES + 1)
        ]

        resolved = repo.record_process_candidate_snapshot(
            agent_id,
            _process_snapshot(boot_id="boot\x7f-a", candidates=candidates),
        )

        assert resolved.state == ProcessSnapshotState.TRUNCATED
        assert resolved.authoritative is False
        assert len(resolved.candidates) == MAX_PROCESS_CANDIDATES
        assert resolved.boot_id == "boot-a"
        candidate = resolved.candidates[0].candidate
        assert "\x00" not in candidate.comm
        assert "\n" not in candidate.comm
        assert len(candidate.comm) == MAX_PROCESS_TEXT_LENGTH
        assert len(candidate.collector_capabilities) == MAX_PROCESS_CAPABILITIES

class TestArtifactPersistence:
    """产物存储持久化。"""

    AGENT_ID = "agent_art"
    IP = "10.0.3.1"

    def test_add_and_query_artifacts(self, repo: SqlRepository):
        repo.register_agent(self.AGENT_ID, "h", self.IP)
        task = repo.create_task(CreateTaskRequest(
            name="art-pg", agent_id=self.AGENT_ID,
            target_pid=1, collector_type="perf_cpu",
        ))
        repo.add_artifacts(task.id, [
            {"artifact_type": "raw", "bucket": "mini-drop",
             "object_key": "tasks/x/perf.data"}
        ])
        arts = repo.artifacts.get(task.id, [])
        assert len(arts) == 1
        assert arts[0]["artifact_type"] == "raw"
        assert arts[0]["integrity_status"] == "LEGACY_UNVERIFIED"
        assert arts[0]["manifest"]["manifest_version"] == "mini-drop.artifact.v1"

    def test_local_artifact_persists_verified_manifest(self, repo: SqlRepository, tmp_path):
        import hashlib

        repo.register_agent(self.AGENT_ID, "h", self.IP)
        task = repo.create_task(CreateTaskRequest(
            name="art-integrity", agent_id=self.AGENT_ID,
            target_pid=1, collector_type="perf_cpu",
        ))
        artifact_path = tmp_path / "sample.json"
        artifact_path.write_bytes(b'{"ok":true}')
        repo.add_artifacts(task.id, [{
            "artifact_type": "top_json",
            "local_path": str(artifact_path),
            "filename": "sample.json",
            "content_type": "application/json",
        }])

        artifact = repo.get_artifacts(task.id)[0]
        assert artifact["sha256"] == hashlib.sha256(b'{"ok":true}').hexdigest()
        assert artifact["integrity_status"] == "VERIFIED"
        assert artifact["manifest"]["size_bytes"] == 11

    def test_artifacts_survive_repo_reload(self, repo: SqlRepository):
        repo.register_agent(self.AGENT_ID, "h", self.IP)
        task = repo.create_task(CreateTaskRequest(
            name="art-survive", agent_id=self.AGENT_ID,
            target_pid=1, collector_type="perf_cpu",
        ))
        repo.add_artifacts(task.id, [{"artifact_type": "raw", "bucket": "m", "object_key": "k"}])

        repo2 = SqlRepository()
        arts = repo2.artifacts.get(task.id, [])
        assert len(arts) == 1


class TestAuditPersistence:
    """审计日志持久化。"""

    def test_audit_logs_survive_repo_reload(self, repo: SqlRepository):
        repo.register_agent("audit_agent", "h", "10.0.4.1")
        repo.create_task(CreateTaskRequest(
            name="audit-task", agent_id="audit_agent",
            target_pid=1, collector_type="perf_cpu",
        ))

        repo2 = SqlRepository()
        logs = repo2.audit_logs
        assert len(logs) >= 1
        assert any(l.event_type == "TASK_CREATED" for l in logs)


class TestTaskCancellationPersistence:
    def test_expired_running_attempt_is_failed_by_maintenance(self, repo: SqlRepository):
        from server.app.database import new_session
        from server.app.models import TaskAttemptModel

        agent_id = "lease_expiry_agent"
        repo.register_agent(agent_id, "h", "10.0.8.9")
        task = repo.create_task(CreateTaskRequest(
            name="expired-lease-task",
            agent_id=agent_id,
            target_pid=399,
            collector_type="sys_metrics",
            duration_sec=5,
        ))
        repo.transition_task(task.id, TaskStatus.RUNNING, "Agent claimed", Actor.SERVER)
        session = new_session()
        attempt = session.query(TaskAttemptModel).filter_by(task_id=task.id).one()
        attempt.lease_expires_at = now_utc() - timedelta(seconds=1)
        session.commit()
        session.close()

        expired = repo.expire_stale_task_leases(timestamp=now_utc())

        assert expired == [task.id]
        persisted = repo.get_task(task.id)
        assert persisted.status == TaskStatus.FAILED.value
        assert persisted.collection_status == CollectionStatus.FAILED.value
        assert repo.get_task_attempts(task.id)[0].status == TaskStatus.FAILED.value
        assert any(
            item.event_type == "TASK_LEASE_EXPIRED" and item.task_id == task.id
            for item in repo.audit_logs
        )

    def test_cancel_task_persists_terminal_state_event_and_audit(self, repo: SqlRepository):
        agent_id = "cancel_agent"
        repo.register_agent(agent_id, "h", "10.0.8.1")
        task = repo.create_task(CreateTaskRequest(
            name="cancel-me",
            agent_id=agent_id,
            target_pid=301,
            collector_type="perf_cpu",
        ))
        repo.transition_task(task.id, TaskStatus.RUNNING, "Agent claimed", Actor.SERVER)

        cancelled = repo.cancel_task(task.id, "operator requested cancellation", Actor.WEB)

        assert cancelled.status == TaskStatus.CANCELLED.value
        assert cancelled.finished_at is not None
        assert repo.events[-1].to_status == TaskStatus.CANCELLED
        assert any(
            item.event_type == "TASK_CANCELLED" and item.task_id == task.id
            for item in repo.audit_logs
        )

    def test_running_transition_creates_and_updates_attempt(self, repo: SqlRepository):
        agent_id = "attempt_agent"
        repo.register_agent(agent_id, "h", "10.0.8.2")
        task = repo.create_task(CreateTaskRequest(
            name="attempted-task",
            agent_id=agent_id,
            target_pid=302,
            collector_type="perf_cpu",
        ))

        repo.transition_task(task.id, TaskStatus.RUNNING, "Agent claimed", Actor.SERVER)
        attempts = repo.get_task_attempts(task.id)
        assert len(attempts) == 1
        assert attempts[0].attempt_no == 1
        assert attempts[0].status == TaskStatus.RUNNING.value
        assert attempts[0].lease_expires_at is not None

        repo.transition_task(task.id, TaskStatus.FAILED, "collector failed", Actor.AGENT)
        attempts = repo.get_task_attempts(task.id)
        assert attempts[0].status == TaskStatus.FAILED.value
        assert attempts[0].finished_at is not None

    def test_collection_attempt_finishes_before_analyzer(self, repo: SqlRepository):
        repo.register_agent("split_state_agent", "h", "10.0.8.3")
        task = repo.create_task(CreateTaskRequest(
            name="split-state-task",
            agent_id="split_state_agent",
            target_pid=303,
            collector_type="perf_cpu",
        ))

        repo.transition_task(task.id, TaskStatus.RUNNING, "Agent claimed", Actor.SERVER)
        repo.transition_task(task.id, TaskStatus.UPLOADING, "uploading", Actor.AGENT)
        repo.transition_task(task.id, TaskStatus.ANALYZING, "artifact persisted", Actor.AGENT)

        persisted = repo.get_task(task.id)
        attempt = repo.get_task_attempts(task.id)[0]
        assert persisted.collection_status == CollectionStatus.COLLECTED.value
        assert persisted.analysis_status == AnalysisStatus.PENDING.value
        assert attempt.status == "SUCCEEDED"
        assert attempt.finished_at is not None
