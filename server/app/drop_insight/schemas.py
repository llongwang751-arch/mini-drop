from __future__ import annotations

import math
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
    min_diagnosis_rounds: int = Field(default=1, ge=1, le=4)
    max_diagnosis_rounds: int = Field(default=6, ge=1, le=12)
    max_concurrent_tasks: int = Field(default=3, ge=1, le=10)
    max_hosts: int = Field(default=5, ge=1, le=20)
    max_artifact_bytes: int = Field(default=524_288_000, ge=1)
    max_risk_level: Literal["R0", "R1", "R2"] = "R2"
    lats_top_k: int = Field(default=3, ge=1, le=8)
    # ``None`` is deliberate: LATSConfig.from_budget then inherits the
    # caller's max_diagnosis_rounds instead of silently replacing it.
    max_lats_iterations: int | None = Field(default=None, ge=1, le=12)
    lats_exploration_constant: float = Field(
        default_factory=lambda: math.sqrt(2.0), ge=0.0, le=8.0
    )
    lats_selection_policy: Literal["UCT", "PUCT"] = "UCT"
    lats_value_lambda: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_diagnosis_round_range(self):
        if self.min_diagnosis_rounds > self.max_diagnosis_rounds:
            raise ValueError(
                "min_diagnosis_rounds must not exceed max_diagnosis_rounds"
            )
        return self


class StartFrozenReplayRequest(StrictModel):
    """Idempotency-only input for one allow-listed frozen showcase run."""

    client_run_id: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class CreateDiagnosisRequestV2(StrictModel):
    query: str = Field(min_length=3, max_length=2000)
    target: DiagnosticTarget = Field(default_factory=DiagnosticTarget)
    time_range: DiagnosticTimeRange | None = None
    auto_scope: bool = False
    mode: Literal[
        "AUTONOMOUS", "ASSISTED", "OBSERVE_ONLY", "REPRODUCTION", "REPLAY"
    ] = "ASSISTED"
    # Per-session switch used by shadow/live A/B. It is persisted with the
    # diagnosis so a result can never be relabelled after the fact.
    skill_policy: Literal["AUTO", "DISABLED"] = "AUTO"
    budget: DiagnosisBudget = Field(default_factory=DiagnosisBudget)


class CreateDiagnosticExperimentRequest(StrictModel):
    name: str = Field(min_length=3, max_length=256)
    treatment_ratio: float = Field(default=0.5, ge=0.05, le=0.95)
    minimum_labeled_per_arm: int = Field(default=30, ge=5, le=100_000)
    minimum_effect_percentage_points: float = Field(default=5.0, ge=0, le=100)
    alpha: float = Field(default=0.05, gt=0, lt=1)
    guardrails: dict[str, Any] = Field(
        default_factory=lambda: {
            "maximum_safety_violations": 0,
            "maximum_verified_rate_regression_percentage_points": 5.0,
        }
    )


class AssignDiagnosticExperimentRequest(StrictModel):
    unit_key: str = Field(min_length=3, max_length=512)
    stratum: str = Field(default="default", min_length=1, max_length=128)
    diagnosis: CreateDiagnosisRequestV2


class RecordDiagnosticExperimentOutcomeRequest(StrictModel):
    root_cause_correct: bool
    outcome_source: Literal["HUMAN", "CONTROLLED_ORACLE"] = "HUMAN"
    safety_violation_count: int = Field(default=0, ge=0, le=1000)
    notes: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApproveDiagnosticExperimentRequest(StrictModel):
    reason: str = Field(min_length=3, max_length=2000)


class PutOperatorPreferenceRequest(StrictModel):
    project_scope: str = Field(default="*", min_length=1, max_length=128)
    memory_key: Literal[
        "explanation_depth",
        "preferred_low_risk_first",
        "response_language",
        "timezone",
    ]
    value: Any


