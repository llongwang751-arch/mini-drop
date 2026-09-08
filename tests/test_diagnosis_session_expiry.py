from datetime import timedelta

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.service import expire_stale_autonomous_diagnoses
from server.app.models import DropInsightEventModel, DropInsightSessionModel
from server.app.state_machine import now_utc


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    reset_engine()


def _diagnosis(diagnosis_id: str, *, created_at, status: str = "UNDERSTANDING"):
    return DropInsightSessionModel(
        id=diagnosis_id,
        query="diagnose a bounded live incident",
        target_json={},
        time_range_json={},
        requested_time_range_json={},
        effective_time_range_json={},
        mode="AUTONOMOUS",
        skill_policy="AUTO",
        created_by="test",
        budget_json={"max_duration_seconds": 60},
        status=status,
        version=1,
        clarification_questions_json=[],
        created_at=created_at,
        updated_at=created_at,
    )


def test_expired_autonomous_session_is_cancelled_with_a_durable_reason() -> None:
    timestamp = now_utc()
    with new_session() as session:
        session.add(_diagnosis("expired", created_at=timestamp - timedelta(seconds=61)))
        session.add(_diagnosis("fresh", created_at=timestamp - timedelta(seconds=30)))
        session.add(
            _diagnosis(
                "terminal",
                created_at=timestamp - timedelta(hours=1),
                status="COMPLETED",
            )
        )
        session.commit()

    assert expire_stale_autonomous_diagnoses(timestamp=timestamp) == ["expired"]

    with new_session() as session:
        expired = session.get(DropInsightSessionModel, "expired")
        assert expired.status == "CANCELLED"
        assert expired.version == 2
        assert session.get(DropInsightSessionModel, "fresh").status == "UNDERSTANDING"
        assert session.get(DropInsightSessionModel, "terminal").status == "COMPLETED"
        event = (
            session.query(DropInsightEventModel)
            .filter(DropInsightEventModel.diagnosis_id == "expired")
            .one()
        )
        assert event.event_type == "diagnosis.expired"
        assert event.payload_json["reason"] == "wall_clock_budget_exhausted"
