"""AnalysisJob persistence, lease, retry and worker integration tests."""

from __future__ import annotations

from datetime import timedelta
import json
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
from server.app.artifact_contracts import (
    COLLECTOR_CONTRACTS,
    ArtifactContractError,
    ArtifactQualityError,
)
from server.app.analyzer_runner import AnalyzerQualityError
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


def _attempt(repo: SqlRepository, task_id: str):
    return repo.get_task_attempts(task_id)[-1]


def _add_attempt_artifacts(
    repo: SqlRepository,
    task_id: str,
    artifacts: list[dict],
):
    attempt = _attempt(repo, task_id)
    artifact_ids = repo.add_attempt_artifacts(task_id, attempt.id, artifacts)
    return attempt, artifact_ids


def test_enqueue_is_idempotent_for_same_input(repo: SqlRepository):
    task = _analyzing_task(repo)
    attempt, artifact_ids = _add_attempt_artifacts(
        repo,
        task.id,
        [{"artifact_type": "sys_metrics", "size_bytes": 10}],
    )
    checksum = artifact_input_checksum([{"artifact_type": "sys_metrics", "size_bytes": 10}])

    first = repo.enqueue_analysis_job(
        task.id,
        task_attempt_id=attempt.id,
        analyzer_type="artifact-set",
        analyzer_version="1.0.0",
        input_checksum=checksum,
        input_artifact_ids=artifact_ids,
    )
    second = repo.enqueue_analysis_job(
        task.id,
        task_attempt_id=attempt.id,
        analyzer_type="artifact-set",
        analyzer_version="1.0.0",
        input_checksum=checksum,
        input_artifact_ids=artifact_ids,
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
    monkeypatch,
):
    contract = COLLECTOR_CONTRACTS[collector_type]
    handler = default_analyzer_registry().resolve(
        contract.analyzer_type,
        contract.analyzer_version,
    )
    if collector_type == "java_async":
        monkeypatch.setattr(
            "server.app.analysis_jobs.analyze_java_flamegraph_artifacts",
            lambda *_args, **_kwargs: {
                7: {"sample_count": 120, "top_functions": [{"name": "Hotspot", "percent": 80.0}]}
            },
        )

    output = handler.analyze(
        "task-contract",
        [{"id": 7, "artifact_type": artifact_type}],
    )

    assert output.existing_artifact_ids == [7]
    assert collector_type in handler.analyzer_type


def test_analysis_job_can_enrich_existing_output_metadata(repo: SqlRepository):
    task = _analyzing_task(repo, "java_async")
    attempt, artifact_ids = _add_attempt_artifacts(
        repo,
        task.id,
        [{
            "artifact_type": "java_flamegraph_html",
            "object_key": f"tasks/{task.id}/java-flamegraph.html",
            "metadata": {"collector_plugin": "java_async"},
        }],
    )
    job = repo.enqueue_analysis_job(
        task.id,
        task_attempt_id=attempt.id,
        analyzer_type="collector.java_async",
        analyzer_version="1.0.0",
        input_checksum="7" * 64,
        input_artifact_ids=artifact_ids,
    )
    assert repo.claim_analysis_job("java-worker") is not None

    repo.complete_analysis_job(
        job.id,
        "java-worker",
        output_artifact_ids=artifact_ids,
        artifact_metadata_updates={
            artifact_ids[0]: {
                "sample_count": 120,
                "profile_event": "alloc",
                "top_functions": [{"name": "Hotspot.allocate", "percent": 75.0}],
            }
        },
    )

    artifact = repo.get_artifacts(task.id)[0]
    assert artifact["metadata"]["sample_count"] == 120
    assert artifact["metadata"]["profile_event"] == "alloc"


def test_attempt_manifest_is_transport_metadata_not_analyzer_input():
    contract = COLLECTOR_CONTRACTS["sys_metrics"]
    artifact_types = contract.validate([
        {"id": 7, "artifact_type": "sys_metrics"},
        {"id": 8, "artifact_type": "manifest"},
    ])

    assert artifact_types == {"sys_metrics", "manifest"}
    assert "manifest" not in contract.analysis_types


def test_profile_contract_rejects_explicit_zero_sample_analysis_artifact():
    contract = COLLECTOR_CONTRACTS["perf_cpu"]

    with pytest.raises(ArtifactQualityError) as exc:
        contract.validate([{
            "id": 7,
            "artifact_type": "flamegraph_json",
            "size_bytes": 36,
            "metadata": {
                "sample_count": 0,
                "profile_quality": {
                    "status": "UNUSABLE",
                    "reason_code": "NO_PROFILE_SAMPLES",
                },
            },
        }])

    payload = json.loads(str(exc.value))
    assert payload["failure_kind"] == "SAMPLE_QUALITY"
    assert payload["reason_code"] == "NO_PROFILE_SAMPLES"


