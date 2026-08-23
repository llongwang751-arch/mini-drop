from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import Field

from server.app.agent_runtime.port import StrictModel
from server.app.agent_runtime.read_tools import (
    COMPARISON_DIMENSIONS,
    MAX_TOOL_PROJECTION_BYTES,
    PROJECTION_KINDS,
    DiagnosisNotFound,
    DiagnosisReadTools,
    EvidenceNotFound,
    InvalidReadRequest,
    ProjectionTooLarge,
)
from server.app.agent_runtime.router import _authorize_internal_runtime


router = APIRouter(prefix="/internal/agent/tools", tags=["internal-agent-tools"])

EvidenceId = Annotated[str, Field(min_length=1, max_length=128)]


class ToolRequest(StrictModel):
    diagnosis_id: str = Field(min_length=1, max_length=128)


class DiagnosisSnapshotRequest(ToolRequest):
    tool: Literal["get_diagnosis_snapshot"]


class EvidenceFilters(StrictModel):
    source_type: str | None = Field(default=None, min_length=1, max_length=64)
    source_system: str | None = Field(default=None, min_length=1, max_length=128)
    evidence_role: str | None = Field(default=None, min_length=1, max_length=32)


class ListEvidenceRequest(ToolRequest):
    tool: Literal["list_diagnosis_evidence"]
    filters: EvidenceFilters = Field(default_factory=EvidenceFilters)
    cursor: str | None = Field(default=None, min_length=1, max_length=128)


class EvidenceProjectionRequest(ToolRequest):
    tool: Literal["get_evidence_projection"]
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=32)
    projection_kinds: list[Literal[
        "identity", "signal", "context", "quality", "provenance", "claims"
    ]] | None = Field(default=None, max_length=len(PROJECTION_KINDS))
    max_bytes: int = Field(default=MAX_TOOL_PROJECTION_BYTES, ge=1, le=MAX_TOOL_PROJECTION_BYTES)


class CompareEvidenceRequest(ToolRequest):
    tool: Literal["compare_evidence"]
    evidence_ids: list[EvidenceId] = Field(min_length=2, max_length=32)
    dimensions: list[Literal[
        "signal", "target", "window", "quality", "source"
    ]] | None = Field(default=None, max_length=len(COMPARISON_DIMENSIONS))


class EvidenceGapsRequest(ToolRequest):
    tool: Literal["get_evidence_gaps"]


class EvaluateHypothesesRequest(ToolRequest):
    tool: Literal["evaluate_hypotheses"]


def _execute(supplied_token: str | None, operation) -> dict:
    _authorize_internal_runtime(supplied_token)
    try:
        data = operation()
    except DiagnosisNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EvidenceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidReadRequest as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProjectionTooLarge as exc:
        raise HTTPException(
            status_code=413,
            detail={
                "message": str(exc),
                "projection_bytes": exc.actual_bytes,
                "max_bytes": exc.max_bytes,
            },
        ) from exc
    return {"ok": True, "data": data}


@router.post("/diagnosis-snapshot")
def diagnosis_snapshot(
    body: DiagnosisSnapshotRequest,
    x_internal_token: str | None = Header(default=None),
) -> dict:
    return _execute(
        x_internal_token,
        lambda: DiagnosisReadTools().diagnosis_snapshot(body.diagnosis_id),
    )


@router.post("/list-diagnosis-evidence")
def list_diagnosis_evidence(
    body: ListEvidenceRequest,
    x_internal_token: str | None = Header(default=None),
) -> dict:
    return _execute(
        x_internal_token,
        lambda: DiagnosisReadTools().list_evidence(
            body.diagnosis_id,
            filters=body.filters.model_dump(mode="json"),
            cursor=body.cursor,
        ),
    )


@router.post("/get-evidence-projection")
def get_evidence_projection(
    body: EvidenceProjectionRequest,
    x_internal_token: str | None = Header(default=None),
) -> dict:
    return _execute(
        x_internal_token,
        lambda: DiagnosisReadTools().evidence_projection(
            body.diagnosis_id,
            evidence_ids=body.evidence_ids,
            projection_kinds=body.projection_kinds,
            max_bytes=body.max_bytes,
        ),
    )


@router.post("/compare-evidence")
def compare_evidence(
    body: CompareEvidenceRequest,
    x_internal_token: str | None = Header(default=None),
) -> dict:
    return _execute(
        x_internal_token,
        lambda: DiagnosisReadTools().compare_evidence(
            body.diagnosis_id,
            evidence_ids=body.evidence_ids,
            dimensions=body.dimensions,
        ),
    )


@router.post("/get-evidence-gaps")
def get_evidence_gaps(
    body: EvidenceGapsRequest,
    x_internal_token: str | None = Header(default=None),
) -> dict:
    return _execute(
        x_internal_token,
        lambda: DiagnosisReadTools().evidence_gaps(body.diagnosis_id),
    )


@router.post("/evaluate-hypotheses")
def evaluate_hypotheses(
    body: EvaluateHypothesesRequest,
    x_internal_token: str | None = Header(default=None),
) -> dict:
    return _execute(
        x_internal_token,
        lambda: DiagnosisReadTools().evaluate_hypotheses(body.diagnosis_id),
    )
