from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
from threading import Barrier, Event, Lock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from server.app.database import init_db, reset_engine
from server.app.drop_insight import service as drop_insight_service
from server.app.drop_insight.service import (
    finalize_effective_time_range,
    open_effective_time_range,
)
from server.app.main import app
from server.app.models import (
    AgentModel,
    AnalysisJobModel,
    ArtifactModel,
    DropInsightEventModel,
    DropInsightHypothesisModel,
    DropInsightReportModel,
    DropInsightSessionModel,
    DropInsightToolCallModel,
    TaskAttemptModel,
    TaskModel,
)
from server.app.sql_repository import SqlRepository
from server.app.schemas import CreateTaskRequest
from server.app.state_machine import now_utc
from server.app.database import new_session


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    reset_engine()
    init_db()
    yield
    reset_engine()


@pytest.fixture
def file_backed_database(monkeypatch, tmp_path):
    reset_engine()
    monkeypatch.setenv(
        "DATABASE_URL",
        f"sqlite:///{tmp_path / 'report-effects.db'}",
    )
    init_db()
    yield
    reset_engine()


def create_diagnosis(
    client: TestClient,
    *,
    mode: str = "ASSISTED",
    time_range: dict | None = None,
    query: str = "order service CPU is high",
    budget: dict | None = None,
) -> str:
    _ensure_process_authority()
    response = client.post(
        "/api/v2/diagnoses",
        json={
            "query": query,
            "mode": mode,
            "target": {
                "service": "order-service",
                "environment": "staging",
            },
            "time_range": time_range or {
                "start": "2026-07-27T10:00:00Z",
                "end": "2026-07-27T10:05:00Z",
            },
            **({"budget": budget} if budget is not None else {}),
        },
    )
    assert response.status_code == 200
    created = response.json()["data"]
    discovery = client.get(
        f"/api/v2/diagnoses/{created['diagnosis_id']}/target-candidates"
    )
    assert discovery.status_code == 200
    authority = discovery.json()["data"]
    assert authority["status"] == "READY"
    clarified = client.post(
        f"/api/v2/diagnoses/{created['diagnosis_id']}/clarify",
        json={
            "expected_version": created["version"],
            "target": {
                "service": "order-service",
                "environment": "staging",
                "discovery_id": authority["discovery_id"],
                "binding_id": authority["candidates"][0]["binding_id"],
            },
        },
    )
    assert clarified.status_code == 200
    diagnosis_id = created["diagnosis_id"]
    session = new_session()
    persisted = session.get(DropInsightSessionModel, diagnosis_id)
    binding = dict((persisted.target_json or {})["process_binding"])
    for task in session.query(TaskModel).filter(
        TaskModel.agent_id == binding["agent_id"],
        TaskModel.target_pid == binding["pid"],
        TaskModel.process_binding_json.is_(None),
    ):
        request_params = dict(task.request_params or {})
        request_params["process_binding"] = binding
        task.request_params = request_params
        task.process_snapshot_id = binding["process_snapshot_id"]
        task.process_binding_json = binding
    session.commit()
    session.close()
    return diagnosis_id


def add_hypothesis(client: TestClient, diagnosis_id: str) -> str:
    response = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/hypotheses",
        json={
            "statement": "calculate_price consumes abnormal CPU",
            "expected_observations": ["perf samples concentrate in calculate_price"],
            "falsification_criteria": ["CPU samples remain evenly distributed"],
        },
    )
    assert response.status_code == 200
    return response.json()["data"]["hypothesis_id"]


def evidence_payload(hypothesis_id: str, evidence_id: str):
    return {
        "evidence_id": evidence_id,
        "hypothesis_id": hypothesis_id,
        "evidence_type": "PERF_HOT_FUNCTION",
        "observation": {
            "symbol": "calculate_price",
            "cpu_percent": 72.4,
        },
        "source_label": "manual-test-note",
    }


def _successful_analysis_job(
    *,
    task_id: str,
    attempt_id: str,
    artifact_id: int,
    timestamp: datetime,
) -> AnalysisJobModel:
    return AnalysisJobModel(
        id=f"analysis-{task_id}",
        task_id=task_id,
        task_attempt_id=attempt_id,
        analyzer_type="collector.perf_cpu",
        analyzer_version="1.2.0",
        input_checksum="c" * 64,
        input_artifact_ids_json=[],
        idempotency_key=f"analysis:{task_id}",
        status="SUCCEEDED",
        status_reason="analyzer verified output",
        retry_count=0,
        max_retries=3,
        next_run_at=timestamp,
        output_artifact_ids_json=[artifact_id],
        created_at=timestamp,
        updated_at=timestamp,
        started_at=timestamp,
        finished_at=timestamp,
    )


def _process_snapshot() -> dict:
    return {
        "generation": 1,
        "boot_id": "boot-a",
        "observed_at_unix_ms": 1,
        "complete": True,
        "truncated": False,
        "error": "",
        "candidates": [
            {
                "pid": 123,
                "process_start_ticks": 456,
                "pid_namespace_inode": 789,
                "namespace_pid": 123,
                "executable_identity": "sha256:order-service",
                "comm": "order-service",
                "cgroup": "/staging/order-service",
                "service_hint": "order-service",
                "instance_hint": "staging",
                "collector_capabilities": ["sys_metrics", "perf_cpu", "ebpf_io", "pyspy"],
            }
        ],
    }


def _ensure_process_authority() -> None:
    session = new_session()
    existing = session.get(AgentModel, "agent-a")
    timestamp = now_utc()
    if existing is None:
        session.close()
        _seed_online_agent(capabilities=["sys_metrics", "perf_cpu", "ebpf_io", "pyspy"])
    else:
        existing.status = "ONLINE"
        existing.last_heartbeat_at = timestamp
        existing.updated_at = timestamp
        session.commit()
        session.close()
    SqlRepository().record_process_candidate_snapshot(
        "agent-a",
        _process_snapshot(),
        received_at=now_utc(),
    )


def _seed_online_agent(
    *,
    agent_id: str = "agent-a",
    capabilities: list[str] | None = None,
) -> None:
    session = new_session()
    timestamp = now_utc()
    session.add(
        AgentModel(
            id=agent_id,
            hostname=f"{agent_id}-host",
            ip_addr="127.0.0.1",
            version="1.0",
            os_info="linux",
            capabilities=capabilities or ["perf_cpu"],
            status="ONLINE",
            last_heartbeat_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()


def _request_perf_tool_call(
    client: TestClient,
    diagnosis_id: str,
) -> dict:
    response = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls",
        json={
            "tool_name": "start_perf_profile",
            "arguments": {
                "agent_id": "agent-a",
                "pid": 123,
                "duration_seconds": 15,
                "sample_rate": 99,
            },
        },
    )
    assert response.status_code == 200
    return response.json()["data"]


def _approved_task_request(
    *,
    diagnosis_id: str,
    tool_call_id: str,
) -> dict:
    session = new_session()
    diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
    binding = dict((diagnosis.target_json or {})["process_binding"])
    session.close()
    return CreateTaskRequest(
        name="Drop Insight: start_perf_profile",
        agent_id=binding["agent_id"],
        target_pid=binding["pid"],
        collector_type="perf_cpu",
        sample_rate=99,
        duration_sec=15,
        options={
            "diagnosis_step_id": tool_call_id,
            "drop_insight_diagnosis_id": diagnosis_id,
            "drop_insight_tool_call_id": tool_call_id,
        },
        process_binding=binding,
    ).model_dump(mode="json")


def _seed_task_winner(
    *,
    diagnosis_id: str,
    tool_call_id: str,
    target_pid: int = 123,
) -> str:
    request = _approved_task_request(
        diagnosis_id=diagnosis_id,
        tool_call_id=tool_call_id,
    )
    request["target_pid"] = target_pid
    timestamp = now_utc()
    task_id = f"winner-{tool_call_id}"
    session = new_session()
    tool_call = session.get(DropInsightToolCallModel, tool_call_id)
    tool_call.status = "APPROVED"
    tool_call.approved_by = "test-reviewer"
    tool_call.approval_reason = "exercise database-winner recovery"
    tool_call.decided_at = timestamp
    session.add(
        TaskModel(
            id=task_id,
            name=request["name"],
            agent_id=request["agent_id"],
            target_pid=target_pid,
            collector_type=request["collector_type"],
            sample_rate=request["sample_rate"],
            duration_sec=request["duration_sec"],
            status="PENDING",
            status_reason="winning concurrent task",
            collection_status="QUEUED",
            analysis_status="NOT_STARTED",
            request_params=request,
            diagnosis_step_id=tool_call_id,
            process_snapshot_id=request["process_binding"]["process_snapshot_id"],
            process_binding_json=request["process_binding"],
            created_at=timestamp,
        )
    )
    session.commit()
    session.close()
    return task_id


def test_manual_evidence_cannot_self_assert_trust_or_complete_report():
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis_id = add_hypothesis(client, diagnosis_id)

    added = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/evidence",
        json=evidence_payload(hypothesis_id, "ev-1"),
    )
    assert added.status_code == 200
    added_data = added.json()["data"]
    assert added_data["role"] == "UNVERIFIED_EXTERNAL"
    assert added_data["classification"]["decision"] == "REJECT"
    assert added_data["classification"]["can_support_conclusion"] is False

    report = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/reports",
        json={
            "hypothesis_id": hypothesis_id,
        },
    )
    assert report.status_code == 200
    data = report.json()["data"]
    assert data["evidence_refs"] == []
    assert data["confidence"] == 0
    assert data["conclusion"].startswith("INSUFFICIENT_EVIDENCE")
    diagnosis = client.get(
        f"/api/v2/diagnoses/{diagnosis_id}"
    ).json()["data"]
    assert diagnosis["status"] == "HYPOTHESIZING"


