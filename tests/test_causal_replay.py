import pytest
from fastapi.testclient import TestClient

from benchmarks.causal_replay.run_golden import run as run_golden
from server.app.database import init_db, reset_engine
from server.app.main import app


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    reset_engine()
    init_db()
    yield
    reset_engine()


def _diagnosis_and_hypothesis(client: TestClient):
    diagnosis = client.post(
        "/api/v2/diagnoses",
        json={
            "query": "订单服务 CPU 飙高，请验证是否由用户态热点导致",
            "target": {
                "service": "order-service",
                "environment": "staging",
                "agent_id": "worker-a",
                "pid": 1234,
            },
            "time_range": {
                "start": "2026-08-23T10:00:00Z",
                "end": "2026-08-23T10:05:00Z",
            },
        },
    ).json()["data"]
    hypothesis = client.post(
        f"/api/v2/diagnoses/{diagnosis['diagnosis_id']}/hypotheses",
        json={
            "statement": "CPU 飙高由用户态业务热点路径导致",
            "expected_observations": ["处理组干预后 CPU 与 P99 同时下降"],
            "falsification_criteria": ["对照组同步改善或处理组没有改善"],
        },
    ).json()["data"]
    return diagnosis["diagnosis_id"], hypothesis["hypothesis_id"]


def _plan_payload(hypothesis_id: str):
    return {
        "hypothesis_id": hypothesis_id,
        "case_id": "CR-CPU-001",
        "design_mode": "DUAL_NODE_CONTROL",
        "treatment_target": {"agent_id": "worker-a", "pid": 1234},
        "control_target": {"agent_id": "worker-b", "pid": 5678},
        "duration_seconds": 60,
    }


def test_causal_experiment_requires_human_approval_and_is_auditable():
    client = TestClient(app)
    diagnosis_id, hypothesis_id = _diagnosis_and_hypothesis(client)

    created = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/causal-experiments",
        json=_plan_payload(hypothesis_id),
    )
    assert created.status_code == 200
    experiment = created.json()["data"]
    assert experiment["status"] == "WAITING_APPROVAL"
    assert experiment["plan"]["approval_required"] is True

    approved = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/causal-experiments/{experiment['experiment_id']}/decision",
        json={"approved": True, "reason": "仅运行白名单单变量实验，同意执行"},
    )
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "APPROVED"

    events = client.get(f"/api/v2/diagnoses/{diagnosis_id}/events").json()["data"]
    assert "causal_experiment.planned" in [item["event_type"] for item in events]
    assert "causal_experiment.decision" in [item["event_type"] for item in events]


def test_causal_experiment_verifies_only_with_four_window_provenance():
    client = TestClient(app)
    diagnosis_id, hypothesis_id = _diagnosis_and_hypothesis(client)
    experiment = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/causal-experiments",
        json=_plan_payload(hypothesis_id),
    ).json()["data"]
    client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/causal-experiments/{experiment['experiment_id']}/decision",
        json={"approved": True, "reason": "批准受控验证"},
    )

    response = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/causal-experiments/{experiment['experiment_id']}/evaluate",
        json={
            "baseline": {"treatment": 100, "control": 100, "task_ids": ["task-baseline"]},
            "incident": {"treatment": 180, "control": 175, "task_ids": ["task-incident"]},
            "intervention": {"treatment": 105, "control": 170, "task_ids": ["task-intervention"]},
            "recovery": {"treatment": 102, "control": 101, "task_ids": ["task-recovery"]},
        },
    )
    assert response.status_code == 200
    result = response.json()["data"]["judgement"]
    assert result["verdict"] == "CAUSALLY_VERIFIED"
    assert result["metrics"]["difference_in_differences"] > 0.25


def test_causal_experiment_abstains_without_control_or_provenance():
    client = TestClient(app)
    diagnosis_id, hypothesis_id = _diagnosis_and_hypothesis(client)
    experiment = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/causal-experiments",
        json=_plan_payload(hypothesis_id),
    ).json()["data"]
    client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/causal-experiments/{experiment['experiment_id']}/decision",
        json={"approved": True, "reason": "批准受控验证"},
    )
    response = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/causal-experiments/{experiment['experiment_id']}/evaluate",
        json={
            "baseline": {"treatment": 100},
            "incident": {"treatment": 180},
            "intervention": {"treatment": 90},
            "recovery": {"treatment": 100},
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["judgement"]["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_preview_rejects_same_agent_as_fake_control():
    client = TestClient(app)
    diagnosis_id, hypothesis_id = _diagnosis_and_hypothesis(client)
    payload = _plan_payload(hypothesis_id)
    payload["control_target"]["agent_id"] = "worker-a"
    response = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/causal-experiments/preview",
        json=payload,
    )
    assert response.status_code == 409
    assert "different agents" in response.json()["detail"]


def test_single_node_crossover_uses_no_control_and_caps_the_claim():
    client = TestClient(app)
    diagnosis_id, hypothesis_id = _diagnosis_and_hypothesis(client)
    payload = {
        "hypothesis_id": hypothesis_id,
        "case_id": "CR-CPU-001",
        "design_mode": "SINGLE_NODE_CROSSOVER",
        "treatment_target": {"agent_id": "worker-a", "pid": 1234},
        "duration_seconds": 30,
    }
    experiment = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/causal-experiments",
        json=payload,
    ).json()["data"]
    assert experiment["plan"]["control_target"] is None
    client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/causal-experiments/{experiment['experiment_id']}/decision",
        json={"approved": True, "reason": "批准同一节点上的可逆白名单实验"},
    )
    response = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/causal-experiments/{experiment['experiment_id']}/evaluate",
        json={
            "baseline": {"treatment": 100, "task_ids": ["task-baseline"]},
            "incident": {"treatment": 180, "task_ids": ["task-incident"]},
            "intervention": {"treatment": 105, "task_ids": ["task-intervention"]},
            "recovery": {"treatment": 102, "task_ids": ["task-recovery"]},
        },
    )
    assert response.status_code == 200
    judgement = response.json()["data"]["judgement"]
    assert judgement["verdict"] == "SUPPORTED_SINGLE_NODE"
    assert judgement["confidence"] <= 0.75
    assert judgement["metrics"]["difference_in_differences"] is None


def test_plan_exposes_small_host_resource_admission_policy():
    client = TestClient(app)
    diagnosis_id, hypothesis_id = _diagnosis_and_hypothesis(client)
    experiment = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/causal-experiments",
        json={
            "hypothesis_id": hypothesis_id,
            "case_id": "CR-CPU-001",
            "design_mode": "SINGLE_NODE_CROSSOVER",
            "treatment_target": {"agent_id": "worker-a", "pid": 1234},
            "duration_seconds": 60,
        },
    ).json()["data"]
    policy = experiment["plan"]["resource_policy"]
    assert policy["recommended"] is True
    assert policy["minimum_online_agents"] == 1
    assert policy["minimum_available_memory_mb_per_worker"] == 512


def test_causal_replay_golden_set_has_no_silent_regressions():
    report = run_golden()
    assert report["total"] >= 10
    assert report["failed"] == 0, report["results"]
    assert report["verdict_coverage"]["INSUFFICIENT_EVIDENCE"] >= 1
