import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.service import (
    finalize_effective_time_range,
    open_effective_time_range,
)
from server.app.main import app
from server.app.models import (
    AgentModel,
    DropInsightSessionModel,
    DropInsightTargetBindingModel,
    DropInsightTargetDiscoveryModel,
)
from server.app.sql_repository import SqlRepository
from server.app.state_machine import now_utc


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    monkeypatch.setenv("MINI_DROP_AI_ENABLED", "none")
    monkeypatch.delenv("MINI_DROP_AI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    reset_engine()
    init_db()
    yield
    reset_engine()


def _seed_agent_snapshot(
    *,
    agent_id: str = "agent-a",
    candidates: list[dict] | None = None,
    complete: bool = True,
    truncated: bool = False,
    error: str = "",
    received_at: datetime | None = None,
    boot_id: str = "boot-a",
    generation: int = 1,
):
    timestamp = received_at or now_utc()
    session = new_session()
    session.add(
        AgentModel(
            id=agent_id,
            hostname=f"{agent_id}-host",
            ip_addr="127.0.0.1",
            version="1.0",
            os_info="linux",
            capabilities=["sys_metrics", "perf_cpu"],
            status="ONLINE",
            last_heartbeat_at=now_utc(),
            created_at=timestamp,
            updated_at=timestamp,
        )
    )
    session.commit()
    session.close()
    if candidates is None:
        candidates = [
            {
                "pid": 1234,
                "process_start_ticks": 456,
                "pid_namespace_inode": 789,
                "namespace_pid": 1234,
                "executable_identity": "sha256:order-service",
                "comm": "order-service",
                "cgroup": "/staging/order-service",
                "service_hint": "order-service",
                "instance_hint": "staging",
                "collector_capabilities": ["sys_metrics", "perf_cpu"],
            }
        ]
    return SqlRepository().record_process_candidate_snapshot(
        agent_id,
        {
            "generation": generation,
            "boot_id": boot_id,
            "observed_at_unix_ms": 1,
            "complete": complete,
            "truncated": truncated,
            "candidates": candidates,
            "error": error,
        },
        received_at=timestamp,
    )


def _create_scoped_diagnosis(client: TestClient) -> dict:
    return client.post(
        "/api/v2/diagnoses",
        json={
            "query": "订单服务 CPU 飙高",
            "target": {"service": "order-service", "environment": "staging"},
            "time_range": {
                "start": "2026-07-27T10:00:00Z",
                "end": "2026-07-27T10:05:00Z",
            },
        },
    ).json()["data"]


def _discover(client: TestClient, diagnosis_id: str) -> dict:
    response = client.get(f"/api/v2/diagnoses/{diagnosis_id}/target-candidates")
    assert response.status_code == 200
    return response.json()["data"]


def _clarify_with_candidate(client: TestClient, diagnosis: dict, discovery: dict):
    return client.post(
        f"/api/v2/diagnoses/{diagnosis['diagnosis_id']}/clarify",
        json={
            "expected_version": diagnosis["version"],
            "target": {
                "service": "order-service",
                "environment": "staging",
                "discovery_id": discovery["discovery_id"],
                "binding_id": discovery["candidates"][0]["binding_id"],
            },
        },
    )


def test_v2_requires_clarification_instead_of_inventing_scope():
    client = TestClient(app)
    response = client.post(
        "/api/v2/diagnoses",
        json={"query": "订单服务最近变慢了，帮我定位"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "NEEDS_CLARIFICATION"
    question_ids = {item["question_id"] for item in data["clarification_questions"]}
    assert question_ids == {
        "target.service",
        "target.environment",
        "target.binding",
        "time_range",
    }


def test_v2_complete_scope_enters_understanding_and_records_event():
    client = TestClient(app)
    _seed_agent_snapshot()
    created = _create_scoped_diagnosis(client)
    discovery = _discover(client, created["diagnosis_id"])
    response = _clarify_with_candidate(client, created, discovery)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "UNDERSTANDING"
    assert data["target"]["agent_id"] == "agent-a"
    assert data["target"]["pid"] == 1234
    assert data["target"]["process_binding"]["boot_id"] == "boot-a"
    diagnosis_id = data["diagnosis_id"]
    detail = client.get(f"/api/v2/diagnoses/{diagnosis_id}")
    assert detail.status_code == 200
    events = client.get(f"/api/v2/diagnoses/{diagnosis_id}/events").json()["data"]
    assert events[0]["event_type"] == "diagnosis.created"
    expected_range = {
        "start": "2026-07-27T10:00:00Z",
        "end": "2026-07-27T10:05:00Z",
        "timezone": "Asia/Shanghai",
    }
    assert data["time_range"] == expected_range
    assert data["requested_time_range"] == expected_range
    assert data["effective_time_range"] == {}


def test_v2_clarification_establishes_immutable_requested_time_range():
    client = TestClient(app)
    created = client.post(
        "/api/v2/diagnoses",
        json={"query": "订单服务 CPU 飙高"},
    ).json()["data"]
    diagnosis_id = created["diagnosis_id"]
    requested_range = {
        "start": "2026-07-27T10:00:00Z",
        "end": "2026-07-27T10:05:00Z",
        "timezone": "Asia/Shanghai",
    }

    first = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/clarify",
        json={"time_range": requested_range},
    )
    assert first.status_code == 200
    established = first.json()["data"]
    assert established["time_range"] == established["requested_time_range"]
    assert established["effective_time_range"] == {}

    identical = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/clarify",
        json={"time_range": requested_range},
    )
    assert identical.status_code == 200
    assert identical.json()["data"]["requested_time_range"] == established[
        "requested_time_range"
    ]

    replacement = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/clarify",
        json={
            "time_range": {
                "start": "2026-07-27T10:05:00Z",
                "end": "2026-07-27T10:10:00Z",
                "timezone": "Asia/Shanghai",
            }
        },
    )
    assert replacement.status_code == 409
    detail = client.get(f"/api/v2/diagnoses/{diagnosis_id}").json()["data"]
    assert detail["time_range"] == established["requested_time_range"]
    assert detail["requested_time_range"] == established["requested_time_range"]
    assert detail["effective_time_range"] == {}


