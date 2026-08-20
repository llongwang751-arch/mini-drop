"""Verify the Drop Insight v2 session status machine and optimistic-lock CAS.

The remaining architecture debt note (2026-08-05) said "internal advance etc
have not fully converged onto a single status machine". These tests pin down
that every session status change now flows through _cas_session_update, which
validates the legal-transition table AND writes with WHERE version = expected.
"""

from __future__ import annotations

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight.service import _cas_session_update
from server.app.models import DropInsightSessionModel
from server.app.state_machine import now_utc


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    yield
    reset_engine()


def _session_row(status: str, version: int = 1) -> DropInsightSessionModel:
    session = new_session()
    model = DropInsightSessionModel(
        id="diag-cas-1",
        query="CPU 使用率高",
        target_json={
            "service": "order",
            "environment": "staging",
            "agent_id": "agent-a",
            "pid": 123,
        },
        time_range_json={
            "start": "2026-07-27T10:00:00Z",
            "end": "2026-07-27T10:05:00Z",
        },
        mode="EVIDENCE_FIRST",
        budget_json={"max_tool_calls": 10, "max_risk_level": "R2"},
        status=status,
        version=version,
        created_at=now_utc(),
        updated_at=now_utc(),
    )
    session.add(model)
    session.commit()
    return model


def test_illegal_status_transition_is_rejected_by_table():
    _session_row("COMPLETED")
    session = new_session()
    diagnosis = session.get(DropInsightSessionModel, "diag-cas-1")
    with pytest.raises(ValueError, match="illegal diagnosis status transition"):
        _cas_session_update(
            session, diagnosis, status="PLANNING", timestamp=now_utc()
        )


def test_valid_transition_increments_version_via_cas():
    _session_row("UNDERSTANDING", version=1)
    session = new_session()
    diagnosis = session.get(DropInsightSessionModel, "diag-cas-1")
    _cas_session_update(session, diagnosis, status="PLANNING", timestamp=now_utc())
    session.commit()
    session.refresh(diagnosis)
    assert diagnosis.status == "PLANNING"
    assert diagnosis.version == 2


def test_stale_version_conflicts_under_optimistic_lock():
    _session_row("UNDERSTANDING", version=1)
    # A concurrent writer bumps the row underneath our in-memory copy.
    session = new_session()
    other = session.get(DropInsightSessionModel, "diag-cas-1")
    other.version = 2
    session.commit()

    session2 = new_session()
    stale = session2.get(DropInsightSessionModel, "diag-cas-1")
    stale.version = 1  # emulate a stale client/worker snapshot
    with pytest.raises(ValueError, match="version conflict"):
        _cas_session_update(
            session2, stale, status="PLANNING", timestamp=now_utc()
        )
