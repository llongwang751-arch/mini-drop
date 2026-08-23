from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from server.app.evaluation.real_world_admission import canonical_hash


CASE_ID = "RW-OTELPY-4224"
REPOSITORY_URL = "https://github.com/open-telemetry/opentelemetry-python"
BASE_SHA = "679297f5ebd37510b6c9e086fc27837935d57e81"
FIX_SHA = "84c6b0a419226328b6884b43a61cfd7a8fa3b3bb"
PHASES = ("baseline", "incident", "verification")
EXPECTED_REVISIONS = {
    "baseline": BASE_SHA,
    "incident": BASE_SHA,
    "verification": FIX_SHA,
}


class FrozenStrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class ReplayEnvironmentV1(FrozenStrictModel):
    os: str = Field(min_length=1, max_length=128)
    architecture: str = Field(min_length=1, max_length=64)
    runtime: str = Field(min_length=1, max_length=128)
    container_image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class RetainedObjectsV1(FrozenStrictModel):
    reader: int = Field(ge=0)
    exporter: int = Field(ge=0)
    provider: int = Field(ge=0)


class ReplayPhaseObservationV1(FrozenStrictModel):
    phase: Literal["baseline", "incident", "verification"]
    revision_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    iterations: int = Field(ge=0)
    retained: RetainedObjectsV1
    all_collected: bool
    terminal_status: Literal["COMPLETED", "FAILED"]
    comparator_exit_code: int
    started_at: datetime
    finished_at: datetime

    @field_validator("started_at", "finished_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("replay timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def require_positive_duration(self) -> "ReplayPhaseObservationV1":
        if self.finished_at <= self.started_at:
            raise ValueError("replay phase must finish after it starts")
        return self


class ReplayRepetitionObservationV1(FrozenStrictModel):
    repetition_id: str = Field(min_length=1, max_length=128)
    phases: list[ReplayPhaseObservationV1] = Field(min_length=1, max_length=3)


class FrozenDiagnosisReceiptV1(FrozenStrictModel):
    schema_version: Literal["local-diagnosis-freeze-receipt-v1"]
    diagnosis_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    case_id: Literal[CASE_ID]
    artifact_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    frozen_at: datetime

    @field_validator("frozen_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("diagnosis freeze timestamp must include a timezone")
        return value


class LocalReplayObservationV1(FrozenStrictModel):
    schema_version: Literal["local-replay-observation-v1"]
    run_id: str = Field(min_length=1, max_length=128)
    case_id: Literal[CASE_ID]
    repository_url: str = Field(min_length=1, max_length=512)
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    fix_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_integrity_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    dependency_lock_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    harness_integrity_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    environment: ReplayEnvironmentV1
    repetitions: list[ReplayRepetitionObservationV1] = Field(min_length=1, max_length=100)
    diagnosis_receipt: FrozenDiagnosisReceiptV1
    oracle_accessed_at: datetime | None = None

    @field_validator("oracle_accessed_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Oracle access timestamp must include a timezone")
        return value


class LocalReplayContractGatesV1(FrozenStrictModel):
    case_identity: bool
    source_identity: bool
    provenance_complete: bool
    environment_bound: bool
    repetitions_sufficient: bool
    phase_order: bool
    phase_revision_identity: bool
    baseline_stable: bool
    incident_reproduced: bool
    verification_recovered: bool
    comparator_passed: bool
    diagnosis_frozen: bool
    oracle_after_freeze: bool


class LocalReplayContractReportV1(FrozenStrictModel):
    schema_version: Literal["local-replay-contract-report-v1"] = (
        "local-replay-contract-report-v1"
    )
    case_id: Literal[CASE_ID]
    run_id: str = Field(min_length=1, max_length=128)
    reporting_tier: Literal["LOCAL_SYNTHETIC_CONTRACT"] = "LOCAL_SYNTHETIC_CONTRACT"
    scoring_status: Literal["UNSCORED"] = "UNSCORED"
    formal_admission_eligible: Literal[False] = False
    observation_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gates: LocalReplayContractGatesV1
    failure_codes: list[str]


_FAILURE_CODES = {
    "case_identity": "CASE_IDENTITY_INVALID",
    "source_identity": "SOURCE_IDENTITY_INVALID",
    "provenance_complete": "PROVENANCE_INCOMPLETE",
    "environment_bound": "ENVIRONMENT_UNBOUND",
    "repetitions_sufficient": "REPETITIONS_INSUFFICIENT",
    "phase_order": "PHASE_ORDER_INVALID",
    "phase_revision_identity": "PHASE_REVISION_IDENTITY_INVALID",
    "baseline_stable": "BASELINE_UNSTABLE",
    "incident_reproduced": "INCIDENT_NOT_REPRODUCED",
    "verification_recovered": "VERIFICATION_NOT_RECOVERED",
    "comparator_passed": "COMPARATOR_FAILED",
    "diagnosis_frozen": "DIAGNOSIS_FREEZE_INVALID",
    "oracle_after_freeze": "ORACLE_ACCESSED_BEFORE_FREEZE",
}


def _zero_retained(value: RetainedObjectsV1) -> bool:
    return value.reader == value.exporter == value.provider == 0


def _ordered_phases(
    repetition: ReplayRepetitionObservationV1,
) -> tuple[bool, list[ReplayPhaseObservationV1]]:
    phases = repetition.phases
    ordered = [phase.phase for phase in phases] == list(PHASES)
    chronological = all(
        left.finished_at <= right.started_at
        for left, right in zip(phases, phases[1:])
    )
    return ordered and chronological, phases


def evaluate_local_replay_contract(
    value: LocalReplayObservationV1 | dict[str, Any],
) -> LocalReplayContractReportV1:
    observation = (
        value
        if isinstance(value, LocalReplayObservationV1)
        else LocalReplayObservationV1.model_validate(value)
    )
    repetitions = observation.repetitions
    repetition_ids = [item.repetition_id for item in repetitions]
    phase_checks = [_ordered_phases(item) for item in repetitions]
    phase_order = all(valid for valid, _ in phase_checks)
    complete_phase_sets = phase_order and all(len(phases) == 3 for _, phases in phase_checks)

    phase_revision_identity = complete_phase_sets and all(
        phase.revision_sha == EXPECTED_REVISIONS[phase.phase]
        for _, phases in phase_checks
        for phase in phases
    )
    baseline_stable = complete_phase_sets and all(
        phases[0].terminal_status == "COMPLETED"
        and _zero_retained(phases[0].retained)
        and phases[0].all_collected
        for _, phases in phase_checks
    )
    incident_reproduced = complete_phase_sets and all(
        phases[1].terminal_status == "COMPLETED"
        and phases[1].iterations > 0
        and phases[1].retained.reader == phases[1].iterations
        and phases[1].retained.exporter == phases[1].iterations
        and phases[1].retained.provider == 0
        and not phases[1].all_collected
        for _, phases in phase_checks
    )
    verification_recovered = complete_phase_sets and all(
        phases[2].terminal_status == "COMPLETED"
        and phases[2].iterations == phases[1].iterations
        and phases[2].iterations > 0
        and _zero_retained(phases[2].retained)
        and phases[2].all_collected
        for _, phases in phase_checks
    )
    comparator_passed = complete_phase_sets and all(
        phase.terminal_status == "COMPLETED" and phase.comparator_exit_code == 0
        for _, phases in phase_checks
        for phase in phases
    )

    observation_finished_at = max(
        phase.finished_at
        for repetition in repetitions
        for phase in repetition.phases
    )
    receipt = observation.diagnosis_receipt
    diagnosis_frozen = (
        receipt.case_id == observation.case_id
        and receipt.run_id == observation.run_id
        and receipt.frozen_at > observation_finished_at
    )
    oracle_after_freeze = (
        observation.oracle_accessed_at is None
        or observation.oracle_accessed_at > receipt.frozen_at
    )

    gates = LocalReplayContractGatesV1(
        case_identity=observation.case_id == CASE_ID,
        source_identity=(
            observation.repository_url == REPOSITORY_URL
            and observation.base_sha == BASE_SHA
            and observation.fix_sha == FIX_SHA
            and observation.base_sha != observation.fix_sha
        ),
        provenance_complete=all((
            bool(observation.source_integrity_hash),
            bool(observation.dependency_lock_hash),
            bool(observation.harness_integrity_hash),
        )),
        environment_bound=all((
            bool(observation.environment.os),
            bool(observation.environment.architecture),
            bool(observation.environment.runtime),
            bool(observation.environment.container_image_digest),
        )),
        repetitions_sufficient=(
            len(repetitions) >= 3
            and len(repetition_ids) == len(set(repetition_ids))
        ),
        phase_order=phase_order,
        phase_revision_identity=phase_revision_identity,
        baseline_stable=baseline_stable,
        incident_reproduced=incident_reproduced,
        verification_recovered=verification_recovered,
        comparator_passed=comparator_passed,
        diagnosis_frozen=diagnosis_frozen,
        oracle_after_freeze=oracle_after_freeze,
    )
    gate_values = gates.model_dump()
    failure_codes = sorted(
        _FAILURE_CODES[name]
        for name, passed in gate_values.items()
        if not passed
    )
    return LocalReplayContractReportV1(
        case_id=observation.case_id,
        run_id=observation.run_id,
        observation_hash=canonical_hash(observation.model_dump(mode="json")),
        gates=gates,
        failure_codes=failure_codes,
    )
