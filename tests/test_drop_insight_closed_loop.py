from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app
from server.app.models import (
    AgentModel,
    AnalysisJobModel,
    ArtifactModel,
    DropInsightToolCallModel,
    TaskAttemptModel,
    TaskModel,
)
from server.app.sql_repository import SqlRepository
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


def create_diagnosis(client: TestClient) -> str:
    response = client.post(
        "/api/v2/diagnoses",
        json={
            "query": "order service CPU is high",
            "target": {
                "service": "order-service",
                "environment": "staging",
                "agent_id": "agent-a",
                "pid": 123,
            },
            "time_range": {
                "start": "2026-07-27T10:00:00Z",
                "end": "2026-07-27T10:05:00Z",
            },
        },
    )
    return response.json()["data"]["diagnosis_id"]


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
    assert client.get(f"/api/v2/diagnoses/{diagnosis_id}").json()["data"]["status"] == "INSUFFICIENT_EVIDENCE"


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


def seed_completed_task():
    session = new_session()
    timestamp = datetime(2026, 7, 27, 10, 1, tzinfo=timezone.utc)
    finished_at = datetime(2026, 7, 27, 10, 2, tzinfo=timezone.utc)
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
            id="task-import",
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
            id="attempt-import",
            task_id="task-import",
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
            task_id="task-import",
            artifact_type="flamegraph",
            bucket="mini-drop",
            object_key="task-import/flamegraph.json",
            content_type="application/json",
            size_bytes=4096,
            sha256="a" * 64,
            integrity_status="VERIFIED",
            integrity_reason="verified in test",
            meta_json={
                "sample_count": 1200,
                "analyzer_version": "1.2.0",
                "top_functions": [
                    {"name": "calculate_price", "samples": 900, "percent": 75.0}
                ],
            },
            created_at=timestamp,
        )
    session.add(artifact)
    session.flush()
    session.add(_successful_analysis_job(
        task_id="task-import",
        attempt_id="attempt-import",
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
    body = {
        "expected_version": 1,
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
    response = client.post(
        "/api/v2/diagnoses",
        json={
            "query": "服务看起来有点奇怪，帮我看看",
            "target": {
                "service": "order-service",
                "environment": "staging",
                "agent_id": "agent-a",
                "pid": 123,
            },
            "time_range": {
                "start": "2026-07-27T10:00:00Z",
                "end": "2026-07-27T10:05:00Z",
            },
        },
    )
    diagnosis_id = response.json()["data"]["diagnosis_id"]

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
    diagnosis_id = client.post(
        "/api/v2/diagnoses",
        json={
            "query": query,
            "target": {
                "service": "order-service",
                "environment": "staging",
                "agent_id": "agent-a",
                "pid": 123,
            },
            "time_range": {
                "start": "2026-07-27T10:00:00Z",
                "end": "2026-07-27T10:05:00Z",
            },
        },
    ).json()["data"]["diagnosis_id"]

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
    created = client.post(
        "/api/v2/diagnoses",
        json={
            "query": "CPU is high",
            "target": {
                "service": "order-service",
                "environment": "staging",
                "agent_id": "agent-a",
                "pid": 123,
            },
            "time_range": {
                "start": "2026-07-27T10:00:00Z",
                "end": "2026-07-27T10:05:00Z",
            },
            "budget": {"max_tool_calls": 1},
        },
    ).json()["data"]
    diagnosis_id = created["diagnosis_id"]
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
    diagnosis_id = client.post(
        "/api/v2/diagnoses",
        json={
            "query": "CPU is high",
            "target": {
                "service": "order-service",
                "environment": "staging",
                "agent_id": "agent-a",
                "pid": 123,
            },
            "time_range": {
                "start": "2026-07-27T10:00:00Z",
                "end": "2026-07-27T10:05:00Z",
            },
            "budget": {
                "max_duration_seconds": 20,
                "max_tool_calls": 5,
                "max_concurrent_tasks": 3,
            },
        },
    ).json()["data"]["diagnosis_id"]

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

    clarified = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/clarify",
        json={
            "target": {"service": "order-service", "environment": "staging", "agent_id": "agent-a", "pid": 123},
            "time_range": {"start": "2026-08-05T10:00:00Z", "end": "2026-08-05T10:05:00Z"},
        },
    )
    assert clarified.status_code == 200, clarified.text
    data = clarified.json()["data"]
    assert data["status"] == "UNDERSTANDING"
    assert data["target"]["service"] == "order-service"
