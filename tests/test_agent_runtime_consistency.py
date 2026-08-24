from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

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
    binding = repo.get_agent_runtime_binding(diagnosis_id="diag-runtime")
    runtime_session_id = f"runtime-session-{runtime_generation}"
    if binding is None:
        repo.upsert_agent_runtime_binding(
            diagnosis_id="diag-runtime",
            runtime_type="pi",
            runtime_version="0.83.0",
            runtime_session_id=runtime_session_id,
            runtime_generation=runtime_generation,
        )
    return repo.mark_agent_runtime_turn_accepted(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
        runtime_session_id=runtime_session_id,
        runtime_generation=runtime_generation,
        accepted_mode="pi",
    )


def _seal_runtime(
    repo: SqlRepository,
    *,
    terminal_status: str,
    final_message: dict,
    runtime_generation: int = 1,
    runtime_session_id: str | None = None,
) -> dict:
    return repo.seal_agent_runtime_turn(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
        runtime_session_id=(
            runtime_session_id or f"runtime-session-{runtime_generation}"
        ),
        runtime_generation=runtime_generation,
        terminal_status=terminal_status,
        final_message=final_message,
    )


def test_concurrent_duplicate_command_returns_committed_winner(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'runtime.db'}")
    reset_engine()
    init_db()
    _create_diagnosis("diag-runtime")
    barrier = Barrier(2)

    def record_once(turn_id: str):
        repo = SqlRepository()
        barrier.wait()
        return repo.record_agent_runtime_turn(**_turn(turn_id=turn_id))

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(record_once, ["turn-race-a", "turn-race-b"]))

    assert results[0]["turn_id"] == results[1]["turn_id"]
    assert results[0]["client_command_id"] == "command-1"
    assert SqlRepository().get_agent_runtime_turn_by_command(
        diagnosis_id="diag-runtime", client_command_id="command-1",
    )["turn_id"] == results[0]["turn_id"]


def test_concurrent_duplicate_command_with_changed_payload_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'runtime.db'}")
    reset_engine()
    init_db()
    _create_diagnosis("diag-runtime")
    barrier = Barrier(2)

    def record_once(message: str):
        repo = SqlRepository()
        barrier.wait()
        try:
            return repo.record_agent_runtime_turn(**_turn(user_message=message))
        except ValueError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(record_once, ["same request", "different request"]))

    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert sum(isinstance(result, dict) for result in results) == 1
    assert SqlRepository().get_agent_runtime_turn_by_command(
        diagnosis_id="diag-runtime", client_command_id="command-1",
    ) is not None


def test_turn_identity_collision_without_command_collision_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'runtime.db'}")
    reset_engine()
    init_db()
    _create_diagnosis("diag-runtime")
    repo = SqlRepository()
    repo.record_agent_runtime_turn(**_turn())

    with pytest.raises(ValueError, match="turn identity already exists"):
        repo.record_agent_runtime_turn(
            **_turn(turn_id="turn-1", client_command_id="command-2")
        )


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
    assert accepted.runtime_session_id == "deterministic:diag-runtime"
    assert accepted.runtime_generation == 1
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


def test_concurrent_recovery_claim_has_one_live_lease_winner(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'runtime.db'}")
    reset_engine()
    init_db()
    _create_diagnosis("diag-runtime")
    SqlRepository().record_agent_runtime_turn(**_turn())
    barrier = Barrier(2)

    def claim(owner: str):
        barrier.wait()
        return SqlRepository().claim_agent_runtime_turn_recovery(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            recovery_owner=owner,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ["owner-a", "owner-b"]))

    winners = [result for result in results if result["recovery_claimed"]]
    losers = [result for result in results if not result["recovery_claimed"]]
    assert len(winners) == 1
    assert len(losers) == 1
    assert winners[0]["recovery_owner"] in {"owner-a", "owner-b"}
    assert losers[0]["recovery_owner"] == winners[0]["recovery_owner"]
    assert winners[0]["recovery_fencing_token"] == 1
    assert losers[0]["recovery_fencing_token"] == 1