def test_report_replay_ignores_stale_version_without_side_effects():
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis_id = add_hypothesis(client, diagnosis_id)

    before = client.get(f"/api/v2/diagnoses/{diagnosis_id}").json()["data"]
    first = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/reports",
        json={
            "expected_version": before["version"],
            "hypothesis_id": hypothesis_id,
        },
    )
    assert first.status_code == 200, first.text
    first_report = first.json()["data"]

    after_first = client.get(
        f"/api/v2/diagnoses/{diagnosis_id}"
    ).json()["data"]
    replay = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/reports",
        json={
            "expected_version": before["version"],
            "hypothesis_id": hypothesis_id,
        },
    )

    assert replay.status_code == 200, replay.text
    replayed_report = replay.json()["data"]
    assert replayed_report["report_id"] == first_report["report_id"]
    assert replayed_report["created_at"] == first_report["created_at"]

    session = new_session()
    try:
        assert session.query(DropInsightReportModel).filter(
            DropInsightReportModel.diagnosis_id == diagnosis_id,
            DropInsightReportModel.hypothesis_id == hypothesis_id,
        ).count() == 1
        assert session.query(DropInsightEventModel).filter(
            DropInsightEventModel.diagnosis_id == diagnosis_id,
            DropInsightEventModel.event_type == "report.generated",
        ).count() == 1
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        assert diagnosis.version == after_first["version"]
    finally:
        session.close()


def test_report_effect_failure_stays_pending_and_replay_resumes(monkeypatch):
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis_id = add_hypothesis(client, diagnosis_id)

    original = drop_insight_service._replan_after_insufficient_evidence
    attempts = 0

    def fail_once(
        current_diagnosis_id,
        current_hypothesis_id,
        current_report_id,
    ):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected report effect failure")
        return original(
            current_diagnosis_id,
            current_hypothesis_id,
            current_report_id,
        )

    monkeypatch.setattr(
        drop_insight_service,
        "_replan_after_insufficient_evidence",
        fail_once,
    )

    with pytest.raises(RuntimeError, match="injected report effect failure"):
        drop_insight_service.generate_report(
            diagnosis_id,
            drop_insight_service.GenerateReportRequest(
                hypothesis_id=hypothesis_id,
            ),
        )

    session = new_session()
    try:
        pending = session.query(DropInsightReportModel).filter(
            DropInsightReportModel.diagnosis_id == diagnosis_id,
            DropInsightReportModel.hypothesis_id == hypothesis_id,
        ).one()
        report_id = pending.id
        created_at = pending.created_at
        assert pending.effects_status == "PENDING"
        assert pending.effects_applied_at is None
    finally:
        session.close()

    replayed = drop_insight_service.generate_report(
        diagnosis_id,
        drop_insight_service.GenerateReportRequest(
            hypothesis_id=hypothesis_id,
        ),
    )

    assert replayed.id == report_id
    assert replayed.created_at == created_at
    assert replayed.effects_status == "APPLIED"
    assert replayed.effects_applied_at is not None
    assert attempts == 2

    applied_at = replayed.effects_applied_at
    replayed_again = drop_insight_service.generate_report(
        diagnosis_id,
        drop_insight_service.GenerateReportRequest(
            expected_version=1,
            hypothesis_id=hypothesis_id,
        ),
    )
    assert replayed_again.effects_applied_at == applied_at
    assert attempts == 2


def test_report_effect_claim_allows_only_one_simultaneous_executor(
    monkeypatch,
    file_backed_database,
):
    _seed_online_agent(capabilities=["sys_metrics"])
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis_id = add_hypothesis(client, diagnosis_id)

    original_claim = drop_insight_service._claim_report_effects
    claim_barrier = Barrier(2)
    release_executor = Event()
    entered = 0
    entered_lock = Lock()

    def coordinated_claim(report_id, owner):
        report, claimed = original_claim(report_id, owner)
        claim_barrier.wait(timeout=5)
        if claimed:
            release_executor.wait(timeout=5)
        return report, claimed

    original_replan = drop_insight_service._replan_after_insufficient_evidence

    def counted_replan(*args, **kwargs):
        nonlocal entered
        with entered_lock:
            entered += 1
        return original_replan(*args, **kwargs)

    monkeypatch.setattr(
        drop_insight_service,
        "_claim_report_effects",
        coordinated_claim,
    )
    monkeypatch.setattr(
        drop_insight_service,
        "_replan_after_insufficient_evidence",
        counted_replan,
    )

    session = new_session()
    timestamp = now_utc()
    report = DropInsightReportModel(
        id="report-simultaneous-claim",
        diagnosis_id=diagnosis_id,
        hypothesis_id=hypothesis_id,
        conclusion="Evidence is insufficient.",
        confidence=0,
        evidence_refs_json=[],
        counter_evidence_refs_json=[],
        assumptions_json=[],
        limitations_json=[],
        next_actions_json=[],
        claims_json=[],
        verification_json={"status": "INSUFFICIENT_EVIDENCE"},
        effects_status="PENDING",
        effects_fencing_token=0,
        created_at=timestamp,
    )
    session.add(report)
    session.commit()
    report_id = report.id
    session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(drop_insight_service._apply_report_effects, report_id)
            for _ in range(2)
        ]
        release_executor.set()
        results = [future.result(timeout=10) for future in futures]

    assert entered == 1
    assert {item.effects_status for item in results} == {"APPLYING", "APPLIED"}
    session = new_session()
    applied = session.get(DropInsightReportModel, report_id)
    assert applied.effects_status == "APPLIED"
    assert applied.effects_phase == "EFFECTS_COMPLETED"
    assert applied.effects_owner is None
    assert applied.effects_lease_expires_at is None
    assert applied.effects_fencing_token == 1
    assert applied.effects_applied_at is not None
    for suffix, model in (
        ("hypothesis", DropInsightHypothesisModel),
        ("tool_call", DropInsightToolCallModel),
        ("event", DropInsightEventModel),
    ):
        assert session.query(model).filter(
            model.diagnosis_id == diagnosis_id,
            model.effect_key
            == f"report:{report_id}:insufficient:{suffix}",
        ).count() == 1
    session.close()


def test_verified_report_effect_claim_records_route_once_under_contention(
    monkeypatch,
    file_backed_database,
):
    _seed_online_agent(capabilities=["sys_metrics"])
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis_id = add_hypothesis(client, diagnosis_id)
    requested = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls",
        json={
            "hypothesis_id": hypothesis_id,
            "tool_name": "collect_sys_metrics",
            "arguments": {
                "agent_id": "agent-a",
                "pid": 123,
                "duration_seconds": 15,
            },
        },
    )
    assert requested.status_code == 200, requested.text
    tool_call_id = requested.json()["data"]["tool_call_id"]

    session = new_session()
    completed_call = session.get(DropInsightToolCallModel, tool_call_id)
    completed_call.status = "COMPLETED"
    timestamp = now_utc()
    report = DropInsightReportModel(
        id="report-simultaneous-verified-route",
        diagnosis_id=diagnosis_id,
        hypothesis_id=hypothesis_id,
        conclusion="The verified route supports the hypothesis.",
        confidence=1000,
        evidence_refs_json=["evidence-support"],
        counter_evidence_refs_json=["evidence-control"],
        assumptions_json=[],
        limitations_json=[],
        next_actions_json=[],
        claims_json=[],
        verification_json={"status": "VERIFIED"},
        effects_status="PENDING",
        effects_fencing_token=0,
        created_at=timestamp,
    )
    session.add(report)
    session.commit()
    report_id = report.id
    session.close()

    original_claim = drop_insight_service._claim_report_effects
    claim_barrier = Barrier(2)
    release_executor = Event()

    def coordinated_claim(candidate_report_id, owner):
        claimed_report, claimed = original_claim(candidate_report_id, owner)
        claim_barrier.wait(timeout=5)
        if claimed:
            release_executor.wait(timeout=5)
        return claimed_report, claimed

    original_record = drop_insight_service._record_successful_route
    entered = 0
    entered_lock = Lock()

    def counted_record(*args, **kwargs):
        nonlocal entered
        with entered_lock:
            entered += 1
        return original_record(*args, **kwargs)

    monkeypatch.setattr(
        drop_insight_service,
        "_claim_report_effects",
        coordinated_claim,
    )
    monkeypatch.setattr(
        drop_insight_service,
        "_record_successful_route",
        counted_record,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(drop_insight_service._apply_report_effects, report_id)
            for _ in range(2)
        ]
        release_executor.set()
        results = [future.result(timeout=10) for future in futures]

    assert entered == 1
    assert {item.effects_status for item in results} == {"APPLYING", "APPLIED"}
    session = new_session()
    applied = session.get(DropInsightReportModel, report_id)
    assert applied.effects_status == "APPLIED"
    assert applied.effects_phase == "EFFECTS_COMPLETED"
    assert applied.effects_fencing_token == 1
    route_events = session.query(DropInsightEventModel).filter(
        DropInsightEventModel.diagnosis_id == diagnosis_id,
        DropInsightEventModel.effect_key == f"report:{report_id}:route:event",
    ).all()
    assert len(route_events) == 1
    assert route_events[0].payload_json["tool_route"] == ["collect_sys_metrics"]
    session.close()


