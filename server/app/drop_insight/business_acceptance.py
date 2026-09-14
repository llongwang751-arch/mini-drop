"""Business repair gates. Measured request outcomes, never a diagnosis oracle.

The caller supplies server-recorded windows. This function does not certify
uploaded numbers as trusted evidence or change a diagnostic Report's verdict.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Workload(Contract):
    dataset_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_set_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    arrival_rate: float = Field(gt=0, le=10000)
    request_count: int = Field(ge=10, le=100000)
    concurrency_limit: int = Field(ge=1, le=1000)
    seed: int
    warmup_requests: int = Field(ge=0)
    environment: str = Field(min_length=1, max_length=128)
    service: str = Field(min_length=1, max_length=128)
    resources_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RequestOutcome(Contract):
    request_id: str = Field(min_length=1, max_length=128)
    latency_ms: float = Field(ge=0)
    success: bool
    quality_passed: bool
    degraded: bool = False
    stage_ms: dict[str, float] = Field(default_factory=dict)
    trace_id: str = Field(pattern=r"^[a-f0-9]{32}$")

    @model_validator(mode="after")
    def valid_stages(self):
        if any(not math.isfinite(v) or v < 0 for v in self.stage_ms.values()):
            raise ValueError("stage duration must be finite and nonnegative")
        if not self.success and self.quality_passed:
            raise ValueError("failed response cannot pass quality")
        return self


class MeasurementWindow(Contract):
    window_id: str = Field(min_length=1, max_length=128)
    workload: Workload
    revision: str = Field(min_length=1, max_length=128)
    config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    generator: Literal["EXTRACTIVE_LOCAL", "REMOTE_MODEL", "CONTROLLED_DEPENDENCY"]
    elapsed_seconds: float = Field(gt=0)
    max_dispatch_lag_ms: float = Field(ge=0)
    requests: list[RequestOutcome] = Field(min_length=10, max_length=100000)

    @model_validator(mode="after")
    def complete_requests(self):
        ids = [r.request_id for r in self.requests]
        if len(ids) != self.workload.request_count or len(set(ids)) != len(ids):
            raise ValueError("every offered request must appear exactly once, including errors")
        return self


class AcceptancePolicy(Contract):
    minimum_requests: int = Field(default=30, ge=10)
    recovery_p95_ratio: float = Field(default=1.3, ge=1, le=2)
    minimum_success_rate: float = Field(default=.98, ge=0, le=1)
    minimum_quality_rate: float = Field(default=.98, ge=0, le=1)
    minimum_fault_ratio: float = Field(default=1.5, gt=1)
    max_dispatch_lag_ms: float = Field(default=150, gt=0)
    latency_floor_ms: float = Field(default=20, ge=0)


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def summarize(window: MeasurementWindow) -> dict:
    rows = window.requests
    def percentile(values, fraction):
        return sorted(values)[max(0, math.ceil(len(values)*fraction)-1)]
    values = [r.latency_ms for r in rows]
    return {"request_count": len(rows), "success_rate": sum(r.success for r in rows)/len(rows),
            "quality_rate": sum(r.quality_passed for r in rows)/len(rows),
            "degraded_count": sum(r.degraded for r in rows),
            "p50_ms": percentile(values,.5), "p95_ms": percentile(values,.95),
            "p99_ms": percentile(values,.99) if len(rows)>=1000 else None,
            "completed_rps": len(rows)/window.elapsed_seconds,
            "stage_p95_ms": {s: percentile([r.stage_ms.get(s,0) for r in rows],.95)
                             for s in sorted({s for r in rows for s in r.stage_ms})}}


def compare_business_windows(baseline: MeasurementWindow, fault: MeasurementWindow,
                             after: MeasurementWindow, policy: AcceptancePolicy,
                             change_summary: str) -> dict:
    windows = (baseline, fault, after)
    summaries = dict(zip(("baseline","fault","after"),map(summarize, windows)))
    reasons=[]
    if not change_summary.strip(): reasons.append("MISSING_CHANGE_DESCRIPTION")
    if len({canonical_hash(w.workload.model_dump()) for w in windows})!=1:
        reasons.append("WORKLOAD_OR_ENVIRONMENT_CHANGED")
    if len({w.generator for w in windows})!=1: reasons.append("GENERATOR_CHANGED")
    if len({w.window_id for w in windows})!=3: reasons.append("WINDOW_REUSED")
    if len({r.trace_id for w in windows for r in w.requests})!=sum(len(w.requests) for w in windows):
        reasons.append("REQUEST_OBSERVATIONS_REUSED")
    if any(len(w.requests)<policy.minimum_requests for w in windows): reasons.append("INSUFFICIENT_SAMPLES")
    if any(w.max_dispatch_lag_ms>policy.max_dispatch_lag_ms for w in windows): reasons.append("LOAD_GENERATOR_LAG")
    b,f,a=(summaries[k] for k in ("baseline","fault","after"))
    if b['success_rate']<policy.minimum_success_rate or b['quality_rate']<policy.minimum_quality_rate:
        reasons.append("INVALID_NORMAL_BASELINE")
    if fault.config_sha256==after.config_sha256 and fault.revision==after.revision:
        reasons.append("NO_RECORDED_CHANGE")
    threshold=max(b['p95_ms']*policy.recovery_p95_ratio,policy.latency_floor_ms)
    injected=(f['p95_ms']>=max(b['p95_ms']*policy.minimum_fault_ratio,policy.latency_floor_ms)
              or f['success_rate']<policy.minimum_success_rate)
    if not injected: reasons.append("FAULT_NOT_OBSERVED")
    if reasons: outcome="INCOMPARABLE"
    elif a['degraded_count']: outcome="DEGRADED_AVAILABLE" if a['success_rate']>=policy.minimum_success_rate and a['p95_ms']<=threshold else "REJECTED"
    elif a['success_rate']>=policy.minimum_success_rate and a['quality_rate']>=max(policy.minimum_quality_rate,b['quality_rate']) and a['p95_ms']<=threshold:
        outcome="IMPROVEMENT_VERIFIED"
    else: outcome="REJECTED"
    return {"schema":"mini-drop.business-comparison.v1","outcome":outcome,"reasons":reasons,
            "summaries":summaries,"policy":policy.model_dump(),"recovery_p95_limit_ms":threshold,
            "change_summary":change_summary,"window_hashes":[canonical_hash(w.model_dump()) for w in windows],
            "scope":"BUSINESS_MEASUREMENT_ONLY; NOT_AI_ROOT_CAUSE_ACCEPTANCE"}
