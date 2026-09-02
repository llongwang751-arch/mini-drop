"""Large, deterministic Skill retrieval A/B and calibration benchmark.

The benchmark deliberately measures the part that can be reproduced without
pretending generated text is Linux telemetry: selecting the correct root-cause
route prior, rejecting incompatible Skills, and staying deterministic under
repeated load. Evidence-verified root-cause accuracy remains a separate live
Campaign metric.
"""

from __future__ import annotations

import hashlib
import math
import random
import statistics
import time
import tracemalloc
from collections import Counter
from dataclasses import dataclass
from typing import Any

from server.app.models import DiagnosticSkillModel

from .retrieval_benchmark import _skill
from .skill_evolution import _rank_hybrid_skills, _select_ranked_skill


DEFAULT_SEED = 20260902
CURRENT_MATCH_THRESHOLD = 0.35
CURRENT_AMBIGUITY_MARGIN = 0.04

_PREFIXES = (
    "告警显示",
    "用户反馈",
    "复现环境发现",
    "值班记录",
    "during the incident",
    "production symptom",
)
_SUFFIXES = (
    "请给出下一步取证方向",
    "需要定位主要原因",
    "现象持续了十分钟",
    "after the latest deployment",
    "with elevated tail latency",
    "and the service has not recovered",
)
_NOISE = (
    "trace id unavailable",
    "dashboard refreshed twice",
    "业务流量基本稳定",
    "暂未发现发布变更",
    "告警可能存在一分钟延迟",
    "the caller retried once",
)
_REPLACEMENTS = (
    ("serialization", "serialisation"),
    ("serialisation", "JSON encoding"),
    ("CPU spike", "processor saturation"),
    ("backtracking", "catastrophic pattern matching"),
    ("malloc free", "allocator allocation and release"),
    ("write-back", "writeback"),
    ("cache miss", "uncached access"),
    ("connection pool", "database pool"),
    ("deadlock", "circular lock wait"),
    ("DNS lookup", "name resolution"),
    ("packet loss", "dropped packets"),
    ("TLS handshake", "secure connection handshake"),
)
_TOOL_CAPABILITY = {
    "collect_sys_metrics": "sys_metrics",
    "start_perf_profile": "perf_cpu",
    "start_ebpf_io_profile": "ebpf_io",
    "start_pyspy_profile": "pyspy",
    "start_jvm_profile": "java_async",
    "collect_database_diagnostics": "database_lock",
    "collect_network_diagnostics": "network_diagnostics",
}


@dataclass(frozen=True)
class ScaledCase:
    case_id: str
    base_case_id: str
    variant_family: str
    category: str
    query: str
    target: dict[str, Any]
    expected_skill_id: str | None


def assign_ab_arm(
    diagnosis_id: str,
    *,
    experiment_salt: str = "mini-drop-skill-ab-v1",
    treatment_ratio: float = 0.5,
) -> str:
    """Return a sticky A/B assignment without storing personal information."""

    if not 0.0 <= treatment_ratio <= 1.0:
        raise ValueError("treatment_ratio must be between zero and one")
    digest = hashlib.sha256(
        f"{experiment_salt}:{diagnosis_id}".encode("utf-8")
    ).digest()
    bucket = int.from_bytes(digest[:8], "big") / float(2**64)
    return "SKILL_ENABLED" if bucket < treatment_ratio else "NO_SKILL"


def _mutate_query(query: str, index: int, rng: random.Random) -> str:
    value = query
    for source, replacement in _REPLACEMENTS:
        if source.lower() in value.lower() and (index + len(source)) % 3 != 0:
            start = value.lower().index(source.lower())
            value = value[:start] + replacement + value[start + len(source) :]
    words = value.split()
    if len(words) > 5 and index % 4 == 0:
        shift = 1 + index % (len(words) - 1)
        words = words[shift:] + words[:shift]
        value = " ".join(words)
    prefix = _PREFIXES[index % len(_PREFIXES)]
    suffix = _SUFFIXES[(index * 3) % len(_SUFFIXES)]
    noise = _NOISE[rng.randrange(len(_NOISE))]
    separator = "，" if index % 2 else "; "
    return f"{prefix}{separator}{value}{separator}{noise}{separator}{suffix}"


