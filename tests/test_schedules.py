from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from server.app.database import init_db, new_session, reset_engine
from server.app.schemas import CreateTaskRequest
from server.app.sql_repository import SqlRepository
from server.app.state_machine import now_utc
from server.app.schedule_worker import run_due_schedules


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


@pytest.fixture(name="repo")
def repo_fixture() -> SqlRepository:
    return SqlRepository()


@pytest.fixture()
def agent(repo):
    return repo.register_agent("agent-sched", "host-sched", "10.0.0.9")


TEMPLATE = {
    "name": "夜间巡检",
    "agent_id": "agent-sched",
    "target_pid": 1,
    "collector_type": "perf_cpu",
    "sample_rate": 99,
    "duration_sec": 15,
}


def test_create_schedule_computes_next_run_and_validates(agent, repo):
    model = repo.create_schedule(
        name="巡检",
        cron_expression="0 3 * * *",
        timezone="Asia/Shanghai",
        task_template=TEMPLATE,
    )
    assert model.next_run_at > now_utc()
    assert model.enabled is True

    with pytest.raises(ValueError):
        repo.create_schedule(
            name="坏 cron",
            cron_expression="not a cron",
            timezone="Asia/Shanghai",
            task_template=TEMPLATE,
        )


def test_fire_schedule_creates_task_record_and_advances(agent, repo):
    schedule = repo.create_schedule(
        name="巡检",
        cron_expression="0 3 * * *",
        timezone="Asia/Shanghai",
        task_template=TEMPLATE,
    )
    scheduled_at = schedule.next_run_at
    next_run = scheduled_at + timedelta(days=1)
    task = repo.fire_schedule(
        schedule,
        scheduled_at=scheduled_at,
        next_run_at=next_run,
        payload=CreateTaskRequest(**TEMPLATE),
    )
    assert task.id.startswith("task_")
    records = repo.list_schedule_records(schedule.id)
    assert len(records) == 1
    assert records[0].task_id == task.id
    assert records[0].status == "created"
    updated = repo.get_schedule(schedule.id)
    # SQLite returns naive datetimes; compare against a naive clock.
    assert updated.next_run_at == next_run.replace(tzinfo=None)
    # The scheduled task also published a transactional outbox message.
    outbox = repo.list_outbox_messages()
    assert any(item.aggregate_id == task.id for item in outbox)


def test_run_due_schedules_fires_only_due(agent, repo):
    due = repo.create_schedule(
        name="到期",
        cron_expression="0 3 * * *",
        timezone="Asia/Shanghai",
        task_template=TEMPLATE,
    )
    future = repo.create_schedule(
        name="未到期",
        cron_expression="0 3 * * *",
        timezone="Asia/Shanghai",
        task_template=TEMPLATE,
    )
    # Force `due` into the past and `future` far into the future.
    with new_session() as session:
        from server.app.models import ScheduleModel

        row = session.get(ScheduleModel, due.id)
        row.next_run_at = now_utc() - timedelta(minutes=1)
        other = session.get(ScheduleModel, future.id)
        other.next_run_at = now_utc() + timedelta(days=30)
        session.commit()

    fired = run_due_schedules(repo, limit=10)
    assert fired == 1
    assert len(repo.list_schedule_records(due.id)) == 1
    assert repo.list_schedule_records(future.id) == []


def test_dedup_slot_prevents_double_fire(agent, repo):
    schedule = repo.create_schedule(
        name="去重",
        cron_expression="0 3 * * *",
        timezone="Asia/Shanghai",
        task_template=TEMPLATE,
    )
    scheduled_at = schedule.next_run_at
    repo.fire_schedule(
        schedule,
        scheduled_at=scheduled_at,
        next_run_at=scheduled_at + timedelta(days=1),
        payload=CreateTaskRequest(**TEMPLATE),
    )
    records = repo.list_schedule_records(schedule.id)
    assert len(records) == 1


def test_schedule_api_crud_and_manual_trigger(agent, repo):
    client = TestClient(__import__("server.app.main", fromlist=["app"]).app)
    created = client.post("/api/schedules", json={
        "name": "API 巡检",
        "cron_expression": "*/30 * * * *",
        "timezone": "Asia/Shanghai",
        "task_template": TEMPLATE,
    })
    assert created.status_code == 200, created.text
    schedule_id = created.json()["data"]["id"]
    assert created.json()["data"]["cron_expression"] == "*/30 * * * *"

    listed = client.get("/api/schedules").json()["data"]["items"]
    assert any(item["id"] == schedule_id for item in listed)

    triggered = client.post(f"/api/schedules/{schedule_id}/trigger")
    assert triggered.status_code == 200, triggered.text
    assert triggered.json()["data"]["task_id"].startswith("task_")

    records = client.get(f"/api/schedules/{schedule_id}/records").json()["data"]["items"]
    assert len(records) == 1

    deleted = client.delete(f"/api/schedules/{schedule_id}")
    assert deleted.status_code == 200
    assert client.get(f"/api/schedules/{schedule_id}/records").status_code == 200
