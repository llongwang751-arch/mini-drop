"""Oracle-isolated RCAEval metric replay for Mini-Drop Route Skills.

The diagnostic functions in this module never receive RCAEval case names or
ground-truth labels. A private evaluator loads a telemetry signature first,
freezes both predictions, and only then compares them with the oracle.
"""

from __future__ import annotations

import hashlib
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


METRIC_FAMILIES = ("cpu", "mem", "load", "latency", "error")


@dataclass(frozen=True)
class PrivateCase:
    opaque_id: str
    source_case: str
    dataset: str
    repetition: int
    inject_time: int
    root_cause_service: str
    fault: str
    metrics_path: Path


@dataclass(frozen=True)
class TelemetrySignature:
    """Public diagnostic input with no source path or oracle fields."""

    case_id: str
    dataset: str
    service_family_scores: Mapping[str, Mapping[str, float]]
    family_features: Mapping[str, float]
    pre_samples: int
    post_samples: int


@dataclass(frozen=True)
class RouteSkill:
    """A train-only route prior, not a cached root-cause answer."""

    skill_id: str
    dataset: str
    fault_family: str
    centroid: Mapping[str, float]
    family_weights: Mapping[str, float]
    probe_order: tuple[str, ...]
    training_case_count: int


@dataclass(frozen=True)
class Prediction:
    service_ranking: tuple[str, ...]
    selected_skill_id: str | None
    inferred_fault: str | None
    score: float | None


@dataclass(frozen=True)
class SkillGate:
    min_score: float = 0.0
    min_margin: float = 0.0
    route_blend: float = 1.0
    allowed_skill_ids: tuple[str, ...] | None = None


def _require_duckdb():
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "RCAEval replay needs: pip install -e '.[benchmark]'"
        ) from exc
    return duckdb


def _require_numpy():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "RCAEval replay needs: pip install -e '.[benchmark]'"
        ) from exc
    return np


def opaque_case_id(source_case: str, *, salt: str = "mini-drop-rcaeval-v1") -> str:
    digest = hashlib.sha256(f"{salt}:{source_case}".encode("utf-8")).hexdigest()
    return f"rcaeval-{digest[:16]}"


def load_private_cases(
    index_path: str | Path,
    data_root: str | Path,
    *,
    dataset: str | None = "RE1-OB",
    suite: str | None = None,
) -> list[PrivateCase]:
    """Load private labels and paths; this result must not enter diagnosis code."""

    duckdb = _require_duckdb()
    index_path = Path(index_path)
    data_root = Path(data_root)
    connection = duckdb.connect()
    try:
        if suite:
            where_clause = "suite = ?"
            selector = suite
        elif dataset:
            where_clause = "dataset = ?"
            selector = dataset
        else:
            raise ValueError("dataset or suite must be provided")
        rows = connection.execute(
            f"""
            SELECT "case", dataset, repetition, inject_time,
                   root_cause_service, fault
            FROM read_parquet(?)
            WHERE {where_clause}
            ORDER BY "case"
            """,
            [str(index_path), selector],
        ).fetchall()
    finally:
        connection.close()

    cases = [
        PrivateCase(
            opaque_id=opaque_case_id(str(row[0])),
            source_case=str(row[0]),
            dataset=str(row[1]),
            repetition=int(row[2]),
            inject_time=int(row[3]),
            root_cause_service=str(row[4]),
            fault=str(row[5]),
            metrics_path=data_root / str(row[0]) / "metrics.parquet",
        )
        for row in rows
    ]
    missing = [str(case.metrics_path) for case in cases if not case.metrics_path.is_file()]
    if missing:
        raise FileNotFoundError(
            f"{len(missing)} metric files missing; first: {missing[0]}"
        )
    return cases


def _metric_parts(metric_name: str) -> tuple[str, str] | None:
    for family in METRIC_FAMILIES:
        suffix = f"_{family}"
        if metric_name.endswith(suffix):
            return metric_name[: -len(suffix)], family
    return None