def test_report_effect_fencing_rejects_stale_owner(file_backed_database):
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis_id = add_hypothesis(client, diagnosis_id)
    session = new_session()
    report = DropInsightReportModel(
        id="report-stale-owner",
        diagnosis_id=diagnosis_id,
        hypothesis_id=hypothesis_id,
        conclusion="Evidence is insufficient.",
        confidence=0,
        evidence_refs_json=[],
        counter_evidence_refs_json=[],
        assumptions_json=[],
        limitations_json=[],
        next_actions_json=[],
        claims_json=[],
        verification_json={"status": "INSUFFICIENT_EVIDENCE"},
        effects_status="PENDING",
        effects_fencing_token=0,
        created_at=now_utc(),
    )
    session.add(report)
    session.commit()
    session.close()

    first, claimed = drop_insight_service._claim_report_effects(
        report.id,
        "owner-a",
    )
    assert claimed is True
    first_token = first.effects_fencing_token
    session = new_session()
    current = session.get(DropInsightReportModel, report.id)
    current.effects_lease_expires_at = now_utc() - timedelta(seconds=1)
    session.commit()
    session.close()

    second, claimed = drop_insight_service._claim_report_effects(
        report.id,
        "owner-b",
    )
    assert claimed is True
    assert second.effects_fencing_token == first_token + 1
    with pytest.raises(drop_insight_service._StaleReportEffectAuthority):
        drop_insight_service._renew_report_effect_lease(
            report.id,
            "owner-a",
            first_token,
        )
    with pytest.raises(drop_insight_service._StaleReportEffectAuthority):
        drop_insight_service._start_report_effect_execution(
            report.id,
            "owner-a",
            first_token,
        )
    with pytest.raises(drop_insight_service._StaleReportEffectAuthority):
        drop_insight_service._complete_report_effects(
            report.id,
            "owner-a",
            first_token,
        )
    drop_insight_service._release_report_effects(
        report.id,
        "owner-a",
        first_token,
    )
    session = new_session()
    current = session.get(DropInsightReportModel, report.id)
    assert current.effects_status == "APPLYING"
    assert current.effects_owner == "owner-b"
    assert current.effects_fencing_token == second.effects_fencing_token
    session.close()
    completed = drop_insight_service._complete_report_effects(
        report.id,
        "owner-b",
        second.effects_fencing_token,
    )
    assert completed.effects_status == "APPLIED"


def test_expired_started_report_reconciles_without_model_call(
    monkeypatch,
    file_backed_database,
):
    _seed_online_agent(capabilities=["sys_metrics"])
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis_id = add_hypothesis(client, diagnosis_id)
    timestamp = now_utc()
    report_id = "report-expired-reconciliation"
    hypothesis_key = f"report:{report_id}:insufficient:hypothesis"
    session = new_session()
    session.add(
        DropInsightReportModel(
            id=report_id,
            diagnosis_id=diagnosis_id,
            hypothesis_id=hypothesis_id,
            conclusion="Evidence is insufficient.",
            confidence=0,
            evidence_refs_json=[],
            counter_evidence_refs_json=[],
            assumptions_json=[],
            limitations_json=[],
            next_actions_json=[],
            claims_json=[],
            verification_json={"status": "INSUFFICIENT_EVIDENCE"},
            effects_status="APPLYING",
            effects_phase="EXECUTION_STARTED",
            effects_owner="owner-a",
            effects_lease_expires_at=timestamp - timedelta(seconds=1),
            effects_fencing_token=1,
            created_at=timestamp,
        )
    )
    session.add(
        DropInsightHypothesisModel(
            id="hyp-reconciled",
            diagnosis_id=diagnosis_id,
            parent_hypothesis_id=hypothesis_id,
            effect_key=hypothesis_key,
            statement="Collect sys metrics to test the next evidence domain.",
            expected_observations_json=["sys metrics provide discriminating evidence"],
            falsification_criteria_json=["sys metrics do not discriminate"],
            status="OPEN",
            source="DETERMINISTIC_RULE",
            round_index=2,
            generation_reason="report_reconciliation",
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()

    def unexpected_model_call(*args, **kwargs):
        raise AssertionError("reconciliation must not call the model")

    monkeypatch.setattr(
        drop_insight_service,
        "propose_hypothesis_plan",
        unexpected_model_call,
    )
    replayed = drop_insight_service._apply_report_effects(report_id)

    assert replayed.effects_status == "APPLIED"
    assert replayed.effects_phase == "EFFECTS_COMPLETED"
    assert replayed.effects_fencing_token == 2
    session = new_session()
    assert session.query(DropInsightHypothesisModel).filter(
        DropInsightHypothesisModel.diagnosis_id == diagnosis_id,
        DropInsightHypothesisModel.effect_key == hypothesis_key,
    ).count() == 1
    assert session.query(DropInsightToolCallModel).filter(
        DropInsightToolCallModel.diagnosis_id == diagnosis_id,
        DropInsightToolCallModel.effect_key
        == f"report:{report_id}:insufficient:tool_call",
    ).count() == 1
    assert session.query(DropInsightEventModel).filter(
        DropInsightEventModel.diagnosis_id == diagnosis_id,
        DropInsightEventModel.effect_key
        == f"report:{report_id}:insufficient:event",
    ).count() == 1
    session.close()


@pytest.mark.parametrize(
    "crash_after",
    ["hypothesis", "tool_call", "planner_event"],
)
def test_insufficient_report_effect_replay_repairs_only_missing_stages(
    monkeypatch,
    crash_after,
):
    _seed_online_agent(capabilities=["sys_metrics"])
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis_id = add_hypothesis(client, diagnosis_id)
    crashed = False

    if crash_after == "hypothesis":
        original = drop_insight_service.create_hypothesis

        def create_then_crash(*args, **kwargs):
            nonlocal crashed
            result = original(*args, **kwargs)
            if not crashed and kwargs.get("effect_key"):
                crashed = True
                raise RuntimeError("crash after hypothesis")
            return result

        monkeypatch.setattr(
            drop_insight_service,
            "create_hypothesis",
            create_then_crash,
        )
    elif crash_after == "tool_call":
        original = drop_insight_service.request_tool_call

        def request_then_crash(*args, **kwargs):
            nonlocal crashed
            result = original(*args, **kwargs)
            if not crashed and kwargs.get("effect_key"):
                crashed = True
                raise RuntimeError("crash after tool call")
            return result

        monkeypatch.setattr(
            drop_insight_service,
            "request_tool_call",
            request_then_crash,
        )
    else:
        original = drop_insight_service._replan_after_insufficient_evidence

        def replan_then_crash(*args, **kwargs):
            nonlocal crashed
            result = original(*args, **kwargs)
            if not crashed:
                crashed = True
                raise RuntimeError("crash after planner event")
            return result

        monkeypatch.setattr(
            drop_insight_service,
            "_replan_after_insufficient_evidence",
            replan_then_crash,
        )

    with pytest.raises(RuntimeError, match=f"crash after {crash_after.replace('_', ' ')}"):
        drop_insight_service.generate_report(
            diagnosis_id,
            drop_insight_service.GenerateReportRequest(
                hypothesis_id=hypothesis_id,
            ),
        )

    session = new_session()
    pending = session.query(DropInsightReportModel).filter(
        DropInsightReportModel.diagnosis_id == diagnosis_id,
        DropInsightReportModel.hypothesis_id == hypothesis_id,
    ).one()
    report_id = pending.id
    assert pending.effects_status == "PENDING"
    session.close()

    replayed = drop_insight_service.generate_report(
        diagnosis_id,
        drop_insight_service.GenerateReportRequest(
            hypothesis_id=hypothesis_id,
        ),
    )
    applied_at = replayed.effects_applied_at
    replayed_again = drop_insight_service.generate_report(
        diagnosis_id,
        drop_insight_service.GenerateReportRequest(
            expected_version=1,
            hypothesis_id=hypothesis_id,
        ),
    )

    assert replayed.effects_status == "APPLIED"
    assert applied_at is not None
    assert replayed_again.effects_applied_at == applied_at
    session = new_session()
    hypothesis_key = f"report:{report_id}:insufficient:hypothesis"
    tool_call_key = f"report:{report_id}:insufficient:tool_call"
    event_key = f"report:{report_id}:insufficient:event"
    assert session.query(DropInsightHypothesisModel).filter(
        DropInsightHypothesisModel.diagnosis_id == diagnosis_id,
        DropInsightHypothesisModel.effect_key == hypothesis_key,
    ).count() == 1
    calls = session.query(DropInsightToolCallModel).filter(
        DropInsightToolCallModel.diagnosis_id == diagnosis_id,
        DropInsightToolCallModel.effect_key == tool_call_key,
    ).all()
    assert len(calls) == 1
    assert calls[0].budget_reservation_status == "RESERVED"
    assert calls[0].budget_reservation_json["duration_seconds"] == 15
    assert session.query(DropInsightEventModel).filter(
        DropInsightEventModel.diagnosis_id == diagnosis_id,
        DropInsightEventModel.effect_key == event_key,
    ).count() == 1
    assert session.query(TaskModel).filter(
        TaskModel.diagnosis_step_id == calls[0].id,
    ).count() == 1
    assert session.query(DropInsightEventModel).filter(
        DropInsightEventModel.diagnosis_id == diagnosis_id,
        DropInsightEventModel.effect_key
        == f"tool_call:{calls[0].id}:task_created",
    ).count() == 1
    session.close()


def test_semantic_tool_call_replay_reserves_budget_once():
    _seed_online_agent(capabilities=["sys_metrics"])
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis_id = add_hypothesis(client, diagnosis_id)
    effect_key = "report:semantic-replay:insufficient:tool_call"
    payload = drop_insight_service.CreateToolCallRequest(
        hypothesis_id=hypothesis_id,
        tool_name="collect_sys_metrics",
        arguments={
            "agent_id": "agent-a",
            "pid": 123,
            "duration_seconds": 15,
        },
    )

    first = drop_insight_service.request_tool_call(
        diagnosis_id,
        payload,
        effect_key=effect_key,
    )
    replayed = drop_insight_service.request_tool_call(
        diagnosis_id,
        payload,
        effect_key=effect_key,
    )

    assert replayed.id == first.id
    session = new_session()
    calls = session.query(DropInsightToolCallModel).filter(
        DropInsightToolCallModel.diagnosis_id == diagnosis_id,
        DropInsightToolCallModel.effect_key == effect_key,
    ).all()
    assert len(calls) == 1
    assert calls[0].budget_reservation_status == "RESERVED"
    assert calls[0].budget_reservation_json["duration_seconds"] == 15
    assert session.query(TaskModel).filter(
        TaskModel.diagnosis_step_id == first.id,
    ).count() == 1
    requested_events = session.query(DropInsightEventModel).filter(
        DropInsightEventModel.diagnosis_id == diagnosis_id,
        DropInsightEventModel.event_type == "tool_call.requested",
    ).all()
    assert sum(
        event.payload_json.get("tool_call_id") == first.id
        for event in requested_events
    ) == 1
    session.close()


def test_first_report_generation_rejects_stale_version():
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis_id = add_hypothesis(client, diagnosis_id)

    stale = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/reports",
        json={
            "expected_version": 1,
            "hypothesis_id": hypothesis_id,
        },
    )

    assert stale.status_code == 409
    assert "version conflict" in stale.json()["detail"]
    session = new_session()
    try:
        assert session.query(DropInsightReportModel).filter(
            DropInsightReportModel.diagnosis_id == diagnosis_id,
            DropInsightReportModel.hypothesis_id == hypothesis_id,
        ).count() == 0
        assert session.query(DropInsightEventModel).filter(
            DropInsightEventModel.diagnosis_id == diagnosis_id,
            DropInsightEventModel.event_type == "report.generated",
        ).count() == 0
    finally:
        session.close()


def test_client_cannot_submit_quality_conclusion_or_coverage():
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis_id = add_hypothesis(client, diagnosis_id)
    payload = evidence_payload(hypothesis_id, "ev-low")
    payload["quality"] = {"level": "HIGH", "sample_count": 9999}
    rejected_evidence = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/evidence", json=payload
    )
    assert rejected_evidence.status_code == 422

    report = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/reports",
        json={
            "hypothesis_id": hypothesis_id,
            "conclusion": "the hotspot is only a provisional possibility",
            "coverage_ratio": 1.0,
        },
    )
    assert report.status_code == 422