def test_expired_recovery_lease_fences_stale_owner_operations():
    repo = SqlRepository()
    repo.record_agent_runtime_turn(**_turn())
    first = repo.claim_agent_runtime_turn_recovery(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
        recovery_owner="owner-old",
        lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    second = repo.claim_agent_runtime_turn_recovery(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
        recovery_owner="owner-new",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    assert first["recovery_claimed"] is True
    assert first["recovery_fencing_token"] == 1
    assert second["recovery_claimed"] is True
    assert second["recovery_owner"] == "owner-new"
    assert second["recovery_fencing_token"] == 2

    repo.upsert_agent_runtime_binding(
        diagnosis_id="diag-runtime",
        runtime_type="pi",
        runtime_version="0.83.0",
        runtime_session_id="runtime-session-1",
        runtime_generation=1,
    )
    stale_authority = {
        "recovery_owner": "owner-old",
        "recovery_fencing_token": first["recovery_fencing_token"],
    }
    with pytest.raises(ValueError, match="stale runtime recovery authority"):
        repo.attach_agent_runtime_turn_authority(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            runtime_session_id="runtime-session-1",
            runtime_generation=1,
            **stale_authority,
        )
    with pytest.raises(ValueError, match="stale runtime recovery authority"):
        repo.mark_agent_runtime_turn_acceptance_unknown(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            detail="stale submit result",
            **stale_authority,
        )
    with pytest.raises(ValueError, match="stale runtime recovery authority"):
        repo.release_agent_runtime_turn_recovery(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            **stale_authority,
        )

    current = repo.get_agent_runtime_turn(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
    )
    assert current["status"] == "SUBMITTING"
    assert current["runtime_session_id"] is None
    assert current["recovery_owner"] == "owner-new"
    assert current["recovery_fencing_token"] == 2

    attached = repo.attach_agent_runtime_turn_authority(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
        runtime_session_id="runtime-session-1",
        runtime_generation=1,
        recovery_owner="owner-new",
        recovery_fencing_token=second["recovery_fencing_token"],
    )
    assert attached["recovery_phase"] == "SUBMIT_INTENT"


def test_unknown_acceptance_can_be_recovered_by_command_identity():
    repo = SqlRepository()
    repo.upsert_agent_runtime_binding(
        diagnosis_id="diag-runtime",
        runtime_type="pi",
        runtime_version="0.83.0",
        runtime_session_id="runtime-session-1",
        runtime_generation=1,
    )
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
        runtime_session_id="runtime-session-2",
        runtime_generation=2,
    )
    repo.record_agent_runtime_turn(**_turn(runtime_generation=2))
    _accept(repo, runtime_generation=2)

    with pytest.raises(ValueError, match="stale runtime generation"):
        repo.record_agent_runtime_event(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            runtime_session_id="runtime-session-2",
            runtime_generation=1,
            event_seq=1,
            event_type="assistant.delta",
            payload={"text": "late"},
            event_id="event-stale",
        )

    _seal_runtime(
        repo,
        runtime_generation=2,
        terminal_status="COMPLETED",
        final_message={"text": "final"},
    )
    with pytest.raises(ValueError, match="sealed turn"):
        repo.record_agent_runtime_event(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            runtime_session_id="runtime-session-2",
            runtime_generation=2,
            event_seq=2,
            event_type="assistant.delta",
            payload={"text": "too late"},
            event_id="event-late",
        )


def test_finalization_uses_next_sequence_after_sparse_events():
    repo = SqlRepository()
    repo.record_agent_runtime_turn(**_turn())
    _accept(repo)
    repo.record_agent_runtime_event(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
        runtime_session_id="runtime-session-1",
        runtime_generation=1,
        event_seq=7,
        event_type="assistant.delta",
        payload={"text": "partial"},
        event_id="event-sparse",
    )

    _seal_runtime(
        repo,
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
    first = _seal_runtime(
        repo,
        terminal_status="COMPLETED",
        final_message={"text": "final answer", "evidence_ids": ["e-1"]},
    )
    replay = _seal_runtime(
        repo,
        terminal_status="COMPLETED",
        final_message={"text": "final answer", "evidence_ids": ["e-1"]},
    )

    assert replay["final_message"] == first["final_message"]
    assert len(repo.list_agent_runtime_events(
        diagnosis_id="diag-runtime", turn_id="turn-1",
    )) == 1

    with pytest.raises(ValueError, match="different finalization"):
        _seal_runtime(
            repo,
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
        repo.finalize_agent_runtime_turn_locally(
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

    if source_status == "ACCEPTED":
        sealed = _seal_runtime(
            repo,
            terminal_status=terminal_status,
            final_message={"reason": terminal_status.lower()},
        )
    else:
        sealed = repo.finalize_agent_runtime_turn_locally(
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

    with pytest.raises(ValueError, match="stale runtime session"):
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
            runtime_session_id="runtime-session-1",
            runtime_generation=1,
            terminal_status="COMPLETED",
            final_message={"text": "late generation"},
        )


def test_runtime_event_identity_and_sequence_are_immutable():
    repo = SqlRepository()
    repo.record_agent_runtime_turn(**_turn())
    _accept(repo)
    event = {
        "diagnosis_id": "diag-runtime",
        "turn_id": "turn-1",
        "runtime_session_id": "runtime-session-1",
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


def test_same_generation_cannot_replace_runtime_binding_identity():
    repo = SqlRepository()
    repo.upsert_agent_runtime_binding(
        diagnosis_id="diag-runtime",
        runtime_type="pi",
        runtime_version="0.83.0",
        runtime_session_id="runtime-session-1",
        runtime_generation=1,
    )

    with pytest.raises(ValueError, match="different runtime binding replay"):
        repo.upsert_agent_runtime_binding(
            diagnosis_id="diag-runtime",
            runtime_type="pi",
            runtime_version="0.83.0",
            runtime_session_id="runtime-session-other",
            runtime_generation=1,
        )


@pytest.mark.parametrize("status", ["SUBMITTING", "ACCEPTANCE_UNKNOWN"])
def test_runtime_events_require_confirmed_acceptance(status: str):
    repo = SqlRepository()
    repo.upsert_agent_runtime_binding(
        diagnosis_id="diag-runtime",
        runtime_type="pi",
        runtime_version="0.83.0",
        runtime_session_id="runtime-session-1",
        runtime_generation=1,
    )
    repo.record_agent_runtime_turn(**_turn(runtime_session_id="runtime-session-1"))
    if status == "ACCEPTANCE_UNKNOWN":
        repo.mark_agent_runtime_turn_acceptance_unknown(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            detail="sidecar timeout",
        )

    with pytest.raises(ValueError, match="runtime turn is not accepted"):
        repo.record_agent_runtime_event(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            runtime_session_id="runtime-session-1",
            runtime_generation=1,
            event_seq=1,
            event_type="assistant.delta",
            payload={"text": "premature"},
            event_id=f"event-{status.lower()}",
        )


def test_wrong_session_callbacks_are_rejected():
    repo = SqlRepository()
    repo.record_agent_runtime_turn(**_turn())
    _accept(repo)

    with pytest.raises(ValueError, match="stale runtime session"):
        repo.record_agent_runtime_event(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            runtime_session_id="runtime-session-other",
            runtime_generation=1,
            event_seq=1,
            event_type="assistant.delta",
            payload={"text": "stale"},
            event_id="event-wrong-session",
        )
    with pytest.raises(ValueError, match="stale runtime session"):
        _seal_runtime(
            repo,
            runtime_session_id="runtime-session-other",
            terminal_status="COMPLETED",
            final_message={"text": "stale"},
        )


def test_rotated_binding_rejects_old_session_callbacks():
    repo = SqlRepository()
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
        repo.record_agent_runtime_event(
            diagnosis_id="diag-runtime",
            turn_id="turn-1",
            runtime_session_id="runtime-session-1",
            runtime_generation=1,
            event_seq=1,
            event_type="assistant.delta",
            payload={"text": "stale"},
            event_id="event-rotated-session",
        )


def test_runtime_callback_cannot_fail_an_unaccepted_turn():
    repo = SqlRepository()
    repo.upsert_agent_runtime_binding(
        diagnosis_id="diag-runtime",
        runtime_type="pi",
        runtime_version="0.83.0",
        runtime_session_id="runtime-session-1",
        runtime_generation=1,
    )
    repo.record_agent_runtime_turn(**_turn(runtime_session_id="runtime-session-1"))

    with pytest.raises(ValueError, match="SUBMITTING -> FAILED"):
        _seal_runtime(
            repo,
            terminal_status="FAILED",
            final_message={"reason": "untrusted callback"},
        )

    failed = repo.fail_agent_runtime_turn_locally(
        diagnosis_id="diag-runtime",
        turn_id="turn-1",
        final_message={"reason": "definitive local rejection"},
    )
    assert failed["status"] == "FAILED"


def test_runtime_event_cannot_claim_a_turn_from_another_diagnosis():
    repo = SqlRepository()
    repo.record_agent_runtime_turn(**_turn())
    _create_diagnosis("diag-runtime-other")

    with pytest.raises(ValueError, match="runtime turn does not exist"):
        repo.record_agent_runtime_event(
            diagnosis_id="diag-runtime-other",
            turn_id="turn-1",
            runtime_session_id="runtime-session-1",
            runtime_generation=1,
            event_seq=1,
            event_type="assistant.delta",
            payload={"text": "misattributed"},
            event_id="event-cross-diagnosis",
        )