def _robust_shift(pre, post) -> float:
    np = _require_numpy()
    pre = np.asarray(pre, dtype=float)
    post = np.asarray(post, dtype=float)
    pre = pre[np.isfinite(pre)]
    post = post[np.isfinite(post)]
    if pre.size < 20 or post.size < 20:
        return 0.0

    pre_median = float(np.median(pre))
    post_median = float(np.median(post))
    mad = float(np.median(np.abs(pre - pre_median))) * 1.4826
    q25, q75, q90 = (float(item) for item in np.quantile(pre, [0.25, 0.75, 0.90]))
    post_q90 = float(np.quantile(post, 0.90))
    iqr_scale = abs(q75 - q25) / 1.349
    relative_floor = max(abs(pre_median), abs(q90), 1.0) * 0.03
    scale = max(mad, iqr_scale, relative_floor, 1e-9)
    location = abs(post_median - pre_median) / scale
    tail = abs(post_q90 - q90) / scale
    return min(50.0, 0.70 * location + 0.30 * tail)


def load_telemetry_signature(
    metrics_path: str | Path,
    *,
    case_id: str,
    dataset: str,
    inject_time: int,
    window_seconds: int = 1_200,
    guard_seconds: int = 30,
) -> TelemetrySignature:
    """Convert raw metrics into an anonymous pre/post anomaly signature."""

    duckdb = _require_duckdb()
    np = _require_numpy()
    connection = duckdb.connect()
    try:
        arrays = connection.execute(
            "SELECT * FROM read_parquet(?)", [str(metrics_path)]
        ).fetchnumpy()
    finally:
        connection.close()

    times = np.asarray(arrays.pop("time"), dtype=float)
    pre_mask = (
        (times >= inject_time - window_seconds)
        & (times < inject_time - guard_seconds)
    )
    post_mask = (
        (times > inject_time + guard_seconds)
        & (times <= inject_time + window_seconds)
    )
    pre_samples = int(pre_mask.sum())
    post_samples = int(post_mask.sum())
    if pre_samples < 20 or post_samples < 20:
        raise ValueError(
            f"insufficient pre/post samples for {case_id}: {pre_samples}/{post_samples}"
        )

    scores: dict[str, dict[str, float]] = defaultdict(dict)
    for metric_name, values in arrays.items():
        parts = _metric_parts(metric_name)
        if parts is None:
            continue
        service, family = parts
        scores[service][family] = _robust_shift(values[pre_mask], values[post_mask])

    family_features: dict[str, float] = {}
    for family in METRIC_FAMILIES:
        family_values = sorted(
            (families.get(family, 0.0) for families in scores.values()), reverse=True
        )
        top = family_values[0] if family_values else 0.0
        second = family_values[1] if len(family_values) > 1 else 0.0
        family_features[family] = math.log1p(top) + 0.25 * math.log1p(max(0.0, top - second))
    norm = math.sqrt(sum(value * value for value in family_features.values())) or 1.0
    family_features = {key: value / norm for key, value in family_features.items()}
    return TelemetrySignature(
        case_id=case_id,
        dataset=dataset,
        service_family_scores={
            service: dict(families) for service, families in sorted(scores.items())
        },
        family_features=family_features,
        pre_samples=pre_samples,
        post_samples=post_samples,
    )


def _service_ranks(signature: TelemetrySignature, family: str) -> dict[str, int]:
    ordered = sorted(
        signature.service_family_scores,
        key=lambda service: (
            -signature.service_family_scores[service].get(family, 0.0),
            service,
        ),
    )
    return {service: index for index, service in enumerate(ordered, start=1)}


def _family_available(signature: TelemetrySignature, family: str) -> bool:
    return any(
        family in families for families in signature.service_family_scores.values()
    )