def test_v2_reproduction_effective_range_has_explicit_lifecycle():
    client = TestClient(app)
    requested_range = {
        "start": "2026-07-27T09:00:00Z",
        "end": "2026-07-27T09:05:00Z",
        "timezone": "Asia/Shanghai",
    }
    created = client.post(
        "/api/v2/diagnoses",
        json={
            "query": "受控复现订单服务 CPU 热点",
            "mode": "REPRODUCTION",
            "target": {
                "service": "order-service",
                "environment": "staging",
                "agent_id": "agent-a",
                "pid": 1234,
            },
            "time_range": requested_range,
        },
    ).json()["data"]
    diagnosis_id = created["diagnosis_id"]
    opened_at = datetime(2026, 7, 27, 10, 0, 0, tzinfo=timezone.utc)

    opened = open_effective_time_range(diagnosis_id, opened_at=opened_at)
    assert opened["requested_time_range"] == requested_range
    assert opened["time_range"] == requested_range
    assert opened["effective_time_range"] == {
        "state": "OPEN",
        "source": "controlled_live_collection",
        "opened_at": "2026-07-27T10:00:00Z",
        "timezone": "Asia/Shanghai",
    }

    finalized = finalize_effective_time_range(
        diagnosis_id,
        observed_start=datetime(2026, 7, 27, 10, 0, 1, tzinfo=timezone.utc),
        observed_end=datetime(2026, 7, 27, 10, 0, 30, tzinfo=timezone.utc),
    )
    assert finalized["requested_time_range"] == requested_range
    assert finalized["time_range"] == requested_range
    assert finalized["effective_time_range"] == {
        "state": "FINALIZED",
        "source": "controlled_live_collection",
        "opened_at": "2026-07-27T10:00:00Z",
        "start": "2026-07-27T10:00:01Z",
        "end": "2026-07-27T10:00:30Z",
        "timezone": "Asia/Shanghai",
    }
    assert finalize_effective_time_range(
        diagnosis_id,
        observed_start=datetime(2026, 7, 27, 10, 0, 1, tzinfo=timezone.utc),
        observed_end=datetime(2026, 7, 27, 10, 0, 30, tzinfo=timezone.utc),
    )["effective_time_range"] == finalized["effective_time_range"]


