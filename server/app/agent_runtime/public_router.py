from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from server.app.agent_runtime.turn_service import (
    RuntimeTurnConflict,
    RuntimeTurnNotFound,
    RuntimeTurnService,
    RuntimeTurnUnavailable,
)
from server.app.schemas import APIResponse


router = APIRouter(
    prefix="/api/v1/diagnoses",
    tags=["agent-runtime"],
)


class SubmitRuntimeTurnRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    message: str = Field(min_length=1, max_length=8000)
    client_command_id: str = Field(min_length=1, max_length=128)
    requested_mode: Literal["COLLABORATE"] | None = None


@router.post("/{diagnosis_id}/turns")
def submit_runtime_turn(
    diagnosis_id: str,
    body: SubmitRuntimeTurnRequest,
    request: Request,
    response: Response,
) -> APIResponse:
    try:
        result = RuntimeTurnService().submit(
            diagnosis_id=diagnosis_id,
            actor_id=request.state.authenticated_principal,
            message=body.message,
            client_command_id=body.client_command_id,
            requested_mode=body.requested_mode,
        )
    except RuntimeTurnNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeTurnConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeTurnUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if result["status"] in {"SUBMITTING", "ACCEPTANCE_UNKNOWN"}:
        response.status_code = status.HTTP_202_ACCEPTED
    return APIResponse(data=result)
