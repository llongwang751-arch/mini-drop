"""Strict contracts for evaluator-only diagnosis scoring."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvaluationOracle(StrictModel):
    """Private expected result loaded only by the evaluator repository."""

    case_id: str = Field(min_length=1, max_length=128)
    expected_instance_id: str | None = Field(default=None, max_length=128)
    expected_location_type: Literal[
        "self", "same_host", "downstream", "shared_resource", "unknown"
    ] | None = None
    expected_domain_type: Literal[
        "cpu", "io", "memory", "network", "database", "runtime", "unknown"
    ] | None = None
    expected_classification: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def require_expected_value(self) -> "EvaluationOracle":
        if not any((
            self.expected_instance_id,
            self.expected_location_type,
            self.expected_domain_type,
            self.expected_classification,
        )):
            raise ValueError("evaluation oracle requires an expected result")
        return self


class EvaluationRequest(StrictModel):
    artifact_id: str = Field(min_length=1, max_length=128)
    expected_artifact_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    evaluator_version: str = Field(min_length=1, max_length=64)


class FrozenDiagnosisArtifact(StrictModel):
    schema_version: Literal["diagnosis-artifact-v1"]
    diagnosis_id: str = Field(min_length=1, max_length=128)
    case_id: str | None = Field(default=None, max_length=128)
    terminal_status: Literal[
        "COMPLETED", "INSUFFICIENT_EVIDENCE", "PARTIAL_COMPLETED", "BUDGET_EXHAUSTED",
        "TOPOLOGY_UNAVAILABLE", "USER_CANCELED", "FAILED",
    ]
    normalized_intent: dict[str, Any] = Field(default_factory=dict)
    target_scope: dict[str, Any] = Field(default_factory=dict)
    requested_time_range: dict[str, Any] = Field(default_factory=dict)
    effective_time_range: dict[str, Any] = Field(default_factory=dict)
    topology: dict[str, Any] | None = None
    conclusion: dict[str, Any]
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    evidence_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    probes: list[dict[str, Any]] = Field(default_factory=list)
    budget: dict[str, Any] = Field(default_factory=dict)
    model_version: str = Field(min_length=1, max_length=128)
    planner_version: str = Field(min_length=1, max_length=128)
