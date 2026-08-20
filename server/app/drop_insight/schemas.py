from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiagnosticTarget(StrictModel):
    service: str | None = Field(default=None, min_length=1, max_length=128)
    environment: str | None = Field(default=None, min_length=1, max_length=64)
    agent_id: str | None = None
    host_id: str | None = None
    container_id: str | None = None
    pid: int | None = Field(default=None, ge=1)
    instance_id: str | None = None


class DiagnosticTimeRange(StrictModel):
    start: datetime
    end: datetime
    timezone: str = "Asia/Shanghai"

    @model_validator(mode="after")
    def validate_range(self):
        if self.end <= self.start:
            raise ValueError("end must be later than start")
        return self


class DiagnosisBudget(StrictModel):
    max_duration_seconds: int = Field(default=300, ge=10, le=1800)
    max_tool_calls: int = Field(default=12, ge=1, le=50)
    max_concurrent_tasks: int = Field(default=3, ge=1, le=10)
    max_hosts: int = Field(default=5, ge=1, le=20)
    max_artifact_bytes: int = Field(default=524_288_000, ge=1)
    max_risk_level: Literal["R0", "R1", "R2"] = "R2"


class CreateDiagnosisRequestV2(StrictModel):
    query: str = Field(min_length=3, max_length=2000)
    target: DiagnosticTarget = Field(default_factory=DiagnosticTarget)
    time_range: DiagnosticTimeRange | None = None
    mode: Literal["ASSISTED", "OBSERVE_ONLY", "REPRODUCTION", "REPLAY"] = "ASSISTED"
    budget: DiagnosisBudget = Field(default_factory=DiagnosisBudget)


class CreateHypothesisRequest(StrictModel):
    expected_version: int | None = Field(default=None, ge=1)
    statement: str = Field(min_length=5, max_length=2000)
    expected_observations: list[str] = Field(min_length=1, max_length=20)
    falsification_criteria: list[str] = Field(min_length=1, max_length=20)


class AddEvidenceRequest(StrictModel):
    """Untrusted, manually supplied context.

    Persisted task/attempt/artifact provenance is deliberately not accepted
    here.  Production evidence must enter through ``import-task`` so quality,
    scope and integrity are computed by the server.
    """

    expected_version: int | None = Field(default=None, ge=1)
    evidence_id: str = Field(min_length=3, max_length=128)
    hypothesis_id: str | None = None
    evidence_type: str = Field(min_length=2, max_length=128)
    observation: dict[str, Any]
    source_label: str = Field(default="manual", min_length=2, max_length=128)
    limitations: list[str] = Field(default_factory=list, max_length=20)


class GenerateReportRequest(StrictModel):
    expected_version: int | None = Field(default=None, ge=1)
    hypothesis_id: str


class PreviewToolCallRequest(StrictModel):
    tool_name: str = Field(min_length=2, max_length=128)
    arguments: dict[str, Any]
    used_tool_calls: int = Field(default=0, ge=0)


class ImportTaskEvidenceRequest(StrictModel):
    expected_version: int | None = Field(default=None, ge=1)
    task_id: str = Field(min_length=3, max_length=128)
    hypothesis_id: str


class CreateToolCallRequest(StrictModel):
    expected_version: int | None = Field(default=None, ge=1)
    hypothesis_id: str | None = None
    tool_name: str = Field(min_length=2, max_length=128)
    arguments: dict[str, Any]


class DecideToolCallRequest(StrictModel):
    approved: bool
    reason: str = Field(min_length=2, max_length=1000)


class UpdateToolCallArgumentsRequest(StrictModel):
    """修改待审批工具调用的参数；修改后重新做策略与预算校验。"""

    arguments: dict[str, Any]


class RunPlannerRequest(StrictModel):
    pass


class VerifyFixRequest(StrictModel):
    """Apply-fix verification: compare a before and after profile task."""

    before_task_id: str = Field(min_length=3, max_length=128)
    after_task_id: str = Field(min_length=3, max_length=128)
    fix_summary: str | None = Field(default=None, max_length=2000)


class ClarifyDiagnosisRequest(StrictModel):
    """Fill in missing scope for a NEEDS_CLARIFICATION session."""

    expected_version: int | None = Field(default=None, ge=1)
    target: DiagnosticTarget | None = None
    time_range: DiagnosticTimeRange | None = None


class SubmitDiagnosisFeedbackRequest(StrictModel):
    report_id: str | None = Field(default=None, max_length=128)
    hypothesis_id: str | None = Field(default=None, max_length=128)
    feedback_label: Literal["correct", "partial", "wrong"]
    corrected_cause: str | None = Field(default=None, max_length=2000)
    feedback_note: str | None = Field(default=None, max_length=4000)
    request_replan: bool = True

    @model_validator(mode="after")
    def validate_correction(self):
        if self.feedback_label in {"partial", "wrong"} and not (
            (self.corrected_cause or "").strip() or (self.feedback_note or "").strip()
        ):
            raise ValueError("partial/wrong feedback requires a corrected cause or note")
        return self