def test_profile_contract_keeps_legacy_unknown_quality_compatible():
    contract = COLLECTOR_CONTRACTS["go_pprof"]

    assert contract.validate([{
        "id": 7,
        "artifact_type": "flamegraph_json",
    }]) == {"flamegraph_json"}


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
    attempt, artifact_ids = _add_attempt_artifacts(
        repo,
        task.id,
        [{"artifact_type": "sys_metrics"}],
    )

    job = enqueue_artifact_analysis(
        repo,
        task.id,
        attempt.id,
        [{"artifact_type": "sys_metrics"}],
        artifact_ids,
        collector_type="sys_metrics",
    )

    assert job.analyzer_type == "collector.sys_metrics"
    assert job.analyzer_version == "1.0.0"


def test_claim_uses_owner_lease_and_can_be_renewed(repo: SqlRepository):
    task = _analyzing_task(repo)
    attempt, artifact_ids = _add_attempt_artifacts(
        repo,
        task.id,
        [{"artifact_type": "sys_metrics"}],
    )
    job = repo.enqueue_analysis_job(
        task.id,
        task_attempt_id=attempt.id,
        analyzer_type="artifact-set",
        analyzer_version="1.0.0",
        input_checksum="a" * 64,
        input_artifact_ids=artifact_ids,
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
    attempt, artifact_ids = _add_attempt_artifacts(repo, task.id, [{
        "artifact_type": "sys_metrics",
        "object_key": "tasks/test/sys_metrics.json",
        "content_type": "application/json",
    }])
    repo.enqueue_analysis_job(
        task.id,
        task_attempt_id=attempt.id,
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
    attempt, artifact_ids = _add_attempt_artifacts(repo, task.id, [{
        "artifact_type": "raw",
        "filename": "perf.data",
        "local_path": "/outside/allowed/root/perf.data",
    }])
    job = repo.enqueue_analysis_job(
        task.id,
        task_attempt_id=attempt.id,
        analyzer_type="artifact-set",
        analyzer_version="1.0.0",
        input_checksum="c" * 64,
        input_artifact_ids=artifact_ids,
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
    assert failed_task.collection_status == "COLLECTED"
    assert failed_task.analysis_status == "FAILED"
    assert repo.get_task_attempts(task.id)[0].status == "SUCCEEDED"


class _EmptyProfileAnalyzer:
    analyzer_type = "empty-profile-test"
    version = "1.0.0"

    def analyze(self, task_id: str, artifacts: list[dict]) -> AnalyzerOutput:
        raise AnalyzerQualityError(
            "NO_PERF_SAMPLES",
            "perf.data contains no samples",
            "run the target under load and collect again",
            details={"sample_count": 0},
        )


def test_empty_profile_quality_failure_cannot_complete_parent_task(
    repo: SqlRepository,
    monkeypatch,
):
    task = _analyzing_task(repo)
    attempt, artifact_ids = _add_attempt_artifacts(repo, task.id, [{
        "artifact_type": "raw",
        "filename": "perf.data",
        "object_key": "tasks/empty/perf.data",
    }])
    job = repo.enqueue_analysis_job(
        task.id,
        task_attempt_id=attempt.id,
        analyzer_type="empty-profile-test",
        analyzer_version="1.0.0",
        input_checksum="f" * 64,
        input_artifact_ids=artifact_ids,
        max_retries=0,
    )
    registry = AnalyzerRegistry()
    registry.register(_EmptyProfileAnalyzer())
    monkeypatch.setenv("MINI_DROP_ANALYZER_RETRY_DELAY_SEC", "0")

    result = AnalysisWorker(
        repo,
        worker_id="empty-profile-worker",
        registry=registry,
    ).process_once()

    assert result is not None and result.status == "DEAD_LETTER"
    failed_job = repo.get_analysis_job(job.id)
    assert failed_job.error_code == "ANALYSIS_INPUT_INVALID"
    assert json.loads(failed_job.error_message)["reason_code"] == "NO_PERF_SAMPLES"
    failed_task = repo.get_task(task.id)
    assert failed_task.status == TaskStatus.FAILED.value
    assert failed_task.collection_status == "COLLECTED"
    assert failed_task.analysis_status == "FAILED"


def test_expired_lease_is_recovered_by_another_worker(repo: SqlRepository):
    task = _analyzing_task(repo)
    attempt, artifact_ids = _add_attempt_artifacts(
        repo,
        task.id,
        [{"artifact_type": "sys_metrics"}],
    )
    job = repo.enqueue_analysis_job(
        task.id,
        task_attempt_id=attempt.id,
        analyzer_type="artifact-set",
        analyzer_version="1.0.0",
        input_checksum="d" * 64,
        input_artifact_ids=artifact_ids,
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
    attempt, artifact_ids = _add_attempt_artifacts(repo, task.id, [{
        "artifact_type": "sys_metrics",
        "object_key": "tasks/slow/sys_metrics.json",
        "content_type": "application/json",
    }])
    job = repo.enqueue_analysis_job(
        task.id,
        task_attempt_id=attempt.id,
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
