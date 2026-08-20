from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.service import compare_before_after, verify_diagnosis_fix
from server.app.models import ArtifactModel, TaskModel
from server.app.state_machine import now_utc


@pytest.fixture(autouse=True)
def _patch_db_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.delenv("MINI_DROP_API_AUTH_ENABLED", raising=False)
    reset_engine()
    init_db()
    yield
    from server.app.models import Base
    from server.app.database import _get_engine

    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


def _seed_task(task_id: str, top_functions) -> None:
    session = new_session()
    session.add(
        TaskModel(
            id=task_id,
            name=task_id,
            agent_id="agent-a",
            target_pid=123,
            collector_type="perf_cpu",
            sample_rate=99,
            duration_sec=15,
            status="DONE",
            status_reason="done",
            created_at=now_utc(),
        )
    )
    session.flush()
    session.add(
        ArtifactModel(
            task_id=task_id,
            artifact_type="top_json",
            bucket="mini-drop",
            object_key=f"tasks/{task_id}/top.json",
            content_type="application/json",
            size_bytes=100,
            sha256="c" * 64,
            integrity_status="VERIFIED",
            meta_json={"top_functions": top_functions},
            created_at=now_utc(),
        )
    )
    session.commit()
    session.close()


def test_compare_verified_when_hotspot_drops():
    before = [{"name": "calculate_price", "samples": 90, "percent": 90.0}]
    after = [{"name": "calculate_price", "samples": 30, "percent": 30.0}]
    result = compare_before_after(before, after)
    assert result["outcome"] == "VERIFIED"
    assert "降至" in result["reason"]


def test_compare_verified_when_hotspot_disappears():
    before = [{"name": "hot_loop", "samples": 90, "percent": 90.0}]
    after = [{"name": "readdir", "samples": 50, "percent": 50.0}]
    result = compare_before_after(before, after)
    assert result["outcome"] == "VERIFIED"
    assert "已从 TopN 消失" in result["reason"]


def test_compare_rejected_when_hotspot_persists():
    before = [{"name": "hot_loop", "samples": 90, "percent": 90.0}]
    after = [{"name": "hot_loop", "samples": 88, "percent": 88.0}]
    result = compare_before_after(before, after)
    assert result["outcome"] == "REJECTED"
    assert "未显著下降" in result["reason"]


def test_compare_rejected_without_before_hotspot():
    result = compare_before_after([], [{"name": "x", "percent": 10}])
    assert result["outcome"] == "REJECTED"


def test_compare_rejected_without_after_hotspot():
    result = compare_before_after([{"name": "x", "percent": 90}], [])
    assert result["outcome"] == "REJECTED"
    assert "不能把数据缺失当作热点消失" in result["reason"]


def test_verify_diagnosis_fix_stores_record():
    _seed_task("task-before", [{"name": "hot_loop", "samples": 90, "percent": 90.0}])
    _seed_task("task-after", [{"name": "hot_loop", "samples": 20, "percent": 20.0}])
    record = verify_diagnosis_fix(
        "diag-fix-1",
        before_task_id="task-before",
        after_task_id="task-after",
        fix_summary="改用记忆化",
    )
    assert record["outcome"] == "VERIFIED"
    assert record["before_task_id"] == "task-before"
    assert record["after_task_id"] == "task-after"


def test_fix_api_smoke():
    _seed_task("task-before", [{"name": "hot_loop", "samples": 90, "percent": 90.0}])
    _seed_task("task-after", [{"name": "other", "samples": 40, "percent": 40.0}])
    client = TestClient(__import__("server.app.main", fromlist=["app"]).app)
    response = client.post(
        "/api/v2/diagnoses/diag-fix-api/fix/verify",
        json={
            "before_task_id": "task-before",
            "after_task_id": "task-after",
            "fix_summary": "修复验证",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["outcome"] == "VERIFIED"
    records = client.get("/api/v2/diagnoses/diag-fix-api/fix").json()["data"]["items"]
    assert len(records) == 1
