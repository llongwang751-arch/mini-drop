from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread

import pytest
from fastapi.testclient import TestClient

from server.app.agent_runtime.pi_adapter import (
    PiAcceptanceUnknown,
    PiDefinitiveRejection,
)
from server.app.agent_runtime.port import (
    AcceptedTurn,
    AgentTurnInput,
    CaseContextSnapshot,
    RuntimeBinding,
    RuntimeState,
)
from server.app.database import _get_engine, init_db, reset_engine
from server.app.diagnosis.store import DiagnosisStore
from server.app.main import app
from server.app.models import AgentRuntimeTurnModel, Base
from server.app.sql_repository import SqlRepository


DIAGNOSIS_ID = "diag-public-runtime"
GATEWAY_TOKEN = "runtime-gateway-token"


class FakeRuntime:
    runtime_type = "fake"
    runtime_version = "1"

    def __init__(self) -> None:
        self.start_calls: list[CaseContextSnapshot] = []
        self.submit_calls: list[AgentTurnInput] = []
        self.accepted_commands: dict[tuple[str, str], AcceptedTurn] = {}
        self.submit_error: Exception | None = None
        self.submit_response: AcceptedTurn | None = None
        self.before_start = None
        self.before_submit = None
        self.before_reconcile = None
        self._lock = Lock()

    def start_or_resume(self, context: CaseContextSnapshot) -> RuntimeBinding:
        with self._lock:
            self.start_calls.append(context)
        if self.before_start is not None:
            self.before_start(context)
        return RuntimeBinding(
            diagnosis_id=context.diagnosis_id,
            runtime_session_id=f"fake:{context.diagnosis_id}:{context.runtime_generation}",
            runtime_generation=context.runtime_generation,
            runtime_type=self.runtime_type,
            runtime_version=self.runtime_version,
        )

    def submit_turn(self, turn: AgentTurnInput) -> AcceptedTurn:
        with self._lock:
            self.submit_calls.append(turn)
        if self.before_submit is not None:
            self.before_submit(turn)
        if self.submit_error is not None:
            raise self.submit_error
        accepted = self.submit_response or self.accepted(turn)
        self.accepted_commands.setdefault(
            (turn.diagnosis_id, turn.client_command_id),
            accepted,
        )
        return accepted

    def get_accepted_turn(
        self, diagnosis_id: str, client_command_id: str,
    ) -> AcceptedTurn | None:
        if self.before_reconcile is not None:
            self.before_reconcile(diagnosis_id, client_command_id)
        return self.accepted_commands.get((diagnosis_id, client_command_id))

    @staticmethod
    def accepted(turn: AgentTurnInput) -> AcceptedTurn:
        return AcceptedTurn(
            turn_id=turn.turn_id,
            runtime_session_id=f"fake:{turn.diagnosis_id}:{turn.runtime_generation}",
            runtime_generation=turn.runtime_generation,
            accepted=True,
            mode="fake",
            detail="accepted",
        )

    def get_state(self, diagnosis_id: str) -> RuntimeState:
        return RuntimeState(
            diagnosis_id=diagnosis_id,
            runtime_session_id=f"fake:{diagnosis_id}:1",
            runtime_generation=1,
            status="READY",
        )

    def seal_turn(self, diagnosis_id: str, turn_id: str) -> None:
        pass

    def cancel_turn(self, diagnosis_id: str, turn_id: str, reason: str) -> None:
        pass


