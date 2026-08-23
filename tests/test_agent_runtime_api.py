from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from server.app.database import _get_engine, init_db, reset_engine
from server.app.diagnosis.store import DiagnosisStore
from server.app.main import app
from server.app.models import Base
from server.app.sql_repository import SqlRepository


TOKEN = "runtime-api-contract-token"


@pytest.fixture(autouse=True)
def _runtime_api_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("MINIO_AUTO_CREATE_BUCKET", "0")
    monkeypatch.setenv("MINI_DROP_EMBED_GRPC", "0")
    monkeypatch.setenv("MINI_DROP_EMBED_MAINTENANCE", "0")
    monkeypatch.setenv("MINI_DROP_OUTBOX_DISPATCH_ENABLED", "0")
    reset_engine()
    init_db()
    _create_diagnosis("diag-runtime-api")
    _create_runtime_turn()
    yield
    Base.metadata.drop_all(bind=_get_engine())
    reset_engine()


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _create_diagnosis(diagnosis_id: str) -> None:
    now = datetime.now(timezone.utc)
    DiagnosisStore().create_session({
        "diagnosis_id": diagnosis_id,
        "case_id": "RW-RUNTIME-API-1",
        "creator_id": "alice",
        "raw_query": "validate runtime callback authority",
        "status": "ANALYZING",
        "policy_profile": "balanced",
        "model_version": "test-model",
        "planner_version": "test-planner",
        "deadline_at": now,
    })


def _create_runtime_turn() -> None:
    repo = SqlRepository()
    repo.upsert_agent_runtime_binding(
        diagnosis_id="diag-runtime-api",
        runtime_type="pi",
        runtime_version="pi-0.83.0",
        runtime_session_id="pi:diag-runtime-api:1",
        runtime_generation=1,
    )
    repo.record_agent_runtime_turn(
        diagnosis_id="diag-runtime-api",
        turn_id="turn-authoritative",
        runtime_generation=1,
        user_message="inspect verified evidence",
        requested_mode="COLLABORATE",
        side_effect_policy="READ_ONLY",
        actor_id="alice",
        client_command_id="command-authoritative",
        status="ACCEPTED",
        runtime_session_id="pi:diag-runtime-api:1",
        accepted_mode="pi",
    )


def _path(
    diagnosis_id: str = "diag-runtime-api",
    turn_id: str = "turn-authoritative",
) -> str:
    return f"/internal/runtime/v1/diagnoses/{diagnosis_id}/turns/{turn_id}/events"


def _headers(token: str = TOKEN) -> dict[str, str]:
    return {"X-Internal-Token": token}


def _event(seq: int, *, event_id: str | None = None, text: str | None = None) -> dict:
    return {
        "event_id": event_id or f"evt-runtime-api-{seq}",
        "event_seq": seq,
        "event_type": "message_end",
        "payload": {"text": text or f"message-{seq}"},
    }


def test_event_callback_requires_configured_internal_token(client, monkeypatch):
    missing = client.post(_path(), json={"runtime_generation": 1, "events": [_event(1)]})
    assert missing.status_code == 401

    wrong = client.post(
        _path(),
        headers=_headers("wrong-token"),
        json={"runtime_generation": 1, "events": [_event(1)]},
    )
    assert wrong.status_code == 401

    monkeypatch.delenv("MINI_DROP_PI_INTERNAL_TOKEN")
    unavailable = client.post(
        _path(),
        headers=_headers(),
        json={"runtime_generation": 1, "events": [_event(1)]},
    )
    assert unavailable.status_code == 503


def test_event_callback_uses_existing_diagnosis_and_turn_authority(client):
    unknown_diagnosis = client.post(
        _path("diag-missing"),
        headers=_headers(),
        json={"runtime_generation": 1, "events": [_event(1)]},
    )
    assert unknown_diagnosis.status_code == 404

    unknown_turn = client.post(
        _path(turn_id="turn-missing"),
        headers=_headers(),
        json={"runtime_generation": 1, "events": [_event(1)]},
    )
    assert unknown_turn.status_code == 404
    assert SqlRepository().get_agent_runtime_binding(
        diagnosis_id="diag-missing"
    ) is None


def test_event_batch_is_atomic_and_exact_replay_is_idempotent(client):
    conflicting = client.post(
        _path(),
        headers=_headers(),
        json={
            "runtime_generation": 1,
            "events": [_event(1), _event(1, event_id="evt-runtime-api-conflict")],
        },
    )
    assert conflicting.status_code == 409
    assert SqlRepository().list_agent_runtime_events(
        diagnosis_id="diag-runtime-api", turn_id="turn-authoritative"
    ) == []

    body = {"runtime_generation": 1, "events": [_event(1), _event(2)]}
    accepted = client.post(_path(), headers=_headers(), json=body)
    replayed = client.post(_path(), headers=_headers(), json=body)

    assert accepted.status_code == 200
    assert replayed.status_code == 200
    assert [item.get("duplicate", False) for item in accepted.json()["data"]["events"]] == [False, False]
    assert [item["duplicate"] for item in replayed.json()["data"]["events"]] == [True, True]
    assert len(SqlRepository().list_agent_runtime_events(
        diagnosis_id="diag-runtime-api", turn_id="turn-authoritative"
    )) == 2


def test_event_callback_rejects_stale_generation_and_sealed_turn(client):
    stale = client.post(
        _path(),
        headers=_headers(),
        json={"runtime_generation": 2, "events": [_event(1)]},
    )
    assert stale.status_code == 409

    SqlRepository().seal_agent_runtime_turn(
        diagnosis_id="diag-runtime-api",
        turn_id="turn-authoritative",
        terminal_status="COMPLETED",
        final_message={"text": "done"},
    )
    sealed = client.post(
        _path(),
        headers=_headers(),
        json={"runtime_generation": 1, "events": [_event(2)]},
    )
    assert sealed.status_code == 409


def test_event_callback_strictly_validates_batch(client):
    empty = client.post(
        _path(),
        headers=_headers(),
        json={"runtime_generation": 1, "events": []},
    )
    unknown = client.post(
        _path(),
        headers=_headers(),
        json={
            "runtime_generation": 1,
            "events": [{**_event(1), "unexpected": True}],
        },
    )

    assert empty.status_code == 422
    assert unknown.status_code == 422
