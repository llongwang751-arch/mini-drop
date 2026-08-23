from __future__ import annotations

import secrets

from fastapi import APIRouter, Header, HTTPException

from server.app.agent_runtime.config import pi_internal_token
from server.app.agent_runtime.port import RuntimeEventBatch
from server.app.diagnosis.store import DiagnosisStore
from server.app.sql_repository import SqlRepository


router = APIRouter(prefix="/internal/runtime/v1", tags=["internal-runtime"])


def _authorize_internal_runtime(supplied: str | None) -> None:
    configured = pi_internal_token()
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="internal runtime token is not configured",
        )
    if not supplied or not secrets.compare_digest(supplied, configured):
        raise HTTPException(status_code=401, detail="invalid internal runtime token")


def _runtime_conflict(exc: ValueError) -> HTTPException:
    message = str(exc)
    if message in {
        "diagnosis session does not exist",
        "runtime turn does not exist",
    }:
        return HTTPException(status_code=404, detail=message)
    return HTTPException(status_code=409, detail=message)


@router.post("/diagnoses/{diagnosis_id}/turns/{turn_id}/events")
def record_runtime_events(
    diagnosis_id: str,
    turn_id: str,
    body: RuntimeEventBatch,
    x_internal_token: str | None = Header(default=None),
) -> dict:
    _authorize_internal_runtime(x_internal_token)
    if DiagnosisStore().get_session(diagnosis_id) is None:
        raise HTTPException(status_code=404, detail="diagnosis session does not exist")
    try:
        events = SqlRepository().record_agent_runtime_events(
            diagnosis_id=diagnosis_id,
            turn_id=turn_id,
            runtime_generation=body.runtime_generation,
            events=[event.model_dump(mode="json") for event in body.events],
        )
    except ValueError as exc:
        raise _runtime_conflict(exc) from exc
    return {"ok": True, "data": {"events": events}}
