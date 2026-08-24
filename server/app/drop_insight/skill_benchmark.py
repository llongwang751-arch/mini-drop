from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping


REQUIRED_CASE_FIELDS = {
    "case_id",
    "incident_family",
    "symptom",
    "environment",
    "available_collectors",
    "expected_root_cause",
    "expected_action",
    "forbidden_action",
    "oracle_refs",
    "difficulty_reason",
}
EXPECTED_FAMILY_COUNTS = {
    "SIMILAR_INCIDENT": 5,
    "MISLEADING_INCIDENT": 4,
    "ENVIRONMENT_DRIFT": 2,
    "WRONG_FEEDBACK": 2,
    "VERSION_ROLLBACK": 1,
    "CONTAMINATED_EVIDENCE": 1,
}


@dataclass(frozen=True)
class BenchmarkObservation:
    case_id: str
    action: str
    mode: str
    tool_calls: int | None = None
    root_cause: str | None = None
    diagnosis_duration_ms: float | None = None
    skill_status: str | None = None
    restored_version: int | None = None
    candidate_admitted: bool | None = None
    evidence_refs: tuple[str, ...] | None = None
    evidence_integrity: str | None = None
    evidence_refs_valid: bool | None = None


def validate_benchmark_dataset(payload: Mapping) -> None:
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise ValueError("cases must be a list")
    _validate_cases(cases)
    counts = Counter(str(case["incident_family"]) for case in cases)
    if counts != Counter(EXPECTED_FAMILY_COUNTS):
        raise ValueError(f"unexpected incident family composition: {dict(counts)}")

    source_ids = payload.get("source_diagnosis_ids")
    if not isinstance(source_ids, list) or not source_ids:
        raise ValueError("source_diagnosis_ids must be a non-empty list")
    case_ids = {str(case["case_id"]) for case in cases}
    overlap = case_ids.intersection(map(str, source_ids))
    if overlap:
        raise ValueError(f"source diagnoses overlap evaluation cases: {sorted(overlap)}")


def _validate_cases(cases: Iterable[Mapping]) -> list[Mapping]:
    case_list = list(cases)
    if not case_list:
        raise ValueError("benchmark cases must not be empty")
    seen: set[str] = set()
    for case in case_list:
        missing = REQUIRED_CASE_FIELDS.difference(case)
        if missing:
            raise ValueError(
                f"case {case.get('case_id', '<unknown>')} missing fields: {sorted(missing)}"
            )
        case_id = case["case_id"]
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("case_id must be a non-empty string")
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        if not isinstance(case["available_collectors"], list):
            raise ValueError(f"available_collectors must be a list for {case_id}")
        if not isinstance(case["oracle_refs"], list) or not case["oracle_refs"]:
            raise ValueError(f"oracle_refs must be a non-empty list for {case_id}")
        if not all(isinstance(ref, str) and ref for ref in case["oracle_refs"]):
            raise ValueError(f"oracle_refs must contain non-empty strings for {case_id}")
        forbidden = case["forbidden_action"]
        if not isinstance(forbidden, str) or not forbidden:
            raise ValueError(f"forbidden_action must be a non-empty string for {case_id}")
    return case_list


def _validate_observations(
    case_ids: set[str], observations: Iterable[BenchmarkObservation]
) -> dict[str, BenchmarkObservation]:
    observed: dict[str, BenchmarkObservation] = {}
    for item in observations:
        if not isinstance(item, BenchmarkObservation):
            raise TypeError("observations must contain BenchmarkObservation values")
        if item.case_id not in case_ids:
            raise ValueError(f"unexpected observation case_id: {item.case_id}")
        if item.case_id in observed:
            raise ValueError(f"duplicate observation case_id: {item.case_id}")
        if not item.action or not item.mode:
            raise ValueError(f"action and mode are required for {item.case_id}")
        if item.tool_calls is not None and item.tool_calls < 0:
            raise ValueError(f"tool_calls must be non-negative for {item.case_id}")
        if item.diagnosis_duration_ms is not None and item.diagnosis_duration_ms < 0:
            raise ValueError(
                f"diagnosis_duration_ms must be non-negative for {item.case_id}"
            )
        observed[item.case_id] = item
    return observed