def expand_catalog(
    catalog: dict[str, Any],
    *,
    positive_variants: int = 60,
    negative_variants: int = 60,
    drift_variants: int = 10,
    seed: int = DEFAULT_SEED,
) -> list[ScaledCase]:
    """Expand the frozen fixture into paraphrase and safety families."""

    if min(positive_variants, negative_variants, drift_variants) < 0:
        raise ValueError("variant counts must be non-negative")
    skills = {str(item["id"]): item for item in catalog["skills"]}
    rng = random.Random(seed)
    cases: list[ScaledCase] = []
    for base in catalog["cases"]:
        expected = base.get("expected_skill_id")
        count = positive_variants if expected else negative_variants
        family = "POSITIVE_PARAPHRASE" if expected else "PSEUDO_SIMILAR_NEGATIVE"
        for index in range(count):
            cases.append(
                ScaledCase(
                    case_id=f"{base['id']}:{family}:{index:03d}",
                    base_case_id=str(base["id"]),
                    variant_family=family,
                    category=str(base["category"]),
                    query=_mutate_query(str(base["query"]), index, rng),
                    target=dict(base["target"]),
                    expected_skill_id=str(expected) if expected else None,
                )
            )
        if not expected:
            continue
        skill = skills[str(expected)]
        first_tool = str((skill.get("probe_order") or [""])[0])
        capability = _TOOL_CAPABILITY.get(first_tool)
        for index in range(drift_variants):
            drift_target = dict(base["target"])
            drift_target["environment"] = "production-drift"
            cases.append(
                ScaledCase(
                    case_id=f"{base['id']}:ENVIRONMENT_DRIFT:{index:03d}",
                    base_case_id=str(base["id"]),
                    variant_family="ENVIRONMENT_DRIFT",
                    category=str(base["category"]),
                    query=_mutate_query(str(base["query"]), index + 1000, rng),
                    target=drift_target,
                    expected_skill_id=None,
                )
            )
            capability_target = dict(base["target"])
            capability_target["collector_capabilities"] = []
            if capability:
                capability_target["permission_denied"] = [capability]
            cases.append(
                ScaledCase(
                    case_id=f"{base['id']}:CAPABILITY_DRIFT:{index:03d}",
                    base_case_id=str(base["id"]),
                    variant_family="CAPABILITY_DRIFT",
                    category=str(base["category"]),
                    query=_mutate_query(str(base["query"]), index + 2000, rng),
                    target=capability_target,
                    expected_skill_id=None,
                )
            )
    return cases


def _rule_default_by_category(
    skills: list[DiagnosticSkillModel],
) -> dict[str, str]:
    """Represent a static rules-only root-cause prior for the A arm."""

    grouped: dict[str, list[str]] = {}
    for skill in skills:
        grouped.setdefault(skill.category, []).append(skill.id)
    return {category: sorted(ids)[0] for category, ids in grouped.items()}


def _rank_case(
    skills: list[DiagnosticSkillModel], case: ScaledCase
) -> list[tuple[float, dict, DiagnosticSkillModel]]:
    target = {**case.target, "_baseline_tool": "collect_sys_metrics"}
    return _rank_hybrid_skills(skills, case.category, target, case.query)


def _select_with_threshold(
    ranked: list[tuple[float, dict, DiagnosticSkillModel]],
    *,
    threshold: float,
    ambiguity_margin: float,
) -> str | None:
    if not ranked or ranked[0][0] < threshold:
        return None
    if len(ranked) > 1:
        total_margin = ranked[0][0] - ranked[1][0]
        bm25_margin = float(ranked[0][1].get("bm25") or 0) - float(
            ranked[1][1].get("bm25") or 0
        )
        vector_margin = float(ranked[0][1].get("vector") or 0) - float(
            ranked[1][1].get("vector") or 0
        )
        if (
            total_margin < ambiguity_margin
            and bm25_margin < 0.08
            and vector_margin < 0.08
        ):
            return None
    return ranked[0][2].id


def _wilson(successes: int, total: int) -> dict[str, float | int | str | None]:
    if total <= 0:
        return {"status": "NOT_MEASURED", "lower": None, "upper": None}
    z = 1.959963984540054
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(
        rate * (1 - rate) / total + z * z / (4 * total * total)
    ) / denominator
    return {
        "status": "MEASURED",
        "level": 0.95,
        "lower": max(0.0, center - margin),
        "upper": min(1.0, center + margin),
    }


def _metrics(expected: list[str | None], predicted: list[str | None]) -> dict[str, Any]:
    correct = sum(left == right for left, right in zip(expected, predicted))
    positives = [index for index, item in enumerate(expected) if item is not None]
    negatives = [index for index, item in enumerate(expected) if item is None]
    positive_correct = sum(predicted[index] == expected[index] for index in positives)
    negative_correct = sum(predicted[index] is None for index in negatives)
    false_activations = len(negatives) - negative_correct
    return {
        "correct": correct,
        "total": len(expected),
        "accuracy": correct / max(len(expected), 1),
        "accuracy_wilson_95": _wilson(correct, len(expected)),
        "positive_root_cause_prior_accuracy": positive_correct / max(len(positives), 1),
        "positive_correct": positive_correct,
        "positive_total": len(positives),
        "negative_rejection_rate": negative_correct / max(len(negatives), 1),
        "negative_correct": negative_correct,
        "negative_total": len(negatives),
        "false_activation_rate": false_activations / max(len(negatives), 1),
        "false_activations": false_activations,
    }