def test_v2_reproduction_effective_range_rejects_invalid_transitions():
    client = TestClient(app)
    created = client.post(
        "/api/v2/diagnoses",
        json={"query": "受控复现订单服务 CPU 热点", "mode": "REPRODUCTION"},
    ).json()["data"]
    diagnosis_id = created["diagnosis_id"]
    opened_at = datetime(2026, 7, 27, 10, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="opened before finalization"):
        finalize_effective_time_range(
            diagnosis_id,
            observed_start=opened_at,
            observed_end=datetime(2026, 7, 27, 10, 0, 30, tzinfo=timezone.utc),
        )
    open_effective_time_range(diagnosis_id, opened_at=opened_at)
    with pytest.raises(ValueError, match="cannot start before"):
        finalize_effective_time_range(
            diagnosis_id,
            observed_start=datetime(2026, 7, 27, 9, 59, 59, tzinfo=timezone.utc),
            observed_end=datetime(2026, 7, 27, 10, 0, 30, tzinfo=timezone.utc),
        )


def test_v2_partial_clarification_keeps_missing_scope_visible():
    client = TestClient(app)
    created = client.post("/api/v2/diagnoses", json={"query": "订单服务 CPU 飙高"}).json()["data"]

    clarified = client.post(
        f"/api/v2/diagnoses/{created['diagnosis_id']}/clarify",
        json={"target": {"service": "order-service", "environment": "staging"}},
    )

    assert clarified.status_code == 200
    data = clarified.json()["data"]
    assert data["status"] == "NEEDS_CLARIFICATION"
    assert data["target"]["service"] == "order-service"
    assert {item["question_id"] for item in data["clarification_questions"]} == {
        "target.binding",
        "time_range",
    }


def test_clarification_rejects_raw_process_authority():
    client = TestClient(app)
    created = _create_scoped_diagnosis(client)
    response = client.post(
        f"/api/v2/diagnoses/{created['diagnosis_id']}/clarify",
        json={"target": {"agent_id": "agent-a", "pid": 1234}},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (None, "UNAVAILABLE"),
        ({"candidates": []}, "EMPTY"),
        ({"complete": False}, "UNAVAILABLE"),
        ({"error": "collector failed"}, "UNAVAILABLE"),
        ({"truncated": True}, "TRUNCATED"),
    ],
)
def test_target_discovery_preserves_fail_closed_snapshot_states(snapshot, expected):
    client = TestClient(app)
    timestamp = now_utc()
    session = new_session()
    session.add(AgentModel(
        id="agent-a",
        hostname="host",
        ip_addr="127.0.0.1",
        version="1",
        os_info="linux",
        capabilities=[],
        status="ONLINE",
        last_heartbeat_at=timestamp,
        created_at=timestamp,
        updated_at=timestamp,
    ))
    session.commit()
    session.close()
    if snapshot is not None:
        value = {
            "generation": 1,
            "boot_id": "boot-a",
            "observed_at_unix_ms": 1,
            "complete": True,
            "truncated": False,
            "candidates": [{
                "pid": 1234,
                "process_start_ticks": 456,
                "pid_namespace_inode": 789,
                "namespace_pid": 1234,
                "executable_identity": "sha256:order-service",
                "comm": "order-service",
                "service_hint": "order-service",
                "instance_hint": "staging",
            }],
            "error": "",
        }
        value.update(snapshot)
        SqlRepository().record_process_candidate_snapshot("agent-a", value, received_at=timestamp)
    diagnosis = _create_scoped_diagnosis(client)
    result = _discover(client, diagnosis["diagnosis_id"])
    assert result["status"] == expected
    assert result["candidates"] == []


def test_target_discovery_ready_ambiguous_and_opaque():
    client = TestClient(app)
    candidates = [
        {
            "pid": pid,
            "process_start_ticks": 400 + pid,
            "pid_namespace_inode": 789,
            "namespace_pid": pid,
            "executable_identity": f"sha256:order-{pid}",
            "comm": "order-service",
            "service_hint": "order-service",
            "instance_hint": "staging",
        }
        for pid in (1234, 1235)
    ]
    _seed_agent_snapshot(candidates=candidates)
    diagnosis = _create_scoped_diagnosis(client)
    ambiguous = _discover(client, diagnosis["diagnosis_id"])
    assert ambiguous["status"] == "AMBIGUOUS"
    assert len(ambiguous["candidates"]) == 2
    assert all("agent_id" not in item and "pid" not in item for item in ambiguous["candidates"])
    ready_response = client.get(
        f"/api/v2/diagnoses/{diagnosis['diagnosis_id']}/target-candidates",
        params={"service": "sha256:order-1234"},
    )
    assert ready_response.status_code == 200
    ready = ready_response.json()["data"]
    assert ready["status"] == "READY"
    assert len(ready["candidates"]) == 1
    assert ready["discovery_id"].startswith("discovery_")
    assert ready["candidates"][0]["binding_id"].startswith("binding_")


