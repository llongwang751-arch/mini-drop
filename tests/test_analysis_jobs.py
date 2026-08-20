"""AnalysisJob persistence, lease, retry and worker integration tests."""

from __future__ import annotations

from datetime import timedelta
import time

import pytest

from server.app.analysis_jobs import (
    AnalysisWorker,
    AnalyzerOutput,
    AnalyzerRegistry,
    ArtifactSetAnalyzer,
    artifact_input_checksum,
    default_analyzer_registry,
    enqueue_artifact_analysis,
)
from server.app.artifact_contracts import COLLECTOR_CONTRACTS, ArtifactContractError
from server.app.database import init_db, new_session, reset_engine
from server.app.models import AnalysisJobModel, Base
from server.app.schemas import CreateTaskRequest
from server.app.sql_repository import SqlRepository
from server.app.state_machine import Actor, TaskStatus, now_utc


@pytest.fixture(autouse=True)
def _database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    from server.app.database import _get_engine

    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture
def repo() -> SqlRepository:
    result = SqlRepository()
    result.register_agent("analysis-agent", "host", "10.0.0.8")
    return result


def _analyzing_task(repo: SqlRepository, collector_type: str = "perf_cpu"):
    task = repo.create_task(CreateTaskRequest(
        name="analysis-test",
        agent_id="analysis-agent",
        target_pid=123,
        collector_type=collector_type,
        duration_sec=5,
    ))
    repo.transition_task(task.id, TaskStatus.RUNNING, "claimed", Actor.AGENT)
    repo.transition_task(task.id, TaskStatus.UPLOADING, "uploaded", Actor.AGENT)
    repo.transition_task(task.id, TaskStatus.ANALYZING, "queued", Actor.SERVER)
    return task


def test_enqueue_is_idempotent_for_same_input(repo: SqlRepository):
    task = _analyzing_task(repo)
    checksum = artifact_input_checksum([{"artifact_type": "sys_metrics", "size_bytes": 10}])

    first = repo.enqueue_analysis_job(
        task.id,
        analyzer_type="artifact-set",
        analyzer_version="1.0.0",
        input_checksum=checksum,
    )
    second = repo.enqueue_analysis_job(
        task.id,
        analyzer_type="artifact-set",
        analyzer_version="1.0.0",
        input_checksum=checksum,
    )

    assert first.id == second.id
    assert len(repo.list_analysis_jobs(task_id=task.id)) == 1


def test_analyzer_registry_is_version_aware():
    registry = AnalyzerRegistry()
    registry.register(ArtifactSetAnalyzer())

    assert registry.resolve("artifact-set", "1.0.0").version == "1.0.0"
    with pytest.raises(LookupError, match="not registered"):
        registry.resolve("artifact-set", "2.0.0")


@pytest.mark.parametrize(
    ("collector_type", "artifact_type"),
    [
        ("ebpf_io", "ebpf_metrics"),
        ("pyspy", "flamegraph_svg"),
        ("continuous_perf", "continuous_summary"),
        ("java_async", "java_flamegraph_html"),
        ("go_pprof", "flamegraph_json"),
        ("memory_smaps", "memory_json"),
        ("sys_metrics", "sys_metrics"),
    ],
)
def test_each_collector_contract_has_a_versioned_analyzer(
    collector_type: str,
    artifact_type: str,
):
    contract = COLLECTOR_CONTRACTS[collector_type]
    handler = default_analyzer_registry().resolve(
        contract.analyzer_type,
        contract.analyzer_version,
    )

    output = handler.analyze(
        "task-contract",
        [{"id": 7, "artifact_type": artifact_type}],
    )

    assert output.existing_artifact_ids == [7]
    assert collector_type in handler.analyzer_type


def test_collector_contract_rejects_wrong_artifact_type():
    contract = COLLECTOR_CONTRACTS["ebpf_io"]
    handler = default_analyzer_registry().resolve(
        contract.analyzer_type,
        contract.analyzer_version,
    )

    with pytest.raises(ArtifactContractError, match="契约外产物"):
        handler.analyze(
            "task-contract",
            [{"id": 7, "artifact_type": "sys_metrics"}],
        )


def test_enqueue_uses_collector_specific_contract(repo: SqlRepository):
    task = _analyzing_task(repo, "sys_metrics")

    job = enqueue_artifact_analysis(
        repo,
        task.id,
        [{"artifact_type": "sys_metrics"}],
        collector_type="sys_metrics",
    )

    assert job.analyzer_type == "collector.sys_metrics"
    assert job.analyzer_version == "1.0.0"


def test_claim_uses_owner_lease_and_can_be_renewed(repo: SqlRepository):
    task = _analyzing_task(repo)
    job = repo.enqueue_analysis_job(
        task.id,
        analyzer_type="artifact-set",
        analyzer_version="1.0.0",
        input_checksum="a" * 64,
    )

    claimed = repo.claim_analysis_job("worker-a", lease_sec=30)

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "RUNNING"
    assert claimed.lease_owner == "worker-a"
    assert repo.claim_analysis_job("worker-b") is None
    assert repo.renew_analysis_job_lease(job.id, "worker-a", lease_sec=60) is True
    assert repo.renew_analysis_job_lease(job.id, "worker-b", lease_sec=60) is False