def test_tool_call_preview_uses_server_side_agent_capabilities_and_requires_approval():
    session = new_session()
    timestamp = now_utc()
    session.add(
        AgentModel(
            id="agent-a",
            hostname="worker-a",
            ip_addr="127.0.0.1",
            version="1.0",
            os_info="linux",
            capabilities=["perf_cpu", "sys_metrics"],
            status="ONLINE",
            last_heartbeat_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()

    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    response = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls/preview",
        json={
            "tool_name": "start_perf_profile",
            "arguments": {
                "agent_id": "agent-a",
                "pid": 123,
                "duration_seconds": 15,
                "sample_rate": 99,
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["decision"] == "REQUIRE_APPROVAL"


def test_tool_call_preview_denies_out_of_scope_agent():
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    response = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls/preview",
        json={
            "tool_name": "get_agent_status",
            "arguments": {"agent_id": "agent-b"},
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["decision"] == "DENY"


def seed_completed_task(
    *,
    task_id: str = "task-import",
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    top_function: str = "calculate_price",
    top_function_percent: float = 75.0,
):
    session = new_session()
    timestamp = started_at or datetime(2026, 7, 27, 10, 1, tzinfo=timezone.utc)
    finished_at = finished_at or datetime(2026, 7, 27, 10, 2, tzinfo=timezone.utc)
    attempt_id = "attempt-import" if task_id == "task-import" else f"attempt-{task_id}"
    if session.get(AgentModel, "agent-a") is None:
        session.add(
            AgentModel(
                id="agent-a",
                hostname="worker-a",
                ip_addr="127.0.0.1",
                version="1.0",
                os_info="linux",
                capabilities=["perf_cpu"],
                status="ONLINE",
                last_heartbeat_at=timestamp,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )
    session.add(
        TaskModel(
            id=task_id,
            name="CPU profile",
            agent_id="agent-a",
            target_pid=123,
            collector_type="perf",
            sample_rate=99,
            duration_sec=15,
            status="DONE",
            status_reason="analyzer completed",
            request_params={},
            created_at=timestamp,
            started_at=timestamp,
            finished_at=finished_at,
        )
    )
    session.add(
        TaskAttemptModel(
            id=attempt_id,
            task_id=task_id,
            attempt_no=1,
            agent_id="agent-a",
            status="DONE",
            reason="completed",
            metadata_json={},
            created_at=timestamp,
            started_at=timestamp,
            finished_at=finished_at,
        )
    )
    session.flush()
    artifact = ArtifactModel(
            task_id=task_id,
            artifact_type="flamegraph",
            bucket="mini-drop",
            object_key=f"{task_id}/flamegraph.json",
            content_type="application/json",
            size_bytes=4096,
            sha256="a" * 64,
            integrity_status="VERIFIED",
            integrity_reason="verified in test",
            meta_json={
                "sample_count": 1200,
                "analyzer_version": "1.2.0",
                "top_functions": [
                    {
                        "name": top_function,
                        "samples": 900,
                        "percent": top_function_percent,
                    }
                ],
            },
            created_at=timestamp,
        )
    session.add(artifact)
    session.flush()
    session.add(_successful_analysis_job(
        task_id=task_id,
        attempt_id=attempt_id,
        artifact_id=artifact.id,
        timestamp=finished_at,
    ))
    session.commit()
    session.close()


def test_import_completed_task_as_traceable_evidence_is_idempotent():
    seed_completed_task()
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis_id = add_hypothesis(client, diagnosis_id)
    body = {
        "task_id": "task-import",
        "hypothesis_id": hypothesis_id,
    }
    first = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/evidence/import-task",
        json=body,
    )
    after_first = client.get(
        f"/api/v2/diagnoses/{diagnosis_id}"
    ).json()["data"]
    second = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/evidence/import-task",
        json=body,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_item = first.json()["data"]["items"][0]
    second_item = second.json()["data"]["items"][0]
    assert first_item["evidence_id"] == second_item["evidence_id"]
    assert first_item["envelope"]["source"]["task_attempt_id"] == "attempt-import"
    assert first_item["envelope"]["source"]["artifact_sha256"] == "a" * 64
    assert first_item["envelope"]["source"]["analysis_job_id"]
    assert first_item["envelope"]["source"]["analyzer_output_schema_version"] == "1.0.0"
    assert first_item["envelope"]["source"]["observation_json_pointer"] == "/metadata"
    assert first_item["role"] == "SUPPORT"
    assert first_item["classification"]["decision"] == "ACCEPT_SUPPORT"
    predicate = first_item["envelope"]["observation"]["metadata"]["hypothesis_predicate"]
    assert predicate["outcome"] == "SUPPORT"
    assert predicate["version"] == "hypothesis-predicate-v2"
    evidence_items = client.get(
        f"/api/v2/diagnoses/{diagnosis_id}/evidence",
    ).json()["data"]["items"]
    assert len(evidence_items) == 1

    session = new_session()
    try:
        assert session.query(DropInsightEventModel).filter(
            DropInsightEventModel.diagnosis_id == diagnosis_id,
            DropInsightEventModel.event_type == "task_evidence.imported",
        ).count() == 1
        persisted = session.get(DropInsightSessionModel, diagnosis_id)
        assert persisted.version == after_first["version"]
    finally:
        session.close()


def test_import_ordinary_evidence_uses_requested_time_range():
    seed_completed_task()
    client = TestClient(app)
    requested_range = {
        "start": "2026-07-27T10:00:00Z",
        "end": "2026-07-27T10:05:00Z",
    }
    diagnosis_id = create_diagnosis(client, time_range=requested_range)
    hypothesis_id = add_hypothesis(client, diagnosis_id)

    response = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/evidence/import-task",
        json={"task_id": "task-import", "hypothesis_id": hypothesis_id},
    )

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["classification"]["decision"] == "ACCEPT_SUPPORT"
    assert item["envelope"]["quality"]["time_overlap"] is True
    detail = client.get(f"/api/v2/diagnoses/{diagnosis_id}").json()["data"]
    assert detail["requested_time_range"] == {
        **requested_range,
        "timezone": "Asia/Shanghai",
    }
    assert detail["effective_time_range"] == {}


def test_import_ordinary_evidence_rejects_outside_requested_time_range():
    seed_completed_task(
        started_at=datetime(2026, 7, 27, 10, 6, tzinfo=timezone.utc),
        finished_at=datetime(2026, 7, 27, 10, 7, tzinfo=timezone.utc),
    )
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis_id = add_hypothesis(client, diagnosis_id)

    response = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/evidence/import-task",
        json={"task_id": "task-import", "hypothesis_id": hypothesis_id},
    )

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["classification"]["decision"] == "REJECT"
    assert item["classification"]["can_support_conclusion"] is False
    assert item["envelope"]["quality"]["time_overlap"] is False


def test_reproduction_import_requires_finalized_effective_time_range():
    seed_completed_task()
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client, mode="REPRODUCTION")
    hypothesis_id = add_hypothesis(client, diagnosis_id)

    unopened = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/evidence/import-task",
        json={"task_id": "task-import", "hypothesis_id": hypothesis_id},
    )
    assert unopened.status_code == 409
    assert "effective live diagnosis time range must be finalized" in unopened.json()[
        "detail"
    ]

    open_effective_time_range(
        diagnosis_id,
        opened_at=datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc),
    )
    open_only = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/evidence/import-task",
        json={"task_id": "task-import", "hypothesis_id": hypothesis_id},
    )
    assert open_only.status_code == 409


