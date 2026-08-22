"""Versioned executable benchmark cases and oracle-only scoring.

The diagnosis planner receives ``query`` and runtime context.  ``oracle`` is
loaded by the evaluator only after a diagnosis concludes, preventing the
expected answer from leaking into AI/tool context.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from server.app.evaluation.conclusion_projection import project_conclusion


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CASE_ROOT = ROOT / "benchmarks" / "cases"


_EVIDENCE_TOKEN_HINTS: dict[str, tuple[str, ...]] = {
    "cpu_metric_change": ("process_cpu_core_usage", "avg_cpu_user_pct", "cpu_pct"),
    "profile_hot_function": ("hot_functions", "hot_function", "flamegraph", "topn", "stack_samples"),
    "source_location": ("source_location", "source_file", "line_number", "unresolved_symbols"),
    "trace_error_edge": ("trace_error_edge", "error_edge", "span_error", "trace_id"),
    "connection_error": ("connection_error", "connection_refused", "unreachable", "econnrefused"),
    "gc_pause_or_count_change": ("gc_pause", "gc_count", "full_gc", "collection_count"),
    "latency_correlation": ("latency_correlation", "duration_ms", "p95_latency", "latency_ms"),
    "io_latency_distribution": ("io_latency", "block_latency", "bio_latency", "latency_histogram"),
    "peer_pressure": ("peer_pressure", "same_host_peer", "neighbor_pressure"),
    "request_rate_change": ("request_rate", "requests_per_second", "rps"),
    "resource_saturation": ("resource_saturation", "process_cpu_core_usage", "load1m"),
    "latency_change": ("latency_change", "p95_latency", "latency_ms"),
    "rss_growth": ("vmrss_slope", "rss_growth", "vmrss_trend"),
    "memory_profile_growth": ("memory_profile", "heap_growth", "retained_bytes"),
    "trace_edge_latency": ("trace_edge_latency", "span_duration", "dependency_latency"),
    "service_latency_change": ("service_latency", "p95_latency", "latency_ms"),
    "target_cpu_profile": ("target_cpu_profile", "hot_functions", "flamegraph", "stack_samples"),
    "peer_cpu_pressure": ("peer_cpu_pressure", "same_host_peer_cpu", "neighbor_cpu"),
    "queue_lag_growth": ("queue_lag", "consumer_lag", "lag_growth"),
    "producer_consumer_rate_gap": ("producer_rate", "consumer_rate", "rate_gap"),
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BenchmarkTrigger(_StrictModel):
    adapter: Literal["mini_drop_golden", "otel_feature_flag", "rcaeval", "swe_perf"]
    action: str = Field(min_length=1, max_length=128)
    parameters: dict[str, Any] = Field(default_factory=dict)


class BenchmarkTopology(_StrictModel):
    mode: Literal[
        "single_host_single_agent",
        "single_host_multi_process_single_agent",
        "multi_host_single_agent_each",
    ]
    agent_deployment: Literal["one_agent_per_host"] = "one_agent_per_host"
    minimum_hosts: int = Field(default=1, ge=1, le=20)

    @model_validator(mode="after")
    def multi_host_requires_two_hosts(self):
        if self.mode == "multi_host_single_agent_each" and self.minimum_hosts < 2:
            raise ValueError("multi-host benchmark requires at least two hosts")
        return self


class BenchmarkEvidencePlan(_StrictModel):
    snapshot_roles: list[
        Literal["incident", "baseline", "peer", "verification", "reproduction"]
    ] = Field(min_length=1)
    required_evidence: list[str] = Field(min_length=1)


class BenchmarkOracle(_StrictModel):
    expected_scope: str = Field(min_length=1, max_length=128)
    expected_root_cause: str = Field(min_length=3, max_length=1000)
    expected_terminal_class: Literal["PERFORMANCE", "RELIABILITY", "UNKNOWN"]
    expected_location_type: Literal[
        "self", "same_host", "downstream", "shared_resource", "unknown",
    ]
    expected_domain_type: Literal[
        "cpu", "io", "memory", "network", "database", "runtime", "unknown",
    ]
    expected_classification: str | None = Field(default=None, max_length=128)
    expected_instance_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def classification_matches_location_contract(self):
        expected_by_location = {
            "self": "self_code_or_process_pressure",
            "same_host": "same_host_noisy_neighbor",
            "downstream": "downstream_dependency",
            "shared_resource": "host_resource_contention",
            "unknown": "insufficient_evidence",
        }
        expected = expected_by_location[self.expected_location_type]
        if self.expected_classification not in {None, expected}:
            raise ValueError(
                "expected_classification must follow the cluster-location "
                f"contract: {self.expected_location_type} -> {expected}"
            )
        return self


class BenchmarkExecution(_StrictModel):
    warmup_runs: int = Field(default=1, ge=0, le=10)
    repetitions: int = Field(default=3, ge=1, le=20)
    timeout_seconds: int = Field(default=600, ge=30, le=7200)


class BenchmarkCase(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    case_id: str = Field(min_length=3, max_length=128)
    title: str = Field(min_length=3, max_length=256)
    source_id: str = Field(min_length=1, max_length=128)
    fault_type: str = Field(min_length=2, max_length=128)
    query: str = Field(min_length=3, max_length=2000)
    trigger: BenchmarkTrigger
    topology: BenchmarkTopology
    evidence_plan: BenchmarkEvidencePlan
    oracle: BenchmarkOracle
    execution: BenchmarkExecution = Field(default_factory=BenchmarkExecution)
    reference_ids: list[str] = Field(default_factory=list)


def load_benchmark_cases(root: Path | None = None) -> list[dict[str, Any]]:
    case_root = root or DEFAULT_CASE_ROOT
    cases: list[dict[str, Any]] = []
    for path in sorted(case_root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases.append(BenchmarkCase.model_validate(payload).model_dump(mode="json"))
    return cases


def load_benchmark_case(case_id: str, root: Path | None = None) -> dict[str, Any]:
    matches = [
        case for case in load_benchmark_cases(root)
        if case["case_id"] == case_id
    ]
    if not matches:
        raise KeyError(f"benchmark case not found: {case_id}")
    if len(matches) > 1:
        raise ValueError(f"duplicate benchmark case: {case_id}")
    return matches[0]


def evaluation_request(case: dict[str, Any]) -> dict[str, Any]:
    """Return fields accepted by EvaluationOracle, isolated from planner input."""
    oracle = case["oracle"]
    return {
        "case_id": case["case_id"],
        "expected_instance_id": oracle.get("expected_instance_id"),
        "expected_location_type": oracle["expected_location_type"],
        "expected_domain_type": oracle["expected_domain_type"],
        "expected_classification": oracle.get("expected_classification"),
    }


def observed_evidence_capabilities(detail: dict[str, Any]) -> set[str]:
    """Infer benchmark evidence semantics only from captured evidence records.

    Collectors may emit explicit ``benchmark_evidence_tags``.  For older
    artifacts, stable schema keys are used as a conservative compatibility
    fallback.  Task lifecycle events alone never satisfy a data requirement.
    """

    capabilities: set[str] = set()
    for evidence in detail.get("evidence", []) or []:
        if evidence.get("source_type") == "task_event":
            continue
        # Collector intent/tags do not count when the underlying artifact
        # was not captured or was rejected by a quality gate.
        if str(evidence.get("data_quality") or "").upper() in {
            "MISSING",
            "INVALID",
            "REJECTED",
        }:
            continue
        observed = evidence.get("observed_value") or {}
        explicit = observed.get("benchmark_evidence_tags") or evidence.get(
            "benchmark_evidence_tags", []
        )
        capabilities.update(str(item) for item in explicit)
        searchable = json.dumps(
            {
                "query_or_probe": evidence.get("query_or_probe"),
                "raw_artifact_ref": evidence.get("raw_artifact_ref"),
                "derived_artifact_ref": evidence.get("derived_artifact_ref"),
                "observed_value": observed,
                "data_quality": evidence.get("data_quality"),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).lower()
        for requirement, hints in _EVIDENCE_TOKEN_HINTS.items():
            if any(hint in searchable for hint in hints):
                capabilities.add(requirement)

        # A perf/py-spy/pprof task name is not proof by itself.  A derived
        # artifact with stack/flame data is required before claiming a hotspot.
        probe = str(evidence.get("query_or_probe") or "").lower()
        if probe in {"perf_cpu", "pyspy", "py-spy", "pprof", "java_async"}:
            if any(token in searchable for token in ("flame", "stack", "topn", "hot_function")):
                capabilities.update({"profile_hot_function", "target_cpu_profile"})

    return capabilities


def evidence_requirement_result(
    case: dict[str, Any], detail: dict[str, Any]
) -> dict[str, Any]:
    required = list(case["evidence_plan"]["required_evidence"])
    observed = observed_evidence_capabilities(detail)
    captured = [item for item in required if item in observed]
    missing = [item for item in required if item not in observed]
    coverage = len(captured) / len(required) if required else 1.0
    return {
        "required": required,
        "captured": captured,
        "missing": missing,
        "coverage": coverage,
    }


def score_diagnosis_detail(case: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    """Score a finished diagnosis without exposing the oracle to the planner."""
    projection = project_conclusion(detail.get("latest_conclusion") or {})
    assessment = projection.assessment
    root_location = projection.root_location
    domain_cause = projection.domain_cause
    oracle = case["oracle"]
    checks: list[dict[str, Any]] = []

    def add(name: str, expected: Any, actual: Any) -> None:
        if expected is not None:
            checks.append({
                "name": name,
                "expected": expected,
                "actual": actual,
                "matched": expected == actual,
            })

    add("location_type", oracle["expected_location_type"], root_location.get("type"))
    add("domain_type", oracle["expected_domain_type"], domain_cause.get("type"))
    add("classification", oracle.get("expected_classification"), assessment.get("classification"))
    add(
        "instance_id",
        oracle.get("expected_instance_id"),
        root_location.get("target_ref"),
    )

    snapshots = detail.get("evidence_snapshots") or []
    available_roles = {item.get("evidence_role") for item in snapshots}
    required_roles = set(case["evidence_plan"]["snapshot_roles"])
    evidence_ids = {item.get("evidence_id") for item in detail.get("evidence", [])}
    referenced_ids = set(assessment.get("evidence_refs", []))
    evidence_integrity = bool(referenced_ids) and referenced_ids.issubset(evidence_ids)
    role_coverage = len(required_roles & available_roles) / len(required_roles)
    requirement_result = evidence_requirement_result(case, detail)
    requirement_coverage = requirement_result["coverage"]
    matched = sum(1 for item in checks if item["matched"])
    exact = bool(checks and matched == len(checks))
    root_score = matched / len(checks) if checks else 0.0
    total_score = round(
        (
            root_score * 0.6
            + requirement_coverage * 0.2
            + role_coverage * 0.1
            + (0.1 if evidence_integrity else 0.0)
        ) * 100,
        1,
    )
    status = detail.get("status")
    completion_calibrated = not (
        status == "COMPLETED"
        and (requirement_result["missing"] or role_coverage < 1.0)
    )
    return {
        "case_id": case["case_id"],
        "strategy": detail.get("normalized_intent", {}).get("analysis_strategy"),
        "checks": checks,
        "root_cause_exact_match": exact,
        "root_cause_score_pct": round(root_score * 100, 1),
        "snapshot_role_coverage_pct": round(role_coverage * 100, 1),
        "required_evidence_coverage_pct": round(requirement_coverage * 100, 1),
        "captured_required_evidence": requirement_result["captured"],
        "missing_required_evidence": requirement_result["missing"],
        "evidence_integrity": evidence_integrity,
        "unsupported_claim": bool(referenced_ids - evidence_ids),
        "completion_calibrated": completion_calibrated,
        "score_pct": total_score,
        "oracle_isolated": True,
    }
