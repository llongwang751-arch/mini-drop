"""Drop Insight v2 resource budget lifecycle: reserve -> settle/release -> usage."""

from __future__ import annotations

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.service import (
    _evaluate_resource_budget,
    _release_budget_reservation,
    _settle_budget_reservation,
)
from server.app.models import (
    ArtifactModel,
    DropInsightSessionModel,
    DropInsightToolCallModel,
    TaskModel,
)
from server.app.state_machine import now_utc


@pytest.fixture(autouse=True)
def _patch_db_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    from server.app.models import Base
    from server.app.database import _get_engine

    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


def _diagnosis(budget: dict) -> DropInsightSessionModel:
    session = new_session()
    model = DropInsightSessionModel(
        id="diag_budget",
        query="CPU 高",
        target_json={"service": "order", "environment": "staging", "agent_id": "agent-a", "pid": 123},
        time_range_json={"start": "2026-08-05T10:00:00Z", "end": "2026-08-05T10:05:00Z"},
        mode="EVIDENCE_FIRST",
        budget_json=budget,
        status="COLLECTING_EVIDENCE",
        version=1,
        created_at=now_utc(),
        updated_at=now_utc(),
    )
    session.add(model)
    session.commit()
    return model


def _reserved_call(session, *, artifact_bytes=100, agent_id="agent-a"):
    model = DropInsightToolCallModel(
        id="toolcall_budget_1",
        diagnosis_id="diag_budget",
        tool_name="start_perf_profile",
        arguments_json={"agent_id": agent_id, "pid": 123, "duration_seconds": 15},
        policy_decision="ALLOW",
        policy_checks_json=[],
        policy_reason="ok",
        status="TASK_CREATED",
        budget_reservation_json={
            "artifact_bytes": artifact_bytes,
            "duration_seconds": 15,
            "agent_id": agent_id,
        },
        budget_reservation_status="RESERVED",
        requested_by="system",
        created_at=now_utc(),
    )
    session.add(model)
    return model


def test_budget_denies_when_artifact_bytes_exceed_limit():
    _diagnosis({"max_artifact_bytes": 10, "max_tool_calls": 5})
    session = new_session()
    _reserved_call(session, artifact_bytes=100)  # already 100 reserved
    session.commit()
    diagnosis = session.get(DropInsightSessionModel, "diag_budget")
    result = _evaluate_resource_budget(
        session,
        diagnosis,
        tool_name="start_perf_profile",
        arguments={"agent_id": "agent-a", "pid": 123, "duration_seconds": 15},
    )
    assert result["allowed"] is False
    artifact_check = next(c for c in result["checks"] if c["name"] == "BUDGET_ARTIFACT_BYTES")
    assert artifact_check["result"] == "FAIL"


def test_settle_uses_actual_artifact_bytes():
    _diagnosis({"max_artifact_bytes": 10_000})
    session = new_session()
    model = _reserved_call(session, artifact_bytes=50)
    session.add(
        TaskModel(
            id="task-budget",
            name="perf",
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
    session.add_all([
        ArtifactModel(
            task_id="task-budget",
            artifact_type="raw",
            bucket="mini-drop",
            object_key="tasks/task-budget/raw/perf.data",
            content_type="application/octet-stream",
            size_bytes=120,
            created_at=now_utc(),
        ),
        ArtifactModel(
            task_id="task-budget",
            artifact_type="top_json",
            bucket="mini-drop",
            object_key="tasks/task-budget/top.json",
            content_type="application/json",
            size_bytes=30,
            created_at=now_utc(),
        ),
    ])
    session.commit()
    task = session.get(TaskModel, "task-budget")
    model = session.get(DropInsightToolCallModel, model.id)
    _settle_budget_reservation(session, model, task, timestamp=now_utc())
    assert model.budget_reservation_status == "SETTLED"
    assert model.budget_settlement_json["artifact_bytes"] == 150
    assert model.budget_settlement_json["duration_seconds"] == 15


def test_release_frees_reservation_on_failure():
    # start_perf_profile estimates 64 MiB; allow enough headroom so a single
    # request passes once the released reservation stops counting.
    _diagnosis({"max_artifact_bytes": 100_000_000})
    session = new_session()
    model = _reserved_call(session, artifact_bytes=50)
    session.commit()
    model = session.get(DropInsightToolCallModel, model.id)
    _release_budget_reservation(model, timestamp=now_utc(), reason="task_failed")
    assert model.budget_reservation_status == "RELEASED"
    assert model.budget_settlement_json["reason"] == "task_failed"
    # A released reservation must no longer count against the budget.
    diagnosis = session.get(DropInsightSessionModel, "diag_budget")
    result = _evaluate_resource_budget(
        session,
        diagnosis,
        tool_name="start_perf_profile",
        arguments={"agent_id": "agent-a", "pid": 123, "duration_seconds": 15},
    )
    assert result["allowed"] is True