@pytest.fixture(autouse=True)
def _runtime_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINIO_AUTO_CREATE_BUCKET", "0")
    monkeypatch.setenv("MINI_DROP_EMBED_GRPC", "0")
    monkeypatch.setenv("MINI_DROP_EMBED_MAINTENANCE", "0")
    monkeypatch.setenv("MINI_DROP_OUTBOX_DISPATCH_ENABLED", "0")
    monkeypatch.setenv("MINI_DROP_API_AUTH_ENABLED", "1")
    monkeypatch.setenv("MINI_DROP_INTERNAL_GATEWAY_TOKEN", GATEWAY_TOKEN)
    monkeypatch.delenv("MINI_DROP_API_KEY", raising=False)
    reset_engine()
    init_db()
    _create_diagnosis(DIAGNOSIS_ID, "alice")
    yield
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture()
def runtime(monkeypatch) -> FakeRuntime:
    fake = FakeRuntime()
    monkeypatch.setattr(
        "server.app.agent_runtime.turn_service.get_runtime",
        lambda: fake,
    )
    return fake


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _create_diagnosis(diagnosis_id: str, creator_id: str) -> None:
    DiagnosisStore().create_session({
        "diagnosis_id": diagnosis_id,
        "case_id": f"case-{diagnosis_id}",
        "creator_id": creator_id,
        "raw_query": "diagnose the current runtime evidence",
        "target_scope": {"service": "checkout"},
        "status": "ANALYZING",
        "policy_profile": "balanced",
        "model_version": "test-model",
        "planner_version": "test-planner",
        "deadline_at": datetime.now(timezone.utc),
    })


def _headers(principal: str = "alice") -> dict[str, str]:
    return {
        "X-Mini-Drop-Gateway-Token": GATEWAY_TOKEN,
        "X-Mini-Drop-Principal": principal,
    }


def _path(diagnosis_id: str = DIAGNOSIS_ID) -> str:
    return f"/api/v1/diagnoses/{diagnosis_id}/turns"


def _body(**overrides) -> dict:
    value = {
        "message": "continue the diagnosis",
        "client_command_id": "command-1",
        "requested_mode": "COLLABORATE",
    }
    value.update(overrides)
    return value


def test_submission_persists_authority_before_runtime_and_derives_policy(
    client: TestClient,
    runtime: FakeRuntime,
) -> None:
    persisted: list[dict] = []

    def inspect_before_binding(context: CaseContextSnapshot) -> None:
        row = SqlRepository().get_agent_runtime_turn_by_command(
            diagnosis_id=context.diagnosis_id,
            client_command_id="command-1",
        )
        assert row is not None
        assert row["status"] == "SUBMITTING"
        assert row["runtime_session_id"] is None
        persisted.append(row)

    def inspect_persisted(turn: AgentTurnInput) -> None:
        row = SqlRepository().get_agent_runtime_turn_by_command(
            diagnosis_id=turn.diagnosis_id,
            client_command_id=turn.client_command_id,
        )
        assert row is not None
        persisted.append(row)

    runtime.before_start = inspect_before_binding
    runtime.before_submit = inspect_persisted
    response = client.post(_path(), headers=_headers(), json=_body())

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "ACCEPTED"
    assert data["turn_id"].startswith("turn-")
    assert data["actor_id"] == "alice"
    assert data["side_effect_policy"] == "READ_ONLY"
    assert data["runtime_generation"] == 1
    assert persisted[0]["status"] == "SUBMITTING"
    assert persisted[0]["turn_id"] == data["turn_id"]
    assert runtime.submit_calls[0].turn_id == data["turn_id"]


@pytest.mark.parametrize(
    "body",
    [
        _body(actor_id="mallory"),
        _body(turn_id="client-turn"),
        _body(side_effect_policy="WRITE"),
        _body(requested_mode="AUTONOMOUS"),
    ],
)
def test_public_request_rejects_client_authority_and_unknown_modes(
    client: TestClient,
    runtime: FakeRuntime,
    body: dict,
) -> None:
    response = client.post(_path(), headers=_headers(), json=body)

    assert response.status_code == 422
    assert runtime.start_calls == []
    assert runtime.submit_calls == []