def _rate(numerator: int, denominator: int, *, status: str = "MEASURED") -> dict:
    return {
        "status": status if denominator else "NOT_MEASURED",
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def _average(values: list[float], *, status: str) -> dict:
    return {
        "status": status if values else "NOT_MEASURED",
        "sample_count": len(values),
        "value": sum(values) / len(values) if values else None,
    }


def _score_run(
    cases: Iterable[Mapping], observations: Iterable[BenchmarkObservation]
) -> dict:
    case_list = _validate_cases(cases)
    observed = _validate_observations(
        {str(case["case_id"]) for case in case_list}, observations
    )
    rows = []
    family_counts: Counter[str] = Counter()
    family_passes: Counter[str] = Counter()
    correct = 0
    negative_transfers = 0
    tool_calls: list[float] = []
    durations: list[float] = []
    root_cause_correct = 0
    root_cause_measured = 0
    misleading_correct = 0
    drift_correct = 0
    quarantine_correct = 0
    quarantine_total = 0
    rollback_correct = 0
    evidence_complete = 0
    evidence_measured = 0

    for case in case_list:
        case_id = str(case["case_id"])
        family = str(case["incident_family"])
        item = observed.get(case_id)
        acceptable = set(case.get("acceptable_actions") or [case["expected_action"]])
        forbidden = {str(case["forbidden_action"])}
        expected_mode = str(case.get("expected_mode") or "APPLY")
        family_counts[family] += 1

        if item is None:
            passed = False
            reason = "MISSING_OBSERVATION"
            action = ""
            mode = ""
            negative_transfer = False
        else:
            action = item.action
            mode = item.mode
            negative_transfer = action in forbidden or (
                expected_mode == "FALLBACK" and mode == "APPLY"
            )
            passed = action in acceptable and mode == expected_mode and not negative_transfer
            if family == "WRONG_FEEDBACK":
                passed = passed and item.skill_status == case["expected_skill_status_after"]
            elif family == "VERSION_ROLLBACK":
                passed = passed and item.restored_version == case["expected_restored_version"]
            elif family == "CONTAMINATED_EVIDENCE":
                passed = passed and item.candidate_admitted is case["expected_candidate_admission"]
            reason = "PASS" if passed else (
                "NEGATIVE_TRANSFER" if negative_transfer else "ORACLE_MISMATCH"
            )

            if item.tool_calls is not None:
                tool_calls.append(float(item.tool_calls))
            if item.diagnosis_duration_ms is not None:
                durations.append(float(item.diagnosis_duration_ms))
            if item.root_cause is not None:
                root_cause_measured += 1
                root_cause_correct += item.root_cause == case["expected_root_cause"]
            if item.evidence_refs_valid is not None:
                evidence_measured += 1
                evidence_complete += item.evidence_refs_valid

        if passed:
            correct += 1
            family_passes[family] += 1
        if negative_transfer:
            negative_transfers += 1
        if family == "MISLEADING_INCIDENT" and passed:
            misleading_correct += 1
        if family == "ENVIRONMENT_DRIFT" and passed:
            drift_correct += 1
        if family == "WRONG_FEEDBACK" and case["expected_skill_status_after"] == "QUARANTINED":
            quarantine_total += 1
            if passed:
                quarantine_correct += 1
        if family == "VERSION_ROLLBACK" and passed:
            rollback_correct += 1

        rows.append(
            {
                "case_id": case_id,
                "incident_family": family,
                "passed": passed,
                "reason": reason,
                "expected_action": case["expected_action"],
                "expected_mode": expected_mode,
                "observed_action": action,
                "observed_mode": mode,
                "tool_calls": item.tool_calls if item is not None else None,
                "negative_transfer": negative_transfer,
                "oracle_refs": case["oracle_refs"],
            }
        )

    total = len(case_list)
    misleading_total = family_counts["MISLEADING_INCIDENT"]
    drift_total = family_counts["ENVIRONMENT_DRIFT"]
    rollback_total = family_counts["VERSION_ROLLBACK"]
    return {
        "total": total,
        "passed": correct,
        "pass_rate": correct / total,
        "metrics": {
            "root_cause_accuracy": _rate(root_cause_correct, root_cause_measured),
            "misleading_counterexample_refusal_rate": _rate(
                misleading_correct, misleading_total
            ),
            "environment_drift_correct_degradation_rate": _rate(
                drift_correct, drift_total
            ),
            "average_tool_call_count": _average(tool_calls, status="PROJECTED"),
            "average_diagnosis_duration_ms": _average(durations, status="MEASURED"),
            "negative_transfer_rate": _rate(negative_transfers, total),
            "quarantine_success_rate": _rate(quarantine_correct, quarantine_total),
            "rollback_success_rate": _rate(rollback_correct, rollback_total),
            "evidence_reference_completeness_rate": _rate(
                evidence_complete, evidence_measured
            ),
        },
        "family_results": {
            family: {
                "passed": family_passes[family],
                "total": count,
                "pass_rate": family_passes[family] / count,
            }
            for family, count in sorted(family_counts.items())
        },
        "cases": rows,
    }


def compare_benchmark_runs(
    cases: Iterable[Mapping],
    baseline: Iterable[BenchmarkObservation],
    skill_enabled: Iterable[BenchmarkObservation],
) -> dict:
    case_list = _validate_cases(cases)
    baseline_result = _score_run(case_list, baseline)
    skill_result = _score_run(case_list, skill_enabled)
    baseline_rows = {row["case_id"]: row for row in baseline_result["cases"]}
    negative_transfer_count = 0
    similar_benefit_count = 0
    similar_benefit_total = 0
    for row in skill_result["cases"]:
        baseline_row = baseline_rows[row["case_id"]]
        baseline_calls = baseline_row["tool_calls"]
        skill_calls = row["tool_calls"]
        regressed = baseline_row["passed"] and not row["passed"]
        cost_regressed = (
            baseline_calls is not None
            and skill_calls is not None
            and skill_calls > baseline_calls
        )
        row["negative_transfer"] = row["negative_transfer"] or regressed or cost_regressed
        if row["negative_transfer"]:
            negative_transfer_count += 1
        if row["incident_family"] == "SIMILAR_INCIDENT":
            similar_benefit_total += 1
            if (
                row["passed"]
                and baseline_calls is not None
                and skill_calls is not None
                and skill_calls < baseline_calls
            ):
                similar_benefit_count += 1
    skill_result["metrics"]["negative_transfer_rate"] = _rate(
        negative_transfer_count, len(case_list)
    )
    skill_result["metrics"]["similar_incident_benefit_rate"] = _rate(
        similar_benefit_count, similar_benefit_total
    )
    baseline_average_calls = baseline_result["metrics"]["average_tool_call_count"]["value"]
    skill_average_calls = skill_result["metrics"]["average_tool_call_count"]["value"]
    return {
        "benchmark_kind": "DETERMINISTIC_SKILL_EVOLUTION_REPLAY",
        "claim_boundary": (
            "Validates offline routing and lifecycle contracts with projected cost; "
            "real root-cause accuracy and diagnosis duration require a Linux Campaign."
        ),
        "baseline": baseline_result,
        "skill_enabled": skill_result,
        "delta": {
            "pass_rate": skill_result["pass_rate"] - baseline_result["pass_rate"],
            "average_tool_call_count": (
                skill_average_calls - baseline_average_calls
                if skill_average_calls is not None and baseline_average_calls is not None
                else None
            ),
            "negative_transfer_rate": (
                skill_result["metrics"]["negative_transfer_rate"]["value"]
                - baseline_result["metrics"]["negative_transfer_rate"]["value"]
            ),
        },
    }