def test_reproduction_import_uses_effective_not_requested_time_range():
    seed_completed_task(
        started_at=datetime(2026, 7, 27, 11, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 7, 27, 11, 2, tzinfo=timezone.utc),
    )
    client = TestClient(app)
    requested_range = {
        "start": "2026-07-27T09:00:00Z",
        "end": "2026-07-27T09:05:00Z",
    }
    diagnosis_id = create_diagnosis(
        client,
        mode="REPRODUCTION",
        time_range=requested_range,
    )
    hypothesis_id = add_hypothesis(client, diagnosis_id)
    open_effective_time_range(
        diagnosis_id,
        opened_at=datetime(2026, 7, 27, 11, 0, tzinfo=timezone.utc),
    )
    finalize_effective_time_range(
        diagnosis_id,
        observed_start=datetime(2026, 7, 27, 11, 1, tzinfo=timezone.utc),
        observed_end=datetime(2026, 7, 27, 11, 2, tzinfo=timezone.utc),
    )

    response = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/evidence/import-task",
        json={"task_id": "task-import", "hypothesis_id": hypothesis_id},
    )

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["classification"]["decision"] == "ACCEPT_SUPPORT"
    assert item["envelope"]["quality"]["time_overlap"] is True
    detail = client.get(f"/api/v2/diagnoses/{diagnosis_id}").json()["data"]
    assert detail["requested_time_range"] == {
        **requested_range,
        "timezone": "Asia/Shanghai",
    }
    assert detail["effective_time_range"]["start"] == "2026-07-27T11:01:00Z"
    assert detail["effective_time_range"]["end"] == "2026-07-27T11:02:00Z"


def test_report_requires_complete_criterion_coverage_for_verified_status():
    seed_completed_task(task_id="task-support", top_function="calculate_price")
    seed_completed_task(task_id="task-counter", top_function="readdir")
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/hypotheses",
        json={
            "statement": "calculate_price and checkout dominate CPU",
            "expected_observations": [
                "calculate_price appears in top functions",
                "checkout appears in top functions",
            ],
            "falsification_criteria": ["readdir appears in top functions"],
        },
    )
    assert hypothesis.status_code == 200
    hypothesis_id = hypothesis.json()["data"]["hypothesis_id"]
    for task_id in ("task-support", "task-counter"):
        imported = client.post(
            f"/api/v2/diagnoses/{diagnosis_id}/evidence/import-task",
            json={"task_id": task_id, "hypothesis_id": hypothesis_id},
        )
        assert imported.status_code == 200, imported.text

    report = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/reports",
        json={"hypothesis_id": hypothesis_id},
    )

    assert report.status_code == 200, report.text
    data = report.json()["data"]
    assert data["evidence_refs"]
    assert data["counter_evidence_refs"]
    assert data["verification"]["has_independent_counter_or_control"] is True
    assert data["verification"]["coverage_ratio"] < 1.0
    assert data["verification"]["status"] == "PARTIAL_WITHOUT_COUNTER"
    assert client.get(
        f"/api/v2/diagnoses/{diagnosis_id}"
    ).json()["data"]["status"] == "COLLECTING_EVIDENCE"


@pytest.mark.parametrize(
    "crash_after",
    ["hypothesis", "tool_call", "planner_event"],
)
def test_counter_report_effect_replay_repairs_only_missing_stages(
    monkeypatch,
    crash_after,
):
    seed_completed_task()
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/hypotheses",
        json={
            "statement": "readdir is the CPU hotspot",
            "expected_observations": ["readdir appears in top functions"],
            "falsification_criteria": [
                "calculate_price appears in top functions"
            ],
        },
    )
    assert hypothesis.status_code == 200
    hypothesis_id = hypothesis.json()["data"]["hypothesis_id"]
    imported = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/evidence/import-task",
        json={"task_id": "task-import", "hypothesis_id": hypothesis_id},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["data"]["items"][0]["role"] == "COUNTER"
    crashed = False

    if crash_after == "hypothesis":
        original = drop_insight_service.create_hypothesis

        def create_then_crash(*args, **kwargs):
            nonlocal crashed
            result = original(*args, **kwargs)
            if not crashed and kwargs.get("effect_key"):
                crashed = True
                raise RuntimeError("crash after hypothesis")
            return result

        monkeypatch.setattr(
            drop_insight_service,
            "create_hypothesis",
            create_then_crash,
        )
    elif crash_after == "tool_call":
        original = drop_insight_service.request_tool_call

        def request_then_crash(*args, **kwargs):
            nonlocal crashed
            result = original(*args, **kwargs)
            if not crashed and kwargs.get("effect_key"):
                crashed = True
                raise RuntimeError("crash after tool call")
            return result

        monkeypatch.setattr(
            drop_insight_service,
            "request_tool_call",
            request_then_crash,
        )
    else:
        original = drop_insight_service._replan_from_counter_evidence

        def replan_then_crash(*args, **kwargs):
            nonlocal crashed
            result = original(*args, **kwargs)
            if not crashed:
                crashed = True
                raise RuntimeError("crash after planner event")
            return result

        monkeypatch.setattr(
            drop_insight_service,
            "_replan_from_counter_evidence",
            replan_then_crash,
        )

    with pytest.raises(
        RuntimeError,
        match=f"crash after {crash_after.replace('_', ' ')}",
    ):
        drop_insight_service.generate_report(
            diagnosis_id,
            drop_insight_service.GenerateReportRequest(
                hypothesis_id=hypothesis_id,
            ),
        )

    session = new_session()
    pending = session.query(DropInsightReportModel).filter(
        DropInsightReportModel.diagnosis_id == diagnosis_id,
        DropInsightReportModel.hypothesis_id == hypothesis_id,
    ).one()
    report_id = pending.id
    assert pending.effects_status == "PENDING"
    session.close()

    replayed = drop_insight_service.generate_report(
        diagnosis_id,
        drop_insight_service.GenerateReportRequest(
            hypothesis_id=hypothesis_id,
        ),
    )
    applied_at = replayed.effects_applied_at
    replayed_again = drop_insight_service.generate_report(
        diagnosis_id,
        drop_insight_service.GenerateReportRequest(
            expected_version=1,
            hypothesis_id=hypothesis_id,
        ),
    )

    assert replayed.effects_status == "APPLIED"
    assert applied_at is not None
    assert replayed_again.effects_applied_at == applied_at
    session = new_session()
    hypothesis_key = f"report:{report_id}:counter:hypothesis"
    tool_call_key = f"report:{report_id}:counter:tool_call"
    event_key = f"report:{report_id}:counter:event"
    assert session.query(DropInsightHypothesisModel).filter(
        DropInsightHypothesisModel.diagnosis_id == diagnosis_id,
        DropInsightHypothesisModel.effect_key == hypothesis_key,
    ).count() == 1
    calls = session.query(DropInsightToolCallModel).filter(
        DropInsightToolCallModel.diagnosis_id == diagnosis_id,
        DropInsightToolCallModel.effect_key == tool_call_key,
    ).all()
    assert len(calls) == 1
    assert calls[0].budget_reservation_status == "RESERVED"
    assert calls[0].budget_reservation_json["duration_seconds"] == 15
    assert session.query(DropInsightEventModel).filter(
        DropInsightEventModel.diagnosis_id == diagnosis_id,
        DropInsightEventModel.effect_key == event_key,
    ).count() == 1
    assert session.query(TaskModel).filter(
        TaskModel.diagnosis_step_id == calls[0].id,
    ).count() == 1
    assert session.query(DropInsightEventModel).filter(
        DropInsightEventModel.diagnosis_id == diagnosis_id,
        DropInsightEventModel.effect_key
        == f"tool_call:{calls[0].id}:task_created",
    ).count() == 1
    session.close()