def train_route_skills(
    examples: Sequence[tuple[TelemetrySignature, str, str]],
) -> list[RouteSkill]:
    """Learn fault-family route priorities from training repetitions only."""

    grouped: dict[tuple[str, str], list[tuple[TelemetrySignature, str]]] = defaultdict(list)
    for signature, root_service, fault in examples:
        grouped[(signature.dataset, fault)].append((signature, root_service))

    skills: list[RouteSkill] = []
    for (dataset, fault), rows in sorted(grouped.items()):
        centroid = {
            family: statistics.median(
                signature.family_features.get(family, 0.0) for signature, _ in rows
            )
            for family in METRIC_FAMILIES
        }
        centroid_norm = math.sqrt(sum(value * value for value in centroid.values())) or 1.0
        centroid = {key: value / centroid_norm for key, value in centroid.items()}

        quality: dict[str, float] = {}
        for family in METRIC_FAMILIES:
            reciprocal_ranks = []
            for signature, root_service in rows:
                if not _family_available(signature, family):
                    continue
                rank = _service_ranks(signature, family).get(root_service)
                reciprocal_ranks.append(1.0 / rank if rank else 0.0)
            quality[family] = statistics.fmean(reciprocal_ranks) if reciprocal_ranks else 0.0

        raw_weights = {
            family: max(0.02, quality[family] ** 3) for family in METRIC_FAMILIES
        }
        total = sum(raw_weights.values()) or 1.0
        weights = {key: value / total for key, value in raw_weights.items()}
        probe_order = tuple(sorted(METRIC_FAMILIES, key=lambda item: (-weights[item], item)))
        skills.append(
            RouteSkill(
                skill_id=f"rcaeval-{dataset.lower()}-route-{fault}-v1",
                dataset=dataset,
                fault_family=fault,
                centroid=centroid,
                family_weights=weights,
                probe_order=probe_order,
                training_case_count=len(rows),
            )
        )
    return skills