def test_worker_completes_analysis_ready_artifact_and_parent_task(repo: SqlRepository):
    task = _analyzing_task(repo)
    artifact_ids = repo.add_artifacts(task.id, [{
        "artifact_type": "sys_metrics",
        "object_key": "tasks/test/sys_metrics.json",
        "content_type": "application/json",
    }])
    repo.enqueue_analysis_job(
        task.id,
        analyzer_type="artifact-set",
        analyzer_version="1.0.0",
        input_checksum="b" * 64,
        input_artifact_ids=artifact_ids,
    )

    result = AnalysisWorker(repo, worker_id="worker-ready").process_once()

    assert result is not None
    assert result.status == "SUCCEEDED"
    assert repo.get_task(task.id).status == TaskStatus.DONE.value
    job = repo.get_analysis_job(result.job_id)
    assert job.output_artifact_ids_json == artifact_ids


def test_failure_retries_then_enters_dead_letter(repo: SqlRepository, monkeypatch):
    task = _analyzing_task(repo)
    repo.add_artifacts(task.id, [{
        "artifact_type": "raw",
        "filename": "perf.data",
        "local_path": "/outside/allowed/root/perf.data",
    }])
    job = repo.enqueue_analysis_job(
        task.id,
        analyzer_type="artifact-set",
        analyzer_version="1.0.0",
        input_checksum="c" * 64,
        max_retries=1,
    )
    monkeypatch.setenv("MINI_DROP_ANALYZER_RETRY_DELAY_SEC", "0")
    worker = AnalysisWorker(repo, worker_id="worker-fail")

    first = worker.process_once()
    second = worker.process_once()

    assert first is not None and first.status == "RETRYING"
    assert second is not None and second.status == "DEAD_LETTER"
    assert repo.get_analysis_job(job.id).retry_count == 2
    failed_task = repo.get_task(task.id)
    assert failed_task.status == TaskStatus.FAILED.value
    assert failed_task.collection_status == "SUCCEEDED"
    assert failed_task.analysis_status == "FAILED"
    assert repo.get_task_attempts(task.id)[0].status == "SUCCEEDED"


def test_expired_lease_is_recovered_by_another_worker(repo: SqlRepository):
    task = _analyzing_task(repo)
    job = repo.enqueue_analysis_job(
        task.id,
        analyzer_type="artifact-set",
        analyzer_version="1.0.0",
        input_checksum="d" * 64,
    )
    assert repo.claim_analysis_job("crashed-worker") is not None
    with new_session() as session:
        model = session.get(AnalysisJobModel, job.id)
        model.lease_expires_at = now_utc() - timedelta(seconds=1)
        session.commit()

    recovered = repo.claim_analysis_job("replacement-worker")

    assert recovered is not None
    assert recovered.id == job.id
    assert recovered.lease_owner == "replacement-worker"
    assert recovered.retry_count == 1
    assert recovered.error_code == "LEASE_EXPIRED"


class _SlowAnalyzer:
    analyzer_type = "slow-test"
    version = "1.0.0"

    def __init__(self, delay: float = 0.12) -> None:
        self.delay = delay

    def analyze(self, task_id: str, artifacts: list[dict]) -> AnalyzerOutput:
        time.sleep(self.delay)
        return AnalyzerOutput("slow analyzer completed", [], [artifacts[0]["id"]])


def _enqueue_slow_job(repo: SqlRepository):
    task = _analyzing_task(repo)
    artifact_ids = repo.add_artifacts(task.id, [{
        "artifact_type": "sys_metrics",
        "object_key": "tasks/slow/sys_metrics.json",
        "content_type": "application/json",
    }])
    job = repo.enqueue_analysis_job(
        task.id,
        analyzer_type="slow-test",
        analyzer_version="1.0.0",
        input_checksum="e" * 64,
        input_artifact_ids=artifact_ids,
    )
    registry = AnalyzerRegistry()
    registry.register(_SlowAnalyzer())
    return task, job, registry


def test_long_analysis_renews_lease_until_completion(
    repo: SqlRepository,
    monkeypatch,
):
    _, _, registry = _enqueue_slow_job(repo)
    original = repo.renew_analysis_job_lease
    renewals = []

    def tracked_renew(job_id, worker_id, *, lease_sec):
        renewals.append((job_id, worker_id))
        return original(job_id, worker_id, lease_sec=lease_sec)

    monkeypatch.setattr(repo, "renew_analysis_job_lease", tracked_renew)
    result = AnalysisWorker(
        repo,
        worker_id="slow-worker",
        registry=registry,
        heartbeat_interval_sec=0.02,
    ).process_once()

    assert result is not None and result.status == "SUCCEEDED"
    assert len(renewals) >= 2


def test_worker_abandons_completion_after_lease_loss(
    repo: SqlRepository,
    monkeypatch,
):
    task, job, registry = _enqueue_slow_job(repo)
    monkeypatch.setattr(repo, "renew_analysis_job_lease", lambda *args, **kwargs: False)

    result = AnalysisWorker(
        repo,
        worker_id="stale-worker",
        registry=registry,
        heartbeat_interval_sec=0.02,
    ).process_once()

    assert result is not None and result.status == "LEASE_LOST"
    assert repo.get_analysis_job(job.id).status == "RUNNING"
    assert repo.get_task(task.id).status == TaskStatus.ANALYZING.value
