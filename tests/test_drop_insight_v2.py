import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, reset_engine
from server.app.main import app


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
        "target.agent_id",
        "target.pid",
        "time_range",
    }


def test_v2_complete_scope_enters_understanding_and_records_event():
    client = TestClient(app)
    response = client.post(
        "/api/v2/diagnoses",
        json={
            "query": "订单服务在测试环境最近五分钟变慢，帮我定位",
            "target": {
                "service": "order-service",
                "environment": "staging",
                "agent_id": "agent-a",
                "pid": 1234,
            },
            "time_range": {
                "start": "2026-07-27T10:00:00Z",
                "end": "2026-07-27T10:05:00Z",
                "timezone": "Asia/Shanghai",
            },
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "UNDERSTANDING"
    diagnosis_id = data["diagnosis_id"]
    detail = client.get(f"/api/v2/diagnoses/{diagnosis_id}")
    assert detail.status_code == 200
    events = client.get(f"/api/v2/diagnoses/{diagnosis_id}/events").json()["data"]
    assert events[0]["event_type"] == "diagnosis.created"


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
        "target.agent_id",
        "target.pid",
        "time_range",
    }


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
    diagnosis = client.post(
        "/api/v2/diagnoses",
        json={
            "query": "订单服务 CPU 高，请定位热点",
            "target": {
                "service": "order-service",
                "environment": "staging",
                "agent_id": "agent-a",
                "pid": 1234,
            },
        },
    ).json()["data"]
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