def _cosine(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    numerator = sum(left.get(key, 0.0) * right.get(key, 0.0) for key in METRIC_FAMILIES)
    left_norm = math.sqrt(sum(left.get(key, 0.0) ** 2 for key in METRIC_FAMILIES))
    right_norm = math.sqrt(sum(right.get(key, 0.0) ** 2 for key in METRIC_FAMILIES))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def select_route_skill(
    signature: TelemetrySignature,
    skills: Sequence[RouteSkill],
    *,
    gate: SkillGate = SkillGate(),
) -> tuple[RouteSkill | None, float | None]:
    compatible = [skill for skill in skills if skill.dataset == signature.dataset]
    ranked = sorted(
        ((_cosine(signature.family_features, skill.centroid), skill) for skill in compatible),
        key=lambda item: (-item[0], item[1].skill_id),
    )
    if not ranked:
        return None, None
    margin = ranked[0][0] - ranked[1][0] if len(ranked) > 1 else ranked[0][0]
    if ranked[0][0] < gate.min_score or margin < gate.min_margin:
        return None, ranked[0][0]
    if (
        gate.allowed_skill_ids is not None
        and ranked[0][1].skill_id not in gate.allowed_skill_ids
    ):
        return None, ranked[0][0]
    return ranked[0][1], ranked[0][0]


def _rank_services(
    signature: TelemetrySignature, family_weights: Mapping[str, float]
) -> tuple[str, ...]:
    available = [
        family for family in METRIC_FAMILIES if _family_available(signature, family)
    ]
    if not available:
        return tuple(sorted(signature.service_family_scores))
    weight_total = sum(family_weights.get(family, 0.0) for family in available)
    if weight_total <= 0.0:
        normalized_weights = {family: 1.0 / len(available) for family in available}
    else:
        normalized_weights = {
            family: family_weights.get(family, 0.0) / weight_total
            for family in available
        }
    family_ranks = {
        family: _service_ranks(signature, family) for family in available
    }
    service_scores = {
        service: sum(
            normalized_weights[family]
            / family_ranks[family].get(service, len(signature.service_family_scores) + 1)
            for family in available
        )
        for service in signature.service_family_scores
    }
    return tuple(sorted(service_scores, key=lambda item: (-service_scores[item], item)))


def predict_without_skill(signature: TelemetrySignature) -> Prediction:
    weights = {family: 1.0 / len(METRIC_FAMILIES) for family in METRIC_FAMILIES}
    return Prediction(_rank_services(signature, weights), None, None, None)


def predict_with_skill(
    signature: TelemetrySignature,
    skills: Sequence[RouteSkill],
    *,
    gate: SkillGate = SkillGate(),
) -> Prediction:
    skill, score = select_route_skill(signature, skills, gate=gate)
    if skill is None:
        baseline = predict_without_skill(signature)
        return Prediction(baseline.service_ranking, None, None, score)
    uniform = 1.0 / len(METRIC_FAMILIES)
    weights = {
        family: (
            (1.0 - gate.route_blend) * uniform
            + gate.route_blend * skill.family_weights.get(family, 0.0)
        )
        for family in METRIC_FAMILIES
    }
    return Prediction(
        service_ranking=_rank_services(signature, weights),
        selected_skill_id=skill.skill_id,
        inferred_fault=skill.fault_family,
        score=score,
    )


def _ranking_metrics(expected: Sequence[str], rankings: Sequence[Sequence[str]]) -> dict:
    ranks = []
    for target, ranking in zip(expected, rankings):
        try:
            ranks.append(ranking.index(target) + 1)
        except ValueError:
            ranks.append(len(ranking) + 1)
    total = max(len(ranks), 1)
    top1_correct = sum(rank <= 1 for rank in ranks)
    return {
        "case_count": len(ranks),
        "top1": top1_correct / total,
        "top1_correct": top1_correct,
        "top1_wilson_95": _wilson_interval(top1_correct, len(ranks)),
        "top3": sum(rank <= 3 for rank in ranks) / total,
        "top5": sum(rank <= 5 for rank in ranks) / total,
        "mrr": sum(1.0 / rank for rank in ranks) / total,
        "mean_rank": sum(ranks) / total,
    }


def _wilson_interval(successes: int, total: int) -> dict[str, float | None]:
    if total <= 0:
        return {"lower": None, "upper": None}
    z = 1.959963984540054
    rate = successes / total
    denominator = 1.0 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(
        rate * (1.0 - rate) / total + z * z / (4 * total * total)
    ) / denominator
    return {
        "lower": max(0.0, center - margin),
        "upper": min(1.0, center + margin),
    }


def _paired_exact_p_value(improved: int, regressed: int) -> float:
    discordant = improved + regressed
    if discordant == 0:
        return 1.0
    tail = sum(
        math.comb(discordant, index) for index in range(min(improved, regressed) + 1)
    ) / (2**discordant)
    return min(1.0, 2.0 * tail)


def evaluate_paired_replay(
    test_rows: Sequence[tuple[TelemetrySignature, str, str]],
    skills: Sequence[RouteSkill],
    *,
    gate: SkillGate = SkillGate(),
    include_cases: bool = True,
) -> dict:
    """Freeze both predictions before revealing each private oracle."""

    baseline_predictions = []
    skill_predictions = []
    for signature, _, _ in test_rows:
        baseline_predictions.append(predict_without_skill(signature))
        skill_predictions.append(predict_with_skill(signature, skills, gate=gate))

    expected_services = [root_service for _, root_service, _ in test_rows]
    expected_faults = [fault for _, _, fault in test_rows]
    baseline_metrics = _ranking_metrics(
        expected_services, [prediction.service_ranking for prediction in baseline_predictions]
    )
    skill_metrics = _ranking_metrics(
        expected_services, [prediction.service_ranking for prediction in skill_predictions]
    )
    fault_correct = sum(
        prediction.inferred_fault == fault
        for prediction, fault in zip(skill_predictions, expected_faults)
    )
    activated = sum(
        prediction.selected_skill_id is not None for prediction in skill_predictions
    )
    transitions = Counter()
    case_results = []
    for index, (signature, root_service, fault) in enumerate(test_rows):
        baseline = baseline_predictions[index]
        treatment = skill_predictions[index]
        before = bool(baseline.service_ranking and baseline.service_ranking[0] == root_service)
        after = bool(treatment.service_ranking and treatment.service_ranking[0] == root_service)
        transition = (
            "IMPROVED" if not before and after
            else "REGRESSED" if before and not after
            else "UNCHANGED"
        )
        transitions[transition] += 1
        if include_cases:
            case_results.append(
                {
                    "case_id": signature.case_id,
                    "expected_service": root_service,
                    "expected_fault": fault,
                    "no_skill_top1": baseline.service_ranking[0] if baseline.service_ranking else None,
                    "skill_top1": treatment.service_ranking[0] if treatment.service_ranking else None,
                    "selected_skill_id": treatment.selected_skill_id,
                    "inferred_fault": treatment.inferred_fault,
                    "skill_match_score": treatment.score,
                    "transition": transition,
                }
            )

    report = {
        "benchmark_kind": "RCAEVAL_RE1_METRIC_ROUTE_SKILL_PAIRED_REPLAY",
        "metric": "root_cause_service_ranking",
        "no_skill": baseline_metrics,
        "skill_enabled": skill_metrics,
        "delta": {
            "top1_percentage_points": round(
                (skill_metrics["top1"] - baseline_metrics["top1"]) * 100, 4
            ),
            "mrr": skill_metrics["mrr"] - baseline_metrics["mrr"],
            "improved": transitions["IMPROVED"],
            "regressed": transitions["REGRESSED"],
            "unchanged": transitions["UNCHANGED"],
        },
        "skill_route_selection": {
            "coverage": activated / max(len(test_rows), 1),
            "accuracy_when_activated": fault_correct / max(activated, 1),
            "overall_correct_route_fraction": fault_correct / max(len(test_rows), 1),
            "correct": fault_correct,
            "total": len(test_rows),
            "activated": activated,
            "gate": {
                "min_score": gate.min_score,
                "min_margin": gate.min_margin,
                "route_blend": gate.route_blend,
                "allowed_skill_ids": list(gate.allowed_skill_ids)
                if gate.allowed_skill_ids is not None
                else None,
            },
        },
        "statistical_test": {
            "name": "paired_exact_binomial_on_top1_discordant_cases",
            "p_value_two_sided": _paired_exact_p_value(
                transitions["IMPROVED"], transitions["REGRESSED"]
            ),
            "interpretation": "exploratory; p < 0.05 is required for conventional significance",
        },
        "cases": case_results if include_cases else None,
    }
    report["breakdown_by_dataset"] = _breakdown(
        [signature.dataset for signature, _, _ in test_rows],
        expected_services,
        baseline_predictions,
        skill_predictions,
    )
    report["breakdown_by_fault"] = _breakdown(
        expected_faults,
        expected_services,
        baseline_predictions,
        skill_predictions,
    )
    return report


def _breakdown(
    group_keys: Sequence[str],
    expected_services: Sequence[str],
    baseline_predictions: Sequence[Prediction],
    skill_predictions: Sequence[Prediction],
) -> dict[str, dict]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, key in enumerate(group_keys):
        grouped[key].append(index)
    result = {}
    for key, indices in sorted(grouped.items()):
        expected = [expected_services[index] for index in indices]
        baseline = _ranking_metrics(
            expected,
            [baseline_predictions[index].service_ranking for index in indices],
        )
        treatment = _ranking_metrics(
            expected,
            [skill_predictions[index].service_ranking for index in indices],
        )
        result[key] = {
            "case_count": len(indices),
            "no_skill_top1": baseline["top1"],
            "skill_top1": treatment["top1"],
            "top1_delta_percentage_points": round(
                (treatment["top1"] - baseline["top1"]) * 100, 4
            ),
            "no_skill_mrr": baseline["mrr"],
            "skill_mrr": treatment["mrr"],
        }
    return result


def calibrate_skill_gate(
    fit_rows: Sequence[tuple[TelemetrySignature, str, str]],
    validation_rows: Sequence[tuple[TelemetrySignature, str, str]],
) -> tuple[SkillGate, dict]:
    """Choose a conservative gate without consulting blind-test oracles."""

    skills = train_route_skills(fit_rows)
    candidates = [
        SkillGate(score, margin, blend)
        for score in (0.0, 0.75, 0.85, 0.90, 0.95)
        for margin in (0.0, 0.01, 0.02, 0.04, 0.08)
        for blend in (0.25, 0.50, 0.75, 1.0)
    ]
    rows = []
    for candidate in candidates:
        ungated_report = evaluate_paired_replay(
            validation_rows, skills, gate=candidate, include_cases=True
        )
        skill_outcomes: dict[str, Counter] = defaultdict(Counter)
        for case in ungated_report["cases"]:
            skill_id = case["selected_skill_id"]
            if skill_id:
                skill_outcomes[skill_id][case["transition"]] += 1
        allowed_skill_ids = tuple(
            sorted(
                skill_id
                for skill_id, outcomes in skill_outcomes.items()
                if outcomes["IMPROVED"] > outcomes["REGRESSED"]
                and outcomes["IMPROVED"] > 0
            )
        )
        gate = SkillGate(
            candidate.min_score,
            candidate.min_margin,
            candidate.route_blend,
            allowed_skill_ids,
        )
        report = evaluate_paired_replay(
            validation_rows, skills, gate=gate, include_cases=False
        )
        rows.append(
            {
                "gate": gate,
                "top1": report["skill_enabled"]["top1"],
                "top3": report["skill_enabled"]["top3"],
                "mrr": report["skill_enabled"]["mrr"],
                "improved": report["delta"]["improved"],
                "regressed": report["delta"]["regressed"],
                "activated": report["skill_route_selection"]["activated"],
            }
        )
    selected = max(
        rows,
        key=lambda row: (
            row["top1"],
            row["top3"],
            row["mrr"],
            -row["regressed"],
            row["improved"],
            -row["activated"],
            row["gate"].min_margin,
            row["gate"].min_score,
        ),
    )
    return selected["gate"], {
        "method": "grid_search_on_repetition_3_only",
        "fit_repetitions": [1, 2],
        "validation_repetitions": [3],
        "candidate_count": len(candidates),
        "selected_validation_top1": selected["top1"],
        "selected_validation_top3": selected["top3"],
        "selected_validation_mrr": selected["mrr"],
        "selected_validation_improved": selected["improved"],
        "selected_validation_regressed": selected["regressed"],
        "selected_validation_activated": selected["activated"],
        "allowed_skill_ids": list(selected["gate"].allowed_skill_ids or ()),
    }


def load_signatures(
    cases: Iterable[PrivateCase],
) -> list[tuple[TelemetrySignature, str, str]]:
    rows = []
    for case in cases:
        signature = load_telemetry_signature(
            case.metrics_path,
            case_id=case.opaque_id,
            dataset=case.dataset,
            inject_time=case.inject_time,
        )
        rows.append((signature, case.root_cause_service, case.fault))
    return rows


def load_signatures_with_exclusions(
    cases: Iterable[PrivateCase],
) -> tuple[list[tuple[TelemetrySignature, str, str]], list[dict[str, str]]]:
    """Load valid cases while preserving auditable dataset-quality exclusions."""

    rows = []
    exclusions = []
    for case in cases:
        try:
            signature = load_telemetry_signature(
                case.metrics_path,
                case_id=case.opaque_id,
                dataset=case.dataset,
                inject_time=case.inject_time,
            )
        except ValueError as exc:
            exclusions.append(
                {
                    "case_id": case.opaque_id,
                    "reason_code": "INVALID_WINDOW",
                    "detail": str(exc),
                }
            )
            continue
        rows.append((signature, case.root_cause_service, case.fault))
    return rows, exclusions