def test_verified_route_event_replay_is_exactly_once(monkeypatch):
    seed_completed_task(
        task_id="task-route-support",
        top_function="calculate_price",
    )
    seed_completed_task(
        task_id="task-route-counter",
        top_function="readdir",
    )
    session = new_session()
    agent = session.get(AgentModel, "agent-a")
    agent.capabilities = ["perf_cpu", "sys_metrics"]
    session.commit()
    session.close()

    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/hypotheses",
        json={
            "statement": "calculate_price is the CPU hotspot",
            "expected_observations": [
                "calculate_price appears in top functions"
            ],
            "falsification_criteria": [
                "readdir appears in top functions"
            ],
        },
    )
    assert hypothesis.status_code == 200
    hypothesis_id = hypothesis.json()["data"]["hypothesis_id"]
    requested = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls",
        json={
            "hypothesis_id": hypothesis_id,
            "tool_name": "collect_sys_metrics",
            "arguments": {
                "agent_id": "agent-a",
                "pid": 123,
                "duration_seconds": 15,
            },
        },
    )
    assert requested.status_code == 200, requested.text
    tool_call_id = requested.json()["data"]["tool_call_id"]
    session = new_session()
    completed_call = session.get(DropInsightToolCallModel, tool_call_id)
    assert completed_call.task_id is not None
    completed_call.status = "COMPLETED"
    task = session.get(TaskModel, completed_call.task_id)
    task.status = "DONE"
    task.status_reason = "collection completed"
    session.commit()
    session.close()

    for task_id in ("task-route-support", "task-route-counter"):
        imported = client.post(
            f"/api/v2/diagnoses/{diagnosis_id}/evidence/import-task",
            json={"task_id": task_id, "hypothesis_id": hypothesis_id},
        )
        assert imported.status_code == 200, imported.text

    original = drop_insight_service._record_successful_route
    crashed = False

    def record_then_crash(*args, **kwargs):
        nonlocal crashed
        result = original(*args, **kwargs)
        if not crashed:
            crashed = True
            raise RuntimeError("crash after route event")
        return result

    monkeypatch.setattr(
        drop_insight_service,
        "_record_successful_route",
        record_then_crash,
    )
    with pytest.raises(RuntimeError, match="crash after route event"):
        drop_insight_service.generate_report(
            diagnosis_id,
            drop_insight_service.GenerateReportRequest(
                hypothesis_id=hypothesis_id,
            ),
        )

    session = new_session()
    pending = session.query(DropInsightReportModel).filter(
        DropInsightReportModel.diagnosis_id == diagnosis_id,
        DropInsightReportModel.hypothesis_id == hypothesis_id,
    ).one()
    report_id = pending.id
    assert pending.verification_json["status"] == "VERIFIED"
    assert pending.verification_json["coverage_ratio"] == 1.0
    assert pending.effects_status == "PENDING"
    route_key = f"report:{report_id}:route:event"
    first_event = session.query(DropInsightEventModel).filter(
        DropInsightEventModel.diagnosis_id == diagnosis_id,
        DropInsightEventModel.effect_key == route_key,
    ).one()
    first_payload = first_event.payload_json
    assert first_payload["tool_route"] == ["collect_sys_metrics"]
    session.close()

    replayed = drop_insight_service.generate_report(
        diagnosis_id,
        drop_insight_service.GenerateReportRequest(
            hypothesis_id=hypothesis_id,
        ),
    )
    applied_at = replayed.effects_applied_at
    replayed_again = drop_insight_service.generate_report(
        diagnosis_id,
        drop_insight_service.GenerateReportRequest(
            hypothesis_id=hypothesis_id,
        ),
    )

    assert replayed.effects_status == "APPLIED"
    assert applied_at is not None
    assert replayed_again.effects_applied_at == applied_at
    session = new_session()
    events = session.query(DropInsightEventModel).filter(
        DropInsightEventModel.diagnosis_id == diagnosis_id,
        DropInsightEventModel.effect_key == route_key,
    ).all()
    assert len(events) == 1
    assert events[0].payload_json == first_payload
    session.close()


def test_import_evidence_counter_when_top_function_matches_falsification():
    seed_completed_task()
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/hypotheses",
        json={
            "statement": "readdir 系统调用是热点",
            "expected_observations": ["readdir 出现在 top functions"],
            "falsification_criteria": ["calculate_price 出现在 top functions"],
        },
    )
    assert hypothesis.status_code == 200
    hypothesis_id = hypothesis.json()["data"]["hypothesis_id"]
    response = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/evidence/import-task",
        json={
            "task_id": "task-import",
            "hypothesis_id": hypothesis_id,
        },
    )
    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    # The artifact's top function calculate_price falsifies this hypothesis.
    assert item["role"] == "COUNTER"
    assert item["classification"]["decision"] == "ACCEPT_COUNTER"
    predicate = item["envelope"]["observation"]["metadata"]["hypothesis_predicate"]
    assert predicate["outcome"] == "COUNTER"


def test_stale_session_version_is_rejected_with_conflict():
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    current = client.get(
        f"/api/v2/diagnoses/{diagnosis_id}"
    ).json()["data"]
    body = {
        "expected_version": current["version"],
        "statement": "CPU latency is caused by calculate_price hotspot",
        "expected_observations": ["calculate_price dominates CPU samples"],
        "falsification_criteria": ["calculate_price is absent from top functions"],
    }

    first = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/hypotheses", json=body
    )
    stale = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/hypotheses", json=body
    )

    assert first.status_code == 200
    assert stale.status_code == 409
    assert "version conflict" in stale.json()["detail"]


def test_import_rejects_running_task():
    seed_completed_task()
    session = new_session()
    task = session.get(TaskModel, "task-import")
    task.status = "RUNNING"
    session.commit()
    session.close()

    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis_id = add_hypothesis(client, diagnosis_id)
    response = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/evidence/import-task",
        json={
            "task_id": "task-import",
            "hypothesis_id": hypothesis_id,
        },
    )
    assert response.status_code == 409


def test_perf_tool_call_requires_approval_then_creates_real_task():
    session = new_session()
    timestamp = now_utc()
    session.add(
        AgentModel(
            id="agent-a",
            hostname="worker-a",
            ip_addr="127.0.0.1",
            version="1.0",
            os_info="linux",
            capabilities=["perf_cpu", "sys_metrics"],
            status="ONLINE",
            last_heartbeat_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()

    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    requested = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls",
        json={
            "tool_name": "start_perf_profile",
            "arguments": {
                "agent_id": "agent-a",
                "pid": 123,
                "duration_seconds": 15,
                "sample_rate": 99,
            },
        },
    )
    assert requested.status_code == 200
    pending = requested.json()["data"]
    assert pending["status"] == "PENDING_APPROVAL"
    assert pending["task_id"] is None
    assert pending["requested_by"] == "local-anonymous"

    decided = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls/{pending['tool_call_id']}/decision",
        json={
            "approved": True,
            "reason": "采样时长和目标范围符合本次诊断预算",
        },
    )
    assert decided.status_code == 200
    approved = decided.json()["data"]
    assert approved["status"] == "TASK_CREATED"
    assert approved["task_id"]
    assert approved["approved_by"] == "local-anonymous"

    session = new_session()
    task = session.get(TaskModel, approved["task_id"])
    assert task.collector_type == "perf_cpu"
    assert task.target_pid == 123
    assert task.diagnosis_step_id == pending["tool_call_id"]
    assert task.request_params["options"]["drop_insight_tool_call_id"] == pending["tool_call_id"]
    session.close()