def test_target_discovery_keeps_authoritative_candidate_when_another_agent_is_absent():
    client = TestClient(app)
    _seed_agent_snapshot()
    timestamp = now_utc()
    session = new_session()
    session.add(AgentModel(
        id="agent-without-snapshot",
        hostname="empty-host",
        ip_addr="127.0.0.2",
        version="1.0",
        os_info="linux",
        capabilities=["perf_cpu"],
        status="ONLINE",
        last_heartbeat_at=timestamp,
        created_at=timestamp,
        updated_at=timestamp,
    ))
    session.commit()
    session.close()
    diagnosis = _create_scoped_diagnosis(client)

    discovery = _discover(client, diagnosis["diagnosis_id"])

    assert discovery["status"] == "READY"
    assert len(discovery["candidates"]) == 1


def test_target_discovery_stale_and_latest_snapshot_invalidation():
    client = TestClient(app)
    _seed_agent_snapshot(received_at=now_utc() - timedelta(seconds=16))
    diagnosis = _create_scoped_diagnosis(client)
    assert _discover(client, diagnosis["diagnosis_id"])["status"] == "STALE"

    session = new_session()
    agent = session.get(AgentModel, "agent-a")
    agent.last_heartbeat_at = now_utc()
    session.commit()
    session.close()
    SqlRepository().record_process_candidate_snapshot("agent-a", {
        "generation": 2,
        "boot_id": "boot-a",
        "observed_at_unix_ms": 2,
        "complete": True,
        "truncated": False,
        "candidates": [{
            "pid": 1234,
            "process_start_ticks": 456,
            "pid_namespace_inode": 789,
            "namespace_pid": 1234,
            "executable_identity": "sha256:order-service",
            "comm": "order-service",
            "service_hint": "order-service",
            "instance_hint": "staging",
        }],
        "error": "",
    }, received_at=now_utc())
    ready = _discover(client, diagnosis["diagnosis_id"])
    assert ready["status"] == "READY"
    SqlRepository().record_process_candidate_snapshot("agent-a", {
        "generation": 3,
        "boot_id": "boot-restarted",
        "observed_at_unix_ms": 3,
        "complete": True,
        "truncated": False,
        "candidates": [{
            "pid": 1234,
            "process_start_ticks": 999,
            "pid_namespace_inode": 789,
            "namespace_pid": 1234,
            "executable_identity": "sha256:order-service",
            "comm": "order-service",
            "service_hint": "order-service",
            "instance_hint": "staging",
        }],
        "error": "",
    }, received_at=now_utc())
    response = _clarify_with_candidate(client, diagnosis, ready)
    assert response.status_code == 409


def test_clarification_rejects_cross_diagnosis_stale_version_and_expiry():
    client = TestClient(app)
    _seed_agent_snapshot()
    first = _create_scoped_diagnosis(client)
    second = _create_scoped_diagnosis(client)
    discovery = _discover(client, first["diagnosis_id"])
    cross = _clarify_with_candidate(client, second, discovery)
    assert cross.status_code == 409

    partial = client.post(
        f"/api/v2/diagnoses/{first['diagnosis_id']}/clarify",
        json={"expected_version": first["version"], "target": {"service": "order-service"}},
    )
    assert partial.status_code == 200
    stale = _clarify_with_candidate(client, first, discovery)
    assert stale.status_code == 409

    fresh_diagnosis = _create_scoped_diagnosis(client)
    expired = _discover(client, fresh_diagnosis["diagnosis_id"])
    session = new_session()
    row = session.get(DropInsightTargetDiscoveryModel, expired["discovery_id"])
    row.expires_at = now_utc() - timedelta(seconds=1)
    session.commit()
    session.close()
    assert _clarify_with_candidate(client, fresh_diagnosis, expired).status_code == 409


def test_v2_rejects_unknown_fields():
    client = TestClient(app)
    response = client.post(
        "/api/v2/diagnoses",
        json={"query": "订单服务变慢", "arbitrary_shell": "rm -rf /"},
    )
    assert response.status_code == 422


