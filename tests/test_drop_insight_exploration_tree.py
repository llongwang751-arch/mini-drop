from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, new_session, reset_engine
from server.app.event_bus import BUS
from server.app.main import app
from server.app.models import OutboxMessageModel
from server.app.outbox_dispatcher import event_bus_deliver


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    reset_engine()
    init_db()
    yield
    reset_engine()


def test_live_tree_grows_with_persisted_diagnosis_events():
    client = TestClient(app)
    created = client.post(
        "/api/v2/diagnoses",
        json={
            "query": "订单服务 CPU 和 P99 同时升高",
            "target": {},
            "mode": "ASSISTED",
        },
    ).json()["data"]
    diagnosis_id = created["diagnosis_id"]

    initial = client.get(
        f"/api/v2/diagnoses/{diagnosis_id}/exploration-tree"
    ).json()["data"]
    assert initial["revision"] == 1
    assert initial["stats"] == {
        "rounds": 0,
        "nodes": 1,
        "tool_calls": 0,
        "evidence": 0,
        "pruned": 0,
        "current_round": 0,
    }

    hypothesis = client.post(
        f"/api/v2/diagnoses/{diagnosis_id}/hypotheses",
        json={
            "statement": "CPU 高占用可能来自业务用户态热点",
            "expected_observations": ["perf 样本集中在少数业务函数"],
            "falsification_criteria": ["样本分散且等待态占主导"],
        },
    ).json()["data"]
    current = client.get(
        f"/api/v2/diagnoses/{diagnosis_id}/exploration-tree"
    ).json()["data"]

    assert current["revision"] == 2
    assert current["stats"]["rounds"] == 1
    assert current["stats"]["nodes"] == 2
    assert current["nodes"][1]["id"] == f"hypothesis:{hypothesis['hypothesis_id']}"
    assert current["nodes"][1]["parent_id"] == f"diagnosis:{diagnosis_id}"
    assert current["active_node_ids"] == [f"hypothesis:{hypothesis['hypothesis_id']}"]

    session = new_session()
    try:
        outbox = (
            session.query(OutboxMessageModel)
            .filter(OutboxMessageModel.aggregate_id == diagnosis_id)
            .order_by(OutboxMessageModel.created_at.asc())
            .all()
        )
        assert [item.aggregate_type for item in outbox] == ["diagnosis", "diagnosis"]
        assert outbox[-1].payload_json["sequence"] == 2
        assert outbox[-1].payload_json["event_type"] == "hypothesis.created"
    finally:
        session.close()


def test_diagnosis_outbox_fans_out_as_sse_progress():
    queue = BUS.subscribe()
    try:
        event_bus_deliver(
            SimpleNamespace(
                aggregate_type="diagnosis",
                aggregate_id="insight-1",
                event_type="hypothesis.created",
                payload_json={"sequence": 3, "event_type": "hypothesis.created"},
            )
        )
        event = queue.get(timeout=1)
        assert event["event"] == "diagnosis_progress"
        assert event["data"] == {
            "diagnosis_id": "insight-1",
            "sequence": 3,
            "event_type": "hypothesis.created",
        }
    finally:
        BUS.unsubscribe(queue)