def test_rejected_tool_call_never_creates_task():
    session = new_session()
    timestamp = now_utc()
    session.add(
        AgentModel(
            id="agent-a",
            hostname="worker-a",
            ip_addr="127.0.0.1",
            version="1.0",
            os_info="linux",
            capabilities=["perf_cpu"],
            status="ONLINE",
            last_heartbeat_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()

    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    requested = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls",
        json={
            "tool_name": "start_perf_profile",
            "arguments": {
                "agent_id": "agent-a",
                "pid": 123,
                "duration_seconds": 15,
                "sample_rate": 99,
            },
        },
    ).json()["data"]
    rejected = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls/{requested['tool_call_id']}/decision",
        json={
            "approved": False,
            "reason": "当前业务高峰期不执行主动采样",
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["data"]["status"] == "REJECTED"
    assert rejected.json()["data"]["task_id"] is None


def test_tool_call_recovers_matching_task_identity_winner():
    _seed_online_agent()
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    pending = _request_perf_tool_call(client, diagnosis_id)
    task_id = _seed_task_winner(
        diagnosis_id=diagnosis_id,
        tool_call_id=pending["tool_call_id"],
    )

    recovered = drop_insight_service._execute_approved_tool_call(
        pending["tool_call_id"]
    )

    assert recovered.status == "TASK_CREATED"
    assert recovered.task_id == task_id
    session = new_session()
    events = (
        session.query(DropInsightEventModel)
        .filter(
            DropInsightEventModel.diagnosis_id == diagnosis_id,
            DropInsightEventModel.effect_key
            == f"tool_call:{pending['tool_call_id']}:task_created",
        )
        .all()
    )
    assert len(events) == 1
    assert events[0].payload_json["task_id"] == task_id
    session.close()


def test_tool_call_rejects_task_identity_winner_with_mismatched_request():
    _seed_online_agent()
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    pending = _request_perf_tool_call(client, diagnosis_id)
    task_id = _seed_task_winner(
        diagnosis_id=diagnosis_id,
        tool_call_id=pending["tool_call_id"],
        target_pid=456,
    )

    with pytest.raises(
        ValueError,
        match="existing task does not match immutable request authority",
    ):
        drop_insight_service._execute_approved_tool_call(
            pending["tool_call_id"]
        )
    session = new_session()
    tool_call = session.get(
        DropInsightToolCallModel,
        pending["tool_call_id"],
    )
    assert tool_call.status == "APPROVED"
    assert tool_call.task_id is None
    assert session.get(TaskModel, task_id).target_pid == 456
    session.close()


def test_tool_call_propagates_unrelated_integrity_error(monkeypatch):
    _seed_online_agent()
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    pending = _request_perf_tool_call(client, diagnosis_id)
    task_id = _seed_task_winner(
        diagnosis_id=diagnosis_id,
        tool_call_id=pending["tool_call_id"],
    )

    def fail_with_unrelated_integrity(self, session, payload, **kwargs):
        task = session.get(TaskModel, task_id)
        task.diagnosis_step_id = None
        session.flush()
        try:
            raise sqlite3.IntegrityError(
                "UNIQUE constraint failed: outbox_messages.id"
            )
        except sqlite3.IntegrityError as cause:
            raise IntegrityError(
                "INSERT INTO outbox_messages ...",
                {},
                cause,
            ) from cause

    monkeypatch.setattr(
        SqlRepository,
        "create_task_in_session",
        fail_with_unrelated_integrity,
    )
    with pytest.raises(IntegrityError) as raised:
        drop_insight_service._execute_approved_tool_call(
            pending["tool_call_id"]
        )

    assert "outbox_messages.id" in str(raised.value.orig)
    session = new_session()
    tool_call = session.get(
        DropInsightToolCallModel,
        pending["tool_call_id"],
    )
    assert tool_call.status == "APPROVED"
    assert tool_call.task_id is None
    assert session.get(TaskModel, task_id).diagnosis_step_id == pending["tool_call_id"]
    session.close()


def test_tool_call_task_link_rolls_back_atomically(monkeypatch):
    session = new_session()
    timestamp = now_utc()
    session.add(
        AgentModel(
            id="agent-a",
            hostname="worker-a",
            ip_addr="127.0.0.1",
            version="1.0",
            os_info="linux",
            capabilities=["perf_cpu"],
            status="ONLINE",
            last_heartbeat_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    pending = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls",
        json={
            "tool_name": "start_perf_profile",
            "arguments": {
                "agent_id": "agent-a",
                "pid": 123,
                "duration_seconds": 15,
                "sample_rate": 99,
            },
        },
    ).json()["data"]

    def fail_audit(*args, **kwargs):
        raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(SqlRepository, "_write_audit", fail_audit)
    with pytest.raises(RuntimeError, match="injected transaction failure"):
        client.post(
            f"/api/v2/diagnoses/{diagnosis_id}/tool-calls/{pending['tool_call_id']}/decision",
            json={"approved": True, "reason": "exercise atomic rollback"},
        )

    session = new_session()
    tool_call = session.get(DropInsightToolCallModel, pending["tool_call_id"])
    orphan = session.query(TaskModel).filter(
        TaskModel.diagnosis_step_id == pending["tool_call_id"]
    ).first()
    assert tool_call.status == "APPROVED"
    assert tool_call.task_id is None
    assert orphan is None
    session.close()


def test_tool_actor_fields_cannot_be_forged_by_client():
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)

    requested = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls",
        json={
            "tool_name": "get_agent_status",
            "arguments": {"agent_id": "agent-a"},
            "requested_by": "forged-planner",
        },
    )
    planned = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/planner/run",
        json={"requested_by": "forged-planner"},
    )
    decided = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls/fake/decision",
        json={
            "approved": True,
            "decided_by": "forged-reviewer",
            "reason": "forged identity must be rejected",
        },
    )

    assert requested.status_code == 422
    assert planned.status_code == 422
    assert decided.status_code == 422


def test_planner_turns_natural_language_into_idempotent_hypothesis_and_tool_request():
    session = new_session()
    timestamp = now_utc()
    session.add(
        AgentModel(
            id="agent-a",
            hostname="worker-a",
            ip_addr="127.0.0.1",
            version="1.0",
            os_info="linux",
            capabilities=["perf_cpu", "ebpf_io", "pyspy", "sys_metrics"],
            status="ONLINE",
            last_heartbeat_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()

    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    first = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/planner/run",
        json={},
    )
    second = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/planner/run",
        json={},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    first_data = first.json()["data"]
    second_data = second.json()["data"]
    assert first_data["category"] == "CPU_HOTSPOT"
    assert first_data["tool_call"]["tool_name"] == "start_perf_profile"
    assert first_data["tool_call"]["status"] == "PENDING_APPROVAL"
    assert first_data["hypothesis"]["hypothesis_id"] == second_data["hypothesis"]["hypothesis_id"]
    assert first_data["tool_call"]["tool_call_id"] == second_data["tool_call"]["tool_call_id"]


def test_planner_keeps_unknown_query_out_of_cpu_fallback():
    client = TestClient(app)
    diagnosis_id = create_diagnosis(
        client,
        query="order service behaves strangely",
    )

    planned = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/planner/run",
        json={},
    )

    assert planned.status_code == 200
    data = planned.json()["data"]
    assert data["category"] == "UNKNOWN"
    assert data["status"] == "NEEDS_CLARIFICATION"
    assert data["hypothesis"] is None
    assert data["tool_call"] is None
    assert client.get(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls"
    ).json()["data"]["items"] == []


@pytest.mark.parametrize(
    ("query", "category"),
    [
        ("MySQL 出现数据库锁等待", "DATABASE_LOCK"),
        ("服务网络丢包并出现重传", "NETWORK_DEGRADATION"),
        ("Java 服务频繁 Full GC", "JVM_GC"),
        ("怀疑下游依赖服务变慢", "DOWNSTREAM_DEPENDENCY"),
        ("消息队列积压", "QUEUE_CONGESTION"),
        ("容器 CPU limit 导致 throttling", "CONTAINER_RESOURCE_LIMIT"),
        ("怀疑同宿主机噪声邻居", "NOISY_NEIGHBOR"),
    ],
)
def test_rules_v2_planner_exposes_domain_and_uses_low_risk_triage(query, category):
    session = new_session()
    timestamp = now_utc()
    session.add(
        AgentModel(
            id="agent-a",
            hostname="worker-a",
            ip_addr="127.0.0.1",
            version="1.0",
            os_info="linux",
            capabilities=["sys_metrics"],
            status="ONLINE",
            last_heartbeat_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client, query=query)

    result = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/planner/run", json={}
    ).json()["data"]

    assert result["planner_kind"] == "DETERMINISTIC_RULES"
    assert result["planner_version"] == "rules-v2"
    assert result["category"] == category
    assert result["tool_call"]["tool_name"] == "collect_sys_metrics"