def test_v2_tools_are_allow_listed_and_schema_closed():
    client = TestClient(app)
    response = client.get("/api/v2/diagnostic-tools")

    assert response.status_code == 200
    tools = response.json()["data"]["items"]
    assert {tool["name"] for tool in tools} >= {
        "get_agent_status",
        "collect_sys_metrics",
        "start_perf_profile",
    }
    assert all(tool["input_schema"]["additionalProperties"] is False for tool in tools)
    perf = next(tool for tool in tools if tool["name"] == "start_perf_profile")
    assert perf["risk_level"] == "R2"
    assert perf["requires_approval"] is True


def test_unified_diagnostic_case_view_keeps_v2_api_available():
    client = TestClient(app)
    created = client.post(
        "/api/v2/diagnoses",
        json={"query": "订单服务最近变慢了，帮我定位"},
    ).json()["data"]
    diagnosis_id = created["diagnosis_id"]

    legacy = client.get(f"/api/v2/diagnoses/{diagnosis_id}")
    cases = client.get("/api/diagnostic-cases").json()["data"]
    unified = client.get(f"/api/diagnostic-cases/{diagnosis_id}")

    assert legacy.status_code == 200
    assert unified.status_code == 200
    assert cases["compatibility"]["v2_preserved"] is True
    assert cases["compatibility"]["write_mode"] == "native_api_only"
    assert any(item["case_id"] == diagnosis_id for item in cases["items"])
    assert unified.json()["data"]["source"] == "drop_insight_v2"
    assert unified.json()["data"]["native_payload"]["diagnosis_id"] == diagnosis_id


def test_wrong_feedback_opens_a_traceable_new_diagnosis_round():
    client = TestClient(app)
    _seed_agent_snapshot()
    created = _create_scoped_diagnosis(client)
    discovery = _discover(client, created["diagnosis_id"])
    clarified = _clarify_with_candidate(client, created, discovery)
    assert clarified.status_code == 200
    diagnosis = clarified.json()["data"]
    hypothesis = client.post(
        f"/api/v2/diagnoses/{diagnosis['diagnosis_id']}/hypotheses",
        json={
            "statement": "CPU 高可能由业务热点函数引起",
            "expected_observations": ["样本集中在业务函数"],
            "falsification_criteria": ["CPU 样本均匀且系统负载异常"],
        },
    ).json()["data"]

    response = client.post(
        f"/api/v2/diagnoses/{diagnosis['diagnosis_id']}/feedback",
        json={
            "hypothesis_id": hypothesis["hypothesis_id"],
            "feedback_label": "wrong",
            "corrected_cause": "怀疑是同宿主机噪声邻居导致 CPU 争抢",
            "request_replan": True,
        },
    )

    assert response.status_code == 200
    feedback = response.json()["data"]
    assert feedback["revision_hypothesis_id"]
    hypotheses = client.get(
        f"/api/v2/diagnoses/{diagnosis['diagnosis_id']}/hypotheses"
    ).json()["data"]["items"]
    parent = next(item for item in hypotheses if item["hypothesis_id"] == hypothesis["hypothesis_id"])
    revision = next(item for item in hypotheses if item["hypothesis_id"] == feedback["revision_hypothesis_id"])
    assert parent["status"] == "COUNTER"
    assert revision["round_index"] == 2
    assert revision["parent_hypothesis_id"] == parent["hypothesis_id"]
    assert revision["source"] == "USER_GUIDED_FALLBACK"
    assert client.get(
        f"/api/v2/diagnoses/{diagnosis['diagnosis_id']}/feedback"
    ).json()["data"]["items"][0]["feedback_label"] == "wrong"


def test_correct_feedback_keeps_current_round_without_replanning():
    client = TestClient(app)
    diagnosis = client.post(
        "/api/v2/diagnoses", json={"query": "服务 CPU 高，请定位原因"}
    ).json()["data"]
    response = client.post(
        f"/api/v2/diagnoses/{diagnosis['diagnosis_id']}/feedback",
        json={"feedback_label": "correct", "request_replan": True},
    )
    assert response.status_code == 200
    assert response.json()["data"]["requested_replan"] is False
    hypotheses = client.get(
        f"/api/v2/diagnoses/{diagnosis['diagnosis_id']}/hypotheses"
    ).json()["data"]["items"]
    assert hypotheses == []
