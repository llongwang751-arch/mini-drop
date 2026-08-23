from __future__ import annotations

from datetime import datetime, timezone

import pytest

from server.app.agent_runtime.deterministic import DeterministicAgentRuntime
from server.app.agent_runtime.port import AgentTurnInput, CaseContextSnapshot
from server.app.database import _get_engine, init_db, reset_engine
from server.app.diagnosis.store import DiagnosisStore
from server.app.models import Base
from server.app.sql_repository import SqlRepository


@pytest.fixture(autouse=True)
def _reset_repo(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    reset_engine()
    init_db()
    _create_diagnosis("diag-runtime")
    yield
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


def _create_diagnosis(diagnosis_id: str) -> None:
    now = datetime.now(timezone.utc)
    DiagnosisStore().create_session({
        "diagnosis_id": diagnosis_id,
        "case_id": "RW-RUNTIME-1",
        "creator_id": "alice",
        "raw_query": "diagnose runtime consistency",
        "status": "ANALYZING",
        "policy_profile": "balanced",
        "model_version": "test-model",
        "planner_version": "test-planner",
        "deadline_at": now,
    })


def _turn(**overrides) -> dict:
    value = {
        "diagnosis_id": "diag-runtime",
        "turn_id": "turn-1",
        "runtime_generation": 1,
        "user_message": "inspect the current evidence",
        "requested_mode": "COLLABORATE",
        "side_effect_policy": "READ_ONLY",
        "actor_id": "alice",
        "client_command_id": "command-1",
    }
    value.update(overrides)
    return value


def _accept(repo: SqlRepository, *, runtime_generation: int = 1) -> dict:
    return repo.mark_agent_runtime_turn_accepted(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
        runtime_session_id=f"runtime-session-{runtime_generation}",
        runtime_generation=runtime_generation,
        accepted_mode="pi",
    )


def test_deterministic_runtime_preserves_authoritative_turn_identity():
    runtime = DeterministicAgentRuntime()
    runtime.start_or_resume(CaseContextSnapshot(
        diagnosis_id="diag-runtime",
        case_id="RW-RUNTIME-1",
        runtime_generation=1,
    ))
    accepted = runtime.submit_turn(AgentTurnInput(
        diagnosis_id="diag-runtime",
        turn_id="turn-authoritative",
        runtime_generation=1,
        message="inspect evidence",
        client_command_id="command-authoritative",
    ))
    replay = runtime.submit_turn(AgentTurnInput(
        diagnosis_id="diag-runtime",
        turn_id="turn-retry-provisional",
        runtime_generation=1,
        message="inspect evidence",
        client_command_id="command-authoritative",
    ))

    assert accepted.turn_id == "turn-authoritative"
    assert replay == accepted


def test_turn_idempotency_is_scoped_and_payload_is_immutable():
    repo = SqlRepository()
    first = repo.record_agent_runtime_turn(**_turn())
    replay = repo.record_agent_runtime_turn(**_turn(turn_id="ignored-on-replay"))

    assert replay["turn_id"] == first["turn_id"]
    with pytest.raises(ValueError, match="client_command_id 已用于不同请求"):
        repo.record_agent_runtime_turn(**_turn(user_message="different request"))

    _create_diagnosis("diag-runtime-other")
    other = repo.record_agent_runtime_turn(**_turn(
        diagnosis_id="diag-runtime-other",
        turn_id="turn-other",
    ))
    assert other["turn_id"] == "turn-other"


def test_unknown_acceptance_can_be_recovered_by_command_identity():
    repo = SqlRepository()
    repo.record_agent_runtime_turn(**_turn(status="SUBMITTING"))
    repo.mark_agent_runtime_turn_acceptance_unknown(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
        detail="sidecar timeout",
    )

    unknown = repo.get_agent_runtime_turn_by_command(
        diagnosis_id="diag-runtime",
        client_command_id="command-1",
    )
    assert unknown["status"] == "ACCEPTANCE_UNKNOWN"

    accepted = repo.mark_agent_runtime_turn_accepted(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
        runtime_session_id="runtime-session-1",
        runtime_generation=1,
        accepted_mode="pi",
    )
    assert accepted["status"] == "ACCEPTED"


def test_stale_generation_and_sealed_turn_events_are_rejected():
    repo = SqlRepository()
    repo.upsert_agent_runtime_binding(
        diagnosis_id="diag-runtime",
        runtime_type="pi",
        runtime_version="0.83.0",
        runtime_session_id="runtime-session-1",
        runtime_generation=2,
    )
    repo.record_agent_runtime_turn(**_turn(runtime_generation=2))
    _accept(repo, runtime_generation=2)

    with pytest.raises(ValueError, match="stale runtime generation"):
        repo.record_agent_runtime_event(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            runtime_generation=1,
            event_seq=1,
            event_type="assistant.delta",
            payload={"text": "late"},
            event_id="event-stale",
        )

    repo.seal_agent_runtime_turn(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
        terminal_status="COMPLETED",
        final_message={"text": "final"},
    )
    with pytest.raises(ValueError, match="sealed turn"):
        repo.record_agent_runtime_event(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            runtime_generation=2,
            event_seq=2,
            event_type="assistant.delta",
            payload={"text": "too late"},
            event_id="event-late",
        )


def test_finalization_uses_next_sequence_after_sparse_events():
    repo = SqlRepository()
    repo.record_agent_runtime_turn(**_turn())
    repo.record_agent_runtime_event(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
        runtime_generation=1,
        event_seq=7,
        event_type="assistant.delta",
        payload={"text": "partial"},
        event_id="event-sparse",
    )
    _accept(repo)

    repo.seal_agent_runtime_turn(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
        terminal_status="COMPLETED",
        final_message={"text": "final"},
    )

    events = repo.list_agent_runtime_events(
        diagnosis_id="diag-runtime", turn_id="turn-1",
    )
    assert [event["event_seq"] for event in events] == [7, 8]
    assert events[-1]["event_type"] == "turn.completed"


def test_finalization_is_exactly_once_and_replay_must_match():
    repo = SqlRepository()
    repo.record_agent_runtime_turn(**_turn())
    _accept(repo)
    first = repo.seal_agent_runtime_turn(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
        terminal_status="COMPLETED",
        final_message={"text": "final answer", "evidence_ids": ["e-1"]},
    )
    replay = repo.seal_agent_runtime_turn(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
        terminal_status="COMPLETED",
        final_message={"text": "final answer", "evidence_ids": ["e-1"]},
    )

    assert replay["final_message"] == first["final_message"]
    assert len(repo.list_agent_runtime_events(
        diagnosis_id="diag-runtime", turn_id="turn-1",
    )) == 1

    with pytest.raises(ValueError, match="different finalization"):
        repo.seal_agent_runtime_turn(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            terminal_status="COMPLETED",
            final_message={"text": "changed answer"},
        )


@pytest.mark.parametrize("status", ["SUBMITTING", "ACCEPTANCE_UNKNOWN"])
def test_completion_requires_confirmed_acceptance(status: str):
    repo = SqlRepository()
    repo.record_agent_runtime_turn(**_turn())
    if status == "ACCEPTANCE_UNKNOWN":
        repo.mark_agent_runtime_turn_acceptance_unknown(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            detail="sidecar timeout",
        )

    with pytest.raises(ValueError, match=f"{status} -> COMPLETED"):
        repo.seal_agent_runtime_turn(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            terminal_status="COMPLETED",
            final_message={"text": "must not be accepted as complete"},
        )


@pytest.mark.parametrize(
    ("terminal_status", "source_status"),
    [
        ("FAILED", "SUBMITTING"),
        ("FAILED", "ACCEPTANCE_UNKNOWN"),
        ("FAILED", "ACCEPTED"),
        ("CANCELLED", "SUBMITTING"),
        ("CANCELLED", "ACCEPTANCE_UNKNOWN"),
        ("CANCELLED", "ACCEPTED"),
    ],
)
def test_failure_and_cancellation_allow_defined_source_states(
    terminal_status: str, source_status: str,
):
    repo = SqlRepository()
    repo.record_agent_runtime_turn(**_turn())
    if source_status == "ACCEPTANCE_UNKNOWN":
        repo.mark_agent_runtime_turn_acceptance_unknown(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            detail="sidecar timeout",
        )
    elif source_status == "ACCEPTED":
        _accept(repo)

    sealed = repo.seal_agent_runtime_turn(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
        terminal_status=terminal_status,
        final_message={"reason": terminal_status.lower()},
    )

    assert sealed["status"] == terminal_status
    assert sealed["sealed_at"] is not None


def test_repeated_acceptance_must_match_runtime_metadata():
    repo = SqlRepository()
    repo.upsert_agent_runtime_binding(
        diagnosis_id="diag-runtime",
        runtime_type="pi",
        runtime_version="0.83.0",
        runtime_session_id="runtime-session-1",
        runtime_generation=1,
    )
    repo.record_agent_runtime_turn(**_turn())
    _accept(repo)

    with pytest.raises(ValueError, match="different runtime turn transition"):
        repo.mark_agent_runtime_turn_accepted(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            runtime_session_id="runtime-session-other",
            runtime_generation=1,
            accepted_mode="pi",
        )
    with pytest.raises(ValueError, match="different runtime turn transition"):
        repo.mark_agent_runtime_turn_accepted(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            runtime_session_id="runtime-session-1",
            runtime_generation=1,
            accepted_mode="deterministic",
        )


def test_stale_binding_generation_rejects_finalization():
    repo = SqlRepository()
    repo.upsert_agent_runtime_binding(
        diagnosis_id="diag-runtime",
        runtime_type="pi",
        runtime_version="0.83.0",
        runtime_session_id="runtime-session-1",
        runtime_generation=1,
    )
    repo.record_agent_runtime_turn(**_turn())
    _accept(repo)
    repo.upsert_agent_runtime_binding(
        diagnosis_id="diag-runtime",
        runtime_type="pi",
        runtime_version="0.83.0",
        runtime_session_id="runtime-session-2",
        runtime_generation=2,
    )

    with pytest.raises(ValueError, match="stale runtime generation"):
        repo.seal_agent_runtime_turn(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            terminal_status="COMPLETED",
            final_message={"text": "late generation"},
        )


def test_runtime_event_identity_and_sequence_are_immutable():
    repo = SqlRepository()
    repo.record_agent_runtime_turn(**_turn())
    event = {
        "diagnosis_id": "diag-runtime",
        "turn_id": "turn-1",
        "runtime_generation": 1,
        "event_seq": 1,
        "event_type": "assistant.delta",
        "payload": {"text": "partial"},
        "event_id": "event-1",
    }
    repo.record_agent_runtime_event(**event)
    replay = repo.record_agent_runtime_event(**event)
    assert replay["duplicate"] is True

    with pytest.raises(ValueError, match="event_id 已用于不同事件"):
        repo.record_agent_runtime_event(**{**event, "payload": {"text": "changed"}})
    with pytest.raises(ValueError, match="runtime event sequence already exists"):
        repo.record_agent_runtime_event(**{**event, "event_id": "event-2"})


def test_runtime_event_cannot_claim_a_turn_from_another_diagnosis():
    repo = SqlRepository()
    repo.record_agent_runtime_turn(**_turn())
    _create_diagnosis("diag-runtime-other")

    with pytest.raises(ValueError, match="runtime turn does not exist"):
        repo.record_agent_runtime_event(
            diagnosis_id="diag-runtime-other",
            turn_id="turn-1",
            runtime_generation=1,
            event_seq=1,
            event_type="assistant.delta",
            payload={"text": "misattributed"},
            event_id="event-cross-diagnosis",
        )