def test_authentication_and_ownership_fail_before_runtime(
    client: TestClient,
    runtime: FakeRuntime,
) -> None:
    unauthenticated = client.post(_path(), json=_body())
    missing = client.post(_path("diag-missing"), headers=_headers(), json=_body())
    non_owner = client.post(_path(), headers=_headers("bob"), json=_body())

    assert unauthenticated.status_code == 401
    assert missing.status_code == 404
    assert non_owner.status_code == 404
    assert missing.json()["detail"] == non_owner.json()["detail"]
    assert runtime.start_calls == []
    assert runtime.submit_calls == []


def test_exact_command_replay_reuses_committed_winner_and_changed_replay_conflicts(
    client: TestClient,
    runtime: FakeRuntime,
) -> None:
    first = client.post(_path(), headers=_headers(), json=_body())
    replay = client.post(_path(), headers=_headers(), json=_body())
    changed = client.post(
        _path(),
        headers=_headers(),
        json=_body(message="different request"),
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["data"]["turn_id"] == first.json()["data"]["turn_id"]
    assert changed.status_code == 409
    assert len(runtime.start_calls) == 1
    assert len(runtime.submit_calls) == 1


def test_ordinary_turns_reuse_binding_without_generation_rotation(
    client: TestClient,
    runtime: FakeRuntime,
) -> None:
    first = client.post(_path(), headers=_headers(), json=_body())
    second = client.post(
        _path(),
        headers=_headers(),
        json=_body(message="next instruction", client_command_id="command-2"),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(runtime.start_calls) == 1
    assert [turn.runtime_generation for turn in runtime.submit_calls] == [1, 1]
    assert first.json()["data"]["runtime_session_id"] == second.json()["data"]["runtime_session_id"]


def test_acceptance_uncertainty_reconciles_before_exact_retry(
    client: TestClient,
    runtime: FakeRuntime,
) -> None:
    runtime.submit_error = PiAcceptanceUnknown("connection closed; acceptance unknown")
    first = client.post(_path(), headers=_headers(), json=_body())
    replay = client.post(_path(), headers=_headers(), json=_body())

    assert first.status_code == 202
    assert replay.status_code == 202
    uncertain = first.json()["data"]
    assert uncertain["status"] == "ACCEPTANCE_UNKNOWN"
    assert replay.json()["data"]["turn_id"] == uncertain["turn_id"]
    assert len(runtime.submit_calls) == 2
    assert runtime.submit_calls[0] == runtime.submit_calls[1]
    assert runtime.submit_calls[0].turn_id == uncertain["turn_id"]

    runtime.accepted_commands[(DIAGNOSIS_ID, "command-1")] = AcceptedTurn(
        turn_id=uncertain["turn_id"],
        runtime_session_id=f"fake:{DIAGNOSIS_ID}:1",
        runtime_generation=1,
        accepted=True,
        mode="fake",
        detail="recovered",
    )
    recovered = client.post(_path(), headers=_headers(), json=_body())

    assert recovered.status_code == 200
    assert recovered.json()["data"]["status"] == "ACCEPTED"
    assert recovered.json()["data"]["turn_id"] == uncertain["turn_id"]
    assert len(runtime.submit_calls) == 2


def test_unbound_submitting_winner_recovers_after_process_crash(
    client: TestClient,
    runtime: FakeRuntime,
) -> None:
    repo = SqlRepository()
    repo.record_agent_runtime_turn(
        diagnosis_id=DIAGNOSIS_ID,
        turn_id="turn-before-crash",
        runtime_generation=1,
        user_message="continue the diagnosis",
        requested_mode="COLLABORATE",
        side_effect_policy="READ_ONLY",
        actor_id="alice",
        client_command_id="command-1",
    )
    reconciled = []
    runtime.before_reconcile = lambda *identity: reconciled.append(identity)

    response = client.post(_path(), headers=_headers(), json=_body())

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "ACCEPTED"
    assert data["turn_id"] == "turn-before-crash"
    assert data["runtime_session_id"] == f"fake:{DIAGNOSIS_ID}:1"
    assert reconciled == [(DIAGNOSIS_ID, "command-1")]
    assert len(runtime.start_calls) == 1
    assert len(runtime.submit_calls) == 1
    assert runtime.submit_calls[0].turn_id == "turn-before-crash"


def test_pending_winner_settles_from_accepted_lookup_without_submit(
    client: TestClient,
    runtime: FakeRuntime,
) -> None:
    repo = SqlRepository()
    repo.upsert_agent_runtime_binding(
        diagnosis_id=DIAGNOSIS_ID,
        runtime_type="fake",
        runtime_version="1",
        runtime_session_id=f"fake:{DIAGNOSIS_ID}:1",
        runtime_generation=1,
    )
    repo.record_agent_runtime_turn(
        diagnosis_id=DIAGNOSIS_ID,
        turn_id="turn-accepted-before-crash",
        runtime_generation=1,
        user_message="continue the diagnosis",
        requested_mode="COLLABORATE",
        side_effect_policy="READ_ONLY",
        actor_id="alice",
        client_command_id="command-1",
        runtime_session_id=f"fake:{DIAGNOSIS_ID}:1",
    )
    runtime.accepted_commands[(DIAGNOSIS_ID, "command-1")] = AcceptedTurn(
        turn_id="turn-accepted-before-crash",
        runtime_session_id=f"fake:{DIAGNOSIS_ID}:1",
        runtime_generation=1,
        accepted=True,
        mode="fake",
    )

    response = client.post(_path(), headers=_headers(), json=_body())

    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "ACCEPTED"
    assert response.json()["data"]["turn_id"] == "turn-accepted-before-crash"
    assert runtime.start_calls == []
    assert runtime.submit_calls == []


def test_mismatched_accepted_lookup_preserves_unknown_winner(
    client: TestClient,
    runtime: FakeRuntime,
) -> None:
    repo = SqlRepository()
    repo.upsert_agent_runtime_binding(
        diagnosis_id=DIAGNOSIS_ID,
        runtime_type="fake",
        runtime_version="1",
        runtime_session_id=f"fake:{DIAGNOSIS_ID}:1",
        runtime_generation=1,
    )
    repo.record_agent_runtime_turn(
        diagnosis_id=DIAGNOSIS_ID,
        turn_id="turn-committed",
        runtime_generation=1,
        user_message="continue the diagnosis",
        requested_mode="COLLABORATE",
        side_effect_policy="READ_ONLY",
        actor_id="alice",
        client_command_id="command-1",
        runtime_session_id=f"fake:{DIAGNOSIS_ID}:1",
    )
    repo.mark_agent_runtime_turn_acceptance_unknown(
        diagnosis_id=DIAGNOSIS_ID,
        turn_id="turn-committed",
        detail="previous response was uncertain",
    )
    runtime.accepted_commands[(DIAGNOSIS_ID, "command-1")] = AcceptedTurn(
        turn_id="turn-substituted",
        runtime_session_id=f"fake:{DIAGNOSIS_ID}:1",
        runtime_generation=1,
        accepted=True,
        mode="fake",
    )

    response = client.post(_path(), headers=_headers(), json=_body())

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["status"] == "ACCEPTANCE_UNKNOWN"
    assert data["turn_id"] == "turn-committed"
    assert runtime.submit_calls == []


def test_substituted_submit_authority_can_reconcile_to_committed_winner(
    client: TestClient,
    runtime: FakeRuntime,
) -> None:
    def provide_recovery(turn: AgentTurnInput) -> None:
        runtime.accepted_commands[(turn.diagnosis_id, turn.client_command_id)] = runtime.accepted(turn)
        runtime.submit_response = AcceptedTurn(
            turn_id="sidecar-invented",
            runtime_session_id=f"fake:{turn.diagnosis_id}:1",
            runtime_generation=1,
            accepted=True,
            mode="fake",
        )

    runtime.before_submit = provide_recovery
    response = client.post(_path(), headers=_headers(), json=_body())

    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "ACCEPTED"
    assert response.json()["data"]["turn_id"] != "sidecar-invented"
    assert len(runtime.submit_calls) == 1


def test_substituted_submit_authority_without_valid_reconciliation_stays_unknown(
    client: TestClient,
    runtime: FakeRuntime,
) -> None:
    runtime.submit_response = AcceptedTurn(
        turn_id="sidecar-invented",
        runtime_session_id=f"fake:{DIAGNOSIS_ID}:1",
        runtime_generation=1,
        accepted=True,
        mode="fake",
    )

    response = client.post(_path(), headers=_headers(), json=_body())

    assert response.status_code == 202
    persisted = SqlRepository().get_agent_runtime_turn_by_command(
        diagnosis_id=DIAGNOSIS_ID,
        client_command_id="command-1",
    )
    assert persisted is not None
    assert persisted["status"] == "ACCEPTANCE_UNKNOWN"
    assert persisted["turn_id"] != "sidecar-invented"


def test_definitive_rejection_is_finalized_locally(
    client: TestClient,
    runtime: FakeRuntime,
) -> None:
    runtime.submit_error = PiDefinitiveRejection("request rejected")

    response = client.post(_path(), headers=_headers(), json=_body())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "FAILED"
    assert data["sealed_at"] is not None
    assert data["final_message"] == {"error": "request rejected"}


def test_late_definitive_rejection_cannot_overwrite_accepted_winner(
    client: TestClient,
    runtime: FakeRuntime,
) -> None:
    def accept_before_rejection(turn: AgentTurnInput) -> None:
        accepted = runtime.accepted(turn)
        SqlRepository().mark_agent_runtime_turn_accepted(
            diagnosis_id=turn.diagnosis_id,
            turn_id=turn.turn_id,
            runtime_session_id=accepted.runtime_session_id,
            runtime_generation=accepted.runtime_generation,
            accepted_mode=accepted.mode,
        )

    runtime.before_submit = accept_before_rejection
    runtime.submit_error = PiDefinitiveRejection("late rejection")

    response = client.post(_path(), headers=_headers(), json=_body())

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["status"] == "ACCEPTED"
    assert data["sealed_at"] is None
    assert data["final_message"] is None


@pytest.mark.parametrize(
    ("stored_type", "stored_version"),
    [("other", "1"), ("fake", "2")],
)
def test_incompatible_stored_binding_fails_before_runtime_io(
    client: TestClient,
    runtime: FakeRuntime,
    stored_type: str,
    stored_version: str,
) -> None:
    SqlRepository().upsert_agent_runtime_binding(
        diagnosis_id=DIAGNOSIS_ID,
        runtime_type=stored_type,
        runtime_version=stored_version,
        runtime_session_id="stored-session",
        runtime_generation=1,
    )

    response = client.post(_path(), headers=_headers(), json=_body())

    assert response.status_code == 503
    assert runtime.start_calls == []
    assert runtime.submit_calls == []
    assert SqlRepository().get_agent_runtime_turn_by_command(
        diagnosis_id=DIAGNOSIS_ID,
        client_command_id="command-1",
    ) is None


def test_incompatible_binding_fences_existing_pending_turn_before_reconcile(
    client: TestClient,
    runtime: FakeRuntime,
) -> None:
    repo = SqlRepository()
    repo.upsert_agent_runtime_binding(
        diagnosis_id=DIAGNOSIS_ID,
        runtime_type="other",
        runtime_version="1",
        runtime_session_id="stored-session",
        runtime_generation=1,
    )
    repo.record_agent_runtime_turn(
        diagnosis_id=DIAGNOSIS_ID,
        turn_id="turn-existing",
        runtime_generation=1,
        user_message="continue the diagnosis",
        requested_mode="COLLABORATE",
        side_effect_policy="READ_ONLY",
        actor_id="alice",
        client_command_id="command-1",
        runtime_session_id="stored-session",
    )
    reconciled = []
    runtime.before_reconcile = lambda *_: reconciled.append(True)

    response = client.post(_path(), headers=_headers(), json=_body())

    assert response.status_code == 503
    assert runtime.start_calls == []
    assert runtime.submit_calls == []
    assert reconciled == []


def test_expired_lease_takeover_fences_slow_runtime_writeback(
    client: TestClient,
    runtime: FakeRuntime,
) -> None:
    first_lookup_started = Event()
    release_first_lookup = Event()
    lookup_count = 0
    lookup_lock = Lock()

    def block_first_lookup(
        diagnosis_id: str,
        client_command_id: str,
    ) -> None:
        nonlocal lookup_count
        with lookup_lock:
            lookup_count += 1
            current_lookup = lookup_count
        if current_lookup == 1:
            first_lookup_started.set()
            assert release_first_lookup.wait(timeout=5)

    runtime.before_reconcile = block_first_lookup
    responses = []

    def send() -> None:
        with TestClient(app) as thread_client:
            responses.append(
                thread_client.post(_path(), headers=_headers(), json=_body())
            )

    first = Thread(target=send)
    first.start()
    assert first_lookup_started.wait(timeout=5)

    repo = SqlRepository()
    winner = repo.get_agent_runtime_turn_by_command(
        diagnosis_id=DIAGNOSIS_ID,
        client_command_id="command-1",
    )
    assert winner is not None
    stale_owner = winner["recovery_owner"]
    stale_token = winner["recovery_fencing_token"]
    with repo._write_session() as session:
        row = session.query(AgentRuntimeTurnModel).filter_by(
            diagnosis_id=DIAGNOSIS_ID,
            turn_id=winner["turn_id"],
        ).one()
        row.recovery_lease_expires_at = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        )

    second = Thread(target=send)
    second.start()
    second.join(timeout=5)
    release_first_lookup.set()
    first.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(responses) == 2
    assert all(response.status_code == 200 for response in responses)
    assert len(runtime.submit_calls) == 1
    assert runtime.submit_calls[0].turn_id == winner["turn_id"]
    settled = repo.get_agent_runtime_turn(
        diagnosis_id=DIAGNOSIS_ID,
        turn_id=winner["turn_id"],
    )
    assert settled is not None
    assert settled["status"] == "ACCEPTED"
    assert settled["recovery_fencing_token"] == stale_token + 1
    assert settled["recovery_owner"] is None
    with pytest.raises(ValueError, match="stale runtime recovery authority"):
        repo.release_agent_runtime_turn_recovery(
            diagnosis_id=DIAGNOSIS_ID,
            turn_id=winner["turn_id"],
            recovery_owner=stale_owner,
            recovery_fencing_token=stale_token,
        )


def test_concurrent_duplicate_command_has_one_runtime_submission(
    client: TestClient,
    runtime: FakeRuntime,
) -> None:
    submitted = Event()
    release = Event()

    def block_first_submission(turn: AgentTurnInput) -> None:
        submitted.set()
        assert release.wait(timeout=5)

    runtime.before_submit = block_first_submission
    responses = []

    def send() -> None:
        with TestClient(app) as thread_client:
            responses.append(thread_client.post(_path(), headers=_headers(), json=_body()))

    first = Thread(target=send)
    first.start()
    assert submitted.wait(timeout=5)
    second = Thread(target=send)
    second.start()
    second.join(timeout=5)
    release.set()
    first.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(runtime.start_calls) == 1
    assert len(runtime.submit_calls) == 1
    assert {response.status_code for response in responses} <= {200, 202}
    turn_ids = {response.json()["data"]["turn_id"] for response in responses}
    assert len(turn_ids) == 1