def evaluate_scaled_ab(
    catalog: dict[str, Any], cases: list[ScaledCase], *, include_cases: bool = True
) -> dict[str, Any]:
    skills = [_skill(item) for item in catalog["skills"]]
    defaults = _rule_default_by_category(skills)
    expected = [case.expected_skill_id for case in cases]
    baseline_predictions = [defaults.get(case.category) for case in cases]
    skill_predictions: list[str | None] = []
    rankings: list[list[tuple[float, dict, DiagnosticSkillModel]]] = []
    for case in cases:
        ranked = _rank_case(skills, case)
        rankings.append(ranked)
        selected = _select_ranked_skill(ranked)
        skill_predictions.append(selected[2].id if selected else None)

    baseline = _metrics(expected, baseline_predictions)
    treatment = _metrics(expected, skill_predictions)
    transitions = Counter()
    by_family: dict[str, Counter] = {}
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        before = baseline_predictions[index] == expected[index]
        after = skill_predictions[index] == expected[index]
        transition = (
            "IMPROVED" if not before and after else
            "REGRESSED" if before and not after else
            "UNCHANGED"
        )
        transitions[transition] += 1
        family = by_family.setdefault(case.variant_family, Counter())
        family["total"] += 1
        family["baseline_correct"] += int(before)
        family["skill_correct"] += int(after)
        if include_cases:
            rows.append(
                {
                    "case_id": case.case_id,
                    "base_case_id": case.base_case_id,
                    "variant_family": case.variant_family,
                    "expected_skill_id": case.expected_skill_id,
                    "baseline_prediction": baseline_predictions[index],
                    "skill_prediction": skill_predictions[index],
                    "transition": transition,
                    "top_score": round(rankings[index][0][0], 6) if rankings[index] else None,
                }
            )

    shadow = {"NO_SKILL": [[], []], "SKILL_ENABLED": [[], []]}
    for index, case in enumerate(cases):
        arm = assign_ab_arm(case.case_id)
        prediction = (
            skill_predictions[index] if arm == "SKILL_ENABLED" else baseline_predictions[index]
        )
        shadow[arm][0].append(expected[index])
        shadow[arm][1].append(prediction)

    return {
        "benchmark_kind": "SCALED_OFFLINE_SKILL_AB_PERTURBATION",
        "dataset": {
            "case_count": len(cases),
            "base_skill_count": len(skills),
            "family_counts": dict(sorted(Counter(case.variant_family for case in cases).items())),
        },
        "metric_name": "symptom_to_root_cause_route_prior_accuracy",
        "baseline_no_skill": baseline,
        "skill_enabled": treatment,
        "delta": {
            "accuracy": treatment["accuracy"] - baseline["accuracy"],
            "accuracy_percentage_points": round(
                (treatment["accuracy"] - baseline["accuracy"]) * 100, 4
            ),
            "positive_accuracy_percentage_points": round(
                (
                    treatment["positive_root_cause_prior_accuracy"]
                    - baseline["positive_root_cause_prior_accuracy"]
                ) * 100,
                4,
            ),
            "improved": transitions["IMPROVED"],
            "regressed": transitions["REGRESSED"],
            "unchanged": transitions["UNCHANGED"],
        },
        "family_results": {
            name: {
                "total": counts["total"],
                "baseline_accuracy": counts["baseline_correct"] / counts["total"],
                "skill_accuracy": counts["skill_correct"] / counts["total"],
            }
            for name, counts in sorted(by_family.items())
        },
        "shadow_ab": {
            "assignment": "SHA256_STICKY_50_50",
            "no_skill": _metrics(*shadow["NO_SKILL"]),
            "skill_enabled": _metrics(*shadow["SKILL_ENABLED"]),
        },
        "measurement_boundary": {
            "route_prior_accuracy": "MEASURED_ON_GENERATED_PERTURBATIONS",
            "evidence_verified_root_cause_accuracy": "NOT_MEASURED",
            "real_linux_collector_execution": "NOT_RUN",
            "production_ab_effect": "NOT_MEASURED",
            "explanation": (
                "A correct Skill ID means the expected diagnostic route prior was selected. "
                "It does not prove that live telemetry supports the root cause."
            ),
        },
        "cases": rows,
    }