def test_budget_usage_is_server_calculated_and_blocks_excess_tool_calls():
    session = new_session()
    timestamp = now_utc()
    session.add(
        AgentModel(
            id="agent-a",
            hostname="worker-a",
            ip_addr="127.0.0.1",
            version="1.0",
            os_info="linux",
            capabilities=["perf_cpu"],
            status="ONLINE",
            last_heartbeat_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()
    client = TestClient(app)
    diagnosis_id = create_diagnosis(
        client,
        budget={"max_tool_calls": 1},
    )
    request_body = {
        "tool_name": "start_perf_profile",
        "arguments": {
            "agent_id": "agent-a",
            "pid": 123,
            "duration_seconds": 15,
            "sample_rate": 99,
        },
    }
    first = client.post(f"/api/v2/diagnoses/{diagnosis_id}/tool-calls", json=request_body)
    second = client.post(f"/api/v2/diagnoses/{diagnosis_id}/tool-calls", json=request_body)
    assert first.json()["data"]["policy_decision"] == "REQUIRE_APPROVAL"
    assert second.json()["data"]["policy_decision"] == "DENY"
    usage = client.get(f"/api/v2/diagnoses/{diagnosis_id}/budget").json()["data"]
    assert usage["used"]["tool_calls"] == 2
    assert usage["remaining"]["tool_calls"] == 0


def test_duration_budget_is_reserved_before_task_creation():
    session = new_session()
    timestamp = now_utc()
    session.add(
        AgentModel(
            id="agent-a",
            hostname="worker-a",
            ip_addr="127.0.0.1",
            version="1.0",
            os_info="linux",
            capabilities=["perf_cpu", "sys_metrics"],
            status="ONLINE",
            last_heartbeat_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()
    client = TestClient(app)
    diagnosis_id = create_diagnosis(
        client,
        budget={
            "max_duration_seconds": 20,
            "max_tool_calls": 5,
            "max_concurrent_tasks": 3,
        },
    )

    first = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls",
        json={
            "tool_name": "start_perf_profile",
            "arguments": {
                "agent_id": "agent-a",
                "pid": 123,
                "duration_seconds": 15,
                "sample_rate": 99,
            },
        },
    ).json()["data"]
    second = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls",
        json={
            "tool_name": "collect_sys_metrics",
            "arguments": {
                "agent_id": "agent-a",
                "pid": 123,
                "duration_seconds": 10,
            },
        },
    ).json()["data"]

    assert first["policy_decision"] == "REQUIRE_APPROVAL"
    assert second["policy_decision"] == "DENY"
    duration_check = next(
        item for item in second["policy_checks"] if item["name"] == "BUDGET_DURATION"
    )
    assert duration_check == {
        "name": "BUDGET_DURATION",
        "result": "FAIL",
        "reserved": 25,
        "limit": 20,
    }


def test_orchestrator_converts_completed_tool_task_into_evidence_and_report():
    session = new_session()
    timestamp = now_utc()
    session.add(
        AgentModel(
            id="agent-a",
            hostname="worker-a",
            ip_addr="127.0.0.1",
            version="1.0",
            os_info="linux",
            capabilities=["perf_cpu"],
            status="ONLINE",
            last_heartbeat_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()
    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis_id = add_hypothesis(client, diagnosis_id)
    pending = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls",
        json={
            "hypothesis_id": hypothesis_id,
            "tool_name": "start_perf_profile",
            "arguments": {
                "agent_id": "agent-a",
                "pid": 123,
                "duration_seconds": 15,
                "sample_rate": 99,
            },
        },
    ).json()["data"]
    approved = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls/{pending['tool_call_id']}/decision",
        json={
            "approved": True,
            "reason": "approved for test",
        },
    ).json()["data"]
    task_id = approved["task_id"]

    started_at = datetime(2026, 7, 27, 10, 1, tzinfo=timezone.utc)
    finished_at = datetime(2026, 7, 27, 10, 2, tzinfo=timezone.utc)
    session = new_session()
    task = session.get(TaskModel, task_id)
    task.status = "DONE"
    task.status_reason = "analyzer completed"
    task.started_at = started_at
    task.finished_at = finished_at
    session.add(
        TaskAttemptModel(
            id="attempt-orchestrated",
            task_id=task_id,
            attempt_no=1,
            agent_id="agent-a",
            status="DONE",
            reason="completed",
            metadata_json={},
            created_at=started_at,
            started_at=started_at,
            finished_at=finished_at,
        )
    )
    session.flush()
    artifact = ArtifactModel(
            task_id=task_id,
            artifact_type="flamegraph",
            bucket="mini-drop",
            object_key=f"{task_id}/flamegraph.json",
            content_type="application/json",
            size_bytes=2048,
            sha256="b" * 64,
            integrity_status="VERIFIED",
            integrity_reason="verified in test",
            meta_json={
                "sample_count": 1500,
                "analyzer_version": "1.2.0",
                "top_functions": [
                    {"name": "calculate_price", "samples": 1200, "percent": 80.0}
                ],
            },
            created_at=finished_at,
        )
    session.add(artifact)
    session.flush()
    session.add(_successful_analysis_job(
        task_id=task_id,
        attempt_id="attempt-orchestrated",
        artifact_id=artifact.id,
        timestamp=finished_at,
    ))
    session.commit()
    session.close()

    advanced = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/orchestrator/advance",
    )
    assert advanced.status_code == 200, advanced.text
    actions = advanced.json()["data"]["actions"]
    assert actions[0]["action"] == "EVIDENCE_IMPORTED"
    assert actions[0]["evidence_refs"]
    assert actions[0]["report_id"]
    reports = client.get(
        f"/api/v2/diagnoses/{diagnosis_id}/reports",
    ).json()["data"]["items"]
    assert reports[0]["hypothesis_id"] == hypothesis_id
    assert reports[0]["evidence_refs"]
    assert reports[0]["confidence"] > 0
    assert reports[0]["claims"]
    assert reports[0]["verification"]["status"] == "PARTIAL_WITHOUT_COUNTER"
    assert reports[0]["verification"]["has_independent_counter_or_control"] is False
    diagnosis = client.get(f"/api/v2/diagnoses/{diagnosis_id}").json()["data"]
    assert diagnosis["status"] == "COLLECTING_EVIDENCE"

    first_version = diagnosis["version"]
    first_report = reports[0]
    replay = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/orchestrator/advance",
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["actions"] == []

    session = new_session()
    try:
        tool_call = session.get(DropInsightToolCallModel, pending["tool_call_id"])
        assert tool_call.terminal_processing_status == "REPORT_EFFECTS_DONE"
        assert tool_call.terminal_processed_at is not None
        assert session.query(DropInsightEventModel).filter(
            DropInsightEventModel.diagnosis_id == diagnosis_id,
            DropInsightEventModel.event_type == "tool_call.task_terminal",
        ).count() == 1
        assert session.query(DropInsightEventModel).filter(
            DropInsightEventModel.diagnosis_id == diagnosis_id,
            DropInsightEventModel.event_type == "task_evidence.imported",
        ).count() == 1
        assert session.query(DropInsightReportModel).filter(
            DropInsightReportModel.diagnosis_id == diagnosis_id,
            DropInsightReportModel.hypothesis_id == hypothesis_id,
        ).count() == 1
        persisted_report = session.get(
            DropInsightReportModel,
            first_report["report_id"],
        )
        assert persisted_report.created_at.isoformat() == first_report["created_at"]
        assert persisted_report.effects_status == "APPLIED"
        assert persisted_report.effects_applied_at is not None
        persisted_diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        assert persisted_diagnosis.version == first_version
    finally:
        session.close()


def test_orchestrator_records_failed_task_terminal_once():
    timestamp = now_utc()
    session = new_session()
    session.add(
        AgentModel(
            id="agent-a",
            hostname="worker-a",
            ip_addr="127.0.0.1",
            version="1.0",
            os_info="linux",
            capabilities=["perf_cpu"],
            status="ONLINE",
            last_heartbeat_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()

    client = TestClient(app)
    diagnosis_id = create_diagnosis(client)
    hypothesis_id = add_hypothesis(client, diagnosis_id)
    pending = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls",
        json={
            "hypothesis_id": hypothesis_id,
            "tool_name": "start_perf_profile",
            "arguments": {
                "agent_id": "agent-a",
                "pid": 123,
                "duration_seconds": 15,
                "sample_rate": 99,
            },
        },
    ).json()["data"]
    approved = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/tool-calls/{pending['tool_call_id']}/decision",
        json={"approved": True, "reason": "approved for test"},
    ).json()["data"]

    session = new_session()
    task = session.get(TaskModel, approved["task_id"])
    task.status = "FAILED"
    task.status_reason = "collector failed"
    session.commit()
    session.close()

    first = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/orchestrator/advance",
    )
    assert first.status_code == 200, first.text
    assert first.json()["data"]["actions"][0]["action"] == "FAILED"
    first_diagnosis = client.get(
        f"/api/v2/diagnoses/{diagnosis_id}"
    ).json()["data"]

    replay = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/orchestrator/advance",
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["actions"] == []

    session = new_session()
    try:
        tool_call = session.get(DropInsightToolCallModel, pending["tool_call_id"])
        assert tool_call.status == "FAILED"
        assert tool_call.budget_reservation_status == "RELEASED"
        assert tool_call.terminal_processing_status == "REPORT_EFFECTS_DONE"
        assert tool_call.terminal_processed_at is not None
        assert session.query(DropInsightEventModel).filter(
            DropInsightEventModel.diagnosis_id == diagnosis_id,
            DropInsightEventModel.event_type == "tool_call.task_terminal",
        ).count() == 1
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        assert diagnosis.version == first_diagnosis["version"]
    finally:
        session.close()


def test_clarify_fills_missing_scope_and_resumes():
    client = TestClient(app)
    # No service/time_range -> NEEDS_CLARIFICATION
    created = client.post(
        "/api/v2/diagnoses",
        json={"query": "服务 CPU 飙高"},
    )
    assert created.status_code == 200
    diagnosis_id = created.json()["data"]["diagnosis_id"]
    assert created.json()["data"]["status"] == "NEEDS_CLARIFICATION"

    _ensure_process_authority()
    discovery = client.get(
        f"/api/v2/diagnoses/{diagnosis_id}/target-candidates",
        params={"service": "order-service", "environment": "staging"},
    )
    assert discovery.status_code == 200, discovery.text
    authority = discovery.json()["data"]
    assert authority["status"] == "READY"
    clarified = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/clarify",
        json={
            "expected_version": created.json()["data"]["version"],
            "target": {
                "service": "order-service",
                "environment": "staging",
                "discovery_id": authority["discovery_id"],
                "binding_id": authority["candidates"][0]["binding_id"],
            },
            "time_range": {
                "start": "2026-08-05T10:00:00Z",
                "end": "2026-08-05T10:05:00Z",
            },
        },
    )
    assert clarified.status_code == 200, clarified.text
    data = clarified.json()["data"]
    assert data["status"] == "UNDERSTANDING"
    assert data["target"]["service"] == "order-service"