class DeleteOperatorPreferenceRequest(StrictModel):
    project_scope: str = Field(default="*", min_length=1, max_length=128)
    memory_key: Literal[
        "explanation_depth",
        "preferred_low_risk_first",
        "response_language",
        "timezone",
    ]


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


class QuarantineDiagnosticSkillRequest(StrictModel):
    reason: str = Field(min_length=3, max_length=1000)


class RecordSkillCampaignValidationRequest(StrictModel):
    """Evidence summary produced by a cross-environment Skill campaign.

    Pass/fail is derived by the service.  Callers cannot submit a trusted
    ``passed`` flag and thereby bypass the publication boundary.
    """

    campaign_id: str = Field(min_length=3, max_length=128)
    benchmark_kind: Literal["CROSS_ENVIRONMENT_SKILL_CAMPAIGN"]
    report_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    environment_fingerprints: list[str] = Field(min_length=2, max_length=64)
    collector_kinds: list[str] = Field(min_length=1, max_length=32)
    positive_cases: int = Field(ge=1)
    misleading_negative_cases: int = Field(ge=1)
    environment_drift_cases: int = Field(ge=1)
    paired_skill_no_skill_cases: int = Field(ge=1)
    passed_cases: int = Field(ge=0)
    total_cases: int = Field(ge=4)
    false_activation_rate: float = Field(ge=0, le=1)
    policy_violation_count: int = Field(default=0, ge=0)
    report_ref: str | None = Field(default=None, min_length=3, max_length=512)

    @model_validator(mode="after")
    def validate_campaign_composition(self):
        environments = {
            value.strip() for value in self.environment_fingerprints if value.strip()
        }
        if len(environments) < 2:
            raise ValueError("campaign must cover at least two distinct environments")
        if self.passed_cases > self.total_cases:
            raise ValueError("passed_cases cannot exceed total_cases")
        classified_cases = (
            self.positive_cases
            + self.misleading_negative_cases
            + self.environment_drift_cases
        )
        if classified_cases > self.total_cases:
            raise ValueError("classified campaign cases cannot exceed total_cases")
        return self


class VerifyFixRequest(StrictModel):
    """Apply-fix verification: compare a before and after profile task."""

    before_task_id: str = Field(min_length=3, max_length=128)
    after_task_id: str = Field(min_length=3, max_length=128)
    fix_summary: str | None = Field(default=None, max_length=2000)


class ClarificationTarget(StrictModel):
    """Client-supplied scope hints plus opaque server discovery selection."""

    service: str | None = Field(default=None, min_length=1, max_length=128)
    environment: str | None = Field(default=None, min_length=1, max_length=64)
    discovery_id: str | None = Field(default=None, min_length=16, max_length=128)
    binding_id: str | None = Field(default=None, min_length=16, max_length=128)


class ClarifyDiagnosisRequest(StrictModel):
    """Fill in missing scope for a NEEDS_CLARIFICATION session."""

    expected_version: int | None = Field(default=None, ge=1)
    target: ClarificationTarget | None = None
    time_range: DiagnosticTimeRange | None = None


class InterveneDiagnosisRequest(StrictModel):
    """A durable user turn that can redirect an in-flight diagnosis.

    The browser supplies intent and context, never a collector name, PID, or
    shell command.  The diagnosis service converts this turn into a new
    falsifiable hypothesis and sends any probe through the normal policy and
    budget gates.
    """

    expected_version: int | None = Field(default=None, ge=1)
    action: Literal[
        "ADD_CONTEXT",
        "CHALLENGE_HYPOTHESIS",
        "CHANGE_DIRECTION",
        "CONTINUE_INVESTIGATION",
    ] = "ADD_CONTEXT"
    message: str = Field(min_length=2, max_length=4000)
    hypothesis_id: str | None = Field(default=None, min_length=3, max_length=128)
    idempotency_key: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


class StartFaultScenarioRequest(StrictModel):
    """Bounded duration for one allow-listed demo-only fault scenario."""

    duration_seconds: int = Field(default=60, ge=15, le=300)


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