def calibrate_retrieval_gates(
    catalog: dict[str, Any], cases: list[ScaledCase]
) -> dict[str, Any]:
    """Grid-search gates on a train split and report untouched validation metrics."""

    skills = [_skill(item) for item in catalog["skills"]]
    ranked = [_rank_case(skills, case) for case in cases]
    train = [index for index, case in enumerate(cases) if int(hashlib.sha256(case.case_id.encode()).hexdigest()[:8], 16) % 5]
    validation = [index for index in range(len(cases)) if index not in set(train)]
    thresholds = [round(0.25 + step * 0.025, 3) for step in range(17)]
    margins = [0.0, 0.02, 0.04, 0.06, 0.08, 0.10]

    def evaluate(indices: list[int], threshold: float, margin: float) -> dict[str, Any]:
        expected = [cases[index].expected_skill_id for index in indices]
        predicted = [
            _select_with_threshold(ranked[index], threshold=threshold, ambiguity_margin=margin)
            for index in indices
        ]
        return _metrics(expected, predicted)

    candidates = []
    for threshold in thresholds:
        for margin in margins:
            metrics = evaluate(train, threshold, margin)
            # False activation is costlier than abstaining on a useful Skill.
            objective = metrics["accuracy"] - 2.0 * metrics["false_activation_rate"]
            candidates.append((objective, metrics["accuracy"], threshold, margin, metrics))
    _, _, threshold, margin, train_metrics = max(
        candidates, key=lambda item: (item[0], item[1], item[2], item[3])
    )
    return {
        "method": "GRID_SEARCH_WITH_FALSE_ACTIVATION_PENALTY",
        "split": {"train": len(train), "validation": len(validation)},
        "selected": {
            "match_threshold": threshold,
            "ambiguity_margin": margin,
            "train_metrics": train_metrics,
            "validation_metrics": evaluate(validation, threshold, margin),
        },
        "current_production_gates": {
            "match_threshold": CURRENT_MATCH_THRESHOLD,
            "ambiguity_margin": CURRENT_AMBIGUITY_MARGIN,
            "validation_metrics": evaluate(
                validation, CURRENT_MATCH_THRESHOLD, CURRENT_AMBIGUITY_MARGIN
            ),
        },
        "boundary": (
            "Generated variants can reject unsafe gate settings, but production thresholds "
            "must be recalibrated with verified live outcomes."
        ),
    }


def run_retrieval_stability(
    catalog: dict[str, Any],
    cases: list[ScaledCase],
    *,
    duration_seconds: float = 0,
    minimum_iterations: int = 5,
) -> dict[str, Any]:
    """Repeat retrieval decisions and detect errors, drift, latency, and heap growth."""

    if duration_seconds < 0 or minimum_iterations < 1:
        raise ValueError("invalid stability test limits")
    tracemalloc.start()
    initial_current, initial_peak = tracemalloc.get_traced_memory()
    started = time.perf_counter()
    latencies: list[float] = []
    errors: list[str] = []
    reference_digest: str | None = None
    decision_drift = 0
    iterations = 0
    while iterations < minimum_iterations or time.perf_counter() - started < duration_seconds:
        iteration_started = time.perf_counter()
        try:
            report = evaluate_scaled_ab(catalog, cases, include_cases=True)
            digest = hashlib.sha256(
                "|".join(str(row["skill_prediction"]) for row in report["cases"]).encode()
            ).hexdigest()
            if reference_digest is None:
                reference_digest = digest
            elif digest != reference_digest:
                decision_drift += 1
        except Exception as exc:  # pragma: no cover - exercised by operational runs
            errors.append(f"{type(exc).__name__}: {exc}")
        latencies.append((time.perf_counter() - iteration_started) * 1000)
        iterations += 1
    elapsed = time.perf_counter() - started
    final_current, final_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    ordered = sorted(latencies)

    def percentile(ratio: float) -> float:
        if not ordered:
            return 0.0
        position = min(int(math.ceil(ratio * len(ordered))) - 1, len(ordered) - 1)
        return ordered[max(position, 0)]

    return {
        "scope": "SKILL_RETRIEVAL_DECISION_LOOP",
        "duration_seconds": elapsed,
        "iterations": iterations,
        "decisions": iterations * len(cases),
        "errors": len(errors),
        "error_samples": errors[:5],
        "decision_drift_iterations": decision_drift,
        "latency_ms_per_iteration": {
            "mean": statistics.fmean(latencies) if latencies else None,
            "p50": percentile(0.50),
            "p95": percentile(0.95),
            "p99": percentile(0.99),
        },
        "python_heap_bytes": {
            "initial_current": initial_current,
            "initial_peak": initial_peak,
            "final_current": final_current,
            "final_peak": final_peak,
            "current_growth": final_current - initial_current,
        },
        "passed": not errors and decision_drift == 0,
        "boundary": (
            "This exercises deterministic Skill retrieval. A full-stack soak must also "
            "run Go API, PostgreSQL, MinIO, C++ Agent, Analyzer, and the model provider."
        ),
    }
