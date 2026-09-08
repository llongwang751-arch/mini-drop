"""Controlled root-cause replay benchmark built from executable Fault Plaza contracts.

This module deliberately separates three claims:

* the Fault Plaza contract provides the root-cause ground truth;
* the production Skill selector may change the evidence route;
* a deterministic closed-set scorer predicts a fault scenario from the query and
  only the observations exposed by tools that the arm actually reached.

It is an offline, repeatable replay.  It never claims that hundreds of Linux
faults were injected on the public interview server.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
from collections import Counter
from typing import Any

from server.app.models import DiagnosticSkillModel

from .benchmark_v2 import _runtime_skill, canonical_sha256
from .fault_plaza import SCENARIOS
from .skill_evolution import _rank_hybrid_skills, _select_ranked_skill


SCHEMA = "mini-drop.controlled-root-cause.v1"
REPORT_SCHEMA = "mini-drop.controlled-root-cause-report.v1"

TOOL_TO_COLLECTOR = {
    "collect_sys_metrics": "sys_metrics",
    "start_perf_profile": "perf_cpu",
    "start_continuous_profile": "continuous_perf",
    "start_ebpf_io_profile": "ebpf_io",
    "start_pyspy_profile": "pyspy",
    "start_jvm_profile": "java_async",
    "collect_memory_profile": "memory_smaps",
    "collect_go_profile": "go_pprof",
}

BASELINE_ROUTES = {
    "CPU_HOTSPOT": ["collect_sys_metrics", "start_perf_profile", "start_continuous_profile"],
    "PYTHON_RUNTIME": ["collect_sys_metrics", "start_perf_profile", "start_pyspy_profile"],
    "GO_RUNTIME": ["collect_sys_metrics", "collect_go_profile", "start_perf_profile"],
    "JVM_GC": ["collect_sys_metrics", "collect_memory_profile", "start_jvm_profile"],
    "MEMORY_PRESSURE": ["collect_sys_metrics", "start_perf_profile", "collect_memory_profile"],
    "IO_LATENCY": ["collect_sys_metrics", "start_perf_profile", "start_ebpf_io_profile"],
    "LOCK_CONTENTION": ["collect_sys_metrics", "start_perf_profile", "start_continuous_profile"],
    "NETWORK_DEGRADATION": ["collect_sys_metrics", "start_perf_profile"],
    "DOWNSTREAM_DEPENDENCY": ["collect_sys_metrics", "start_perf_profile"],
    "NOISY_NEIGHBOR": ["collect_sys_metrics", "start_perf_profile"],
    "QUEUE_CONGESTION": ["collect_sys_metrics", "start_perf_profile"],
    "LOAD_SATURATION": ["collect_sys_metrics", "start_perf_profile"],
}

_TOKEN_RE = re.compile(r"[a-z0-9_+.-]+|[\u4e00-\u9fff]", re.IGNORECASE)


def _tokens(value: Any) -> set[str]:
    text = str(value or "").casefold()
    raw = _TOKEN_RE.findall(text)
    chinese = [item for item in raw if len(item) == 1 and "\u4e00" <= item <= "\u9fff"]
    tokens = {item for item in raw if len(item) > 1}
    tokens.update("".join(chinese[index : index + 2]) for index in range(len(chinese) - 1))
    return {item for item in tokens if item.strip()}


def _scenario_profiles() -> list[dict[str, Any]]:
    return [
        {
            "scenario_id": item.scenario_id,
            "runtime": item.target_runtime,
            "family": item.family,
            "related_skill": item.related_skill,
            "tokens": _tokens(
                " ".join(
                    [
                        item.title,
                        item.family,
                        item.symptom,
                        item.diagnosis_query,
                        *item.expected_signals,
                    ]
                )
            ),
        }
        for item in SCENARIOS
    ]


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


def _collector_route(tools: list[str]) -> list[str]:
    return _dedupe([TOOL_TO_COLLECTOR.get(item, item) for item in tools])


def _skill_route(
    skills: list[DiagnosticSkillModel],
    case: dict[str, Any],
) -> tuple[list[str], str | None, dict[str, Any] | None]:
    target = {
        **dict(case.get("target") or {}),
        "_baseline_tool": "collect_sys_metrics",
    }
    ranked = _rank_hybrid_skills(
        skills,
        str(case.get("category") or ""),
        target,
        str(case.get("query") or ""),
    )
    selected = _select_ranked_skill(ranked)
    if selected is None:
        return [], None, None
    score, reason, skill = selected
    route = list((skill.strategy_json or {}).get("probe_order") or [])
    return route, skill.id, {"score": round(float(score), 6), **dict(reason or {})}


def _available_observations(case: dict[str, Any], route: list[str], budget: int) -> list[str]:
    observations = case.get("observations_by_collector") or {}
    values: list[str] = []
    for collector in _collector_route(route)[:budget]:
        values.extend(str(item) for item in observations.get(collector) or [])
    return values


def _predict_scenario(
    case: dict[str, Any],
    observations: list[str],
    *,
    selected_skill: str | None,
) -> tuple[str | None, float]:
    query_tokens = _tokens(case.get("query"))
    observation_tokens = _tokens(" ".join(observations))
    runtime = str((case.get("target") or {}).get("runtime") or "").casefold()
    category_family = str(case.get("family_hint") or "").casefold()
    best: tuple[float, str] | None = None
    for profile in _scenario_profiles():
        profile_runtime = str(profile["runtime"]).casefold()
        if runtime and runtime != profile_runtime:
            continue
        profile_tokens = profile["tokens"]
        query_overlap = len(query_tokens & profile_tokens) / max(math.sqrt(len(query_tokens) * len(profile_tokens)), 1)
        evidence_overlap = len(observation_tokens & profile_tokens) / max(math.sqrt(len(observation_tokens) * len(profile_tokens)), 1)
        family_bonus = 0.08 if category_family and category_family == str(profile["family"]).casefold() else 0.0
        skill_bonus = 0.16 if selected_skill and selected_skill == profile["related_skill"] else 0.0
        score = 0.38 * query_overlap + 0.54 * evidence_overlap + family_bonus + skill_bonus
        candidate = (score, str(profile["scenario_id"]))
        if best is None or candidate > best:
            best = candidate
    if best is None or best[0] < 0.08:
        return None, 0.0
    return best[1], round(best[0], 6)


def _wilson(successes: int, total: int) -> dict[str, float | int | str | None]:
    if total <= 0:
        return {"status": "NOT_MEASURED", "lower": None, "upper": None}
    z = 1.959963984540054
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / denominator
    return {
        "status": "MEASURED",
        "level": 0.95,
        "lower": round(max(0.0, center - margin), 6),
        "upper": round(min(1.0, center + margin), 6),
    }


def _arm_metrics(rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    correct = sum(bool(item[arm]["root_cause_correct"]) for item in rows)
    text_matches = sum(
        bool(item[arm]["text_prediction_matches_oracle"]) for item in rows
    )
    decisive = [item[arm]["decisive_collector_position"] for item in rows]
    decisive_hits = [value for value in decisive if value is not None]
    within_two = sum(value is not None and value <= 2 for value in decisive)
    return {
        "correct": correct,
        "total": len(rows),
        "root_cause_top1_accuracy": round(correct / max(len(rows), 1), 6),
        "accuracy_wilson_95": _wilson(correct, len(rows)),
        "text_prediction_matches_oracle": text_matches,
        "text_prediction_matches_oracle_rate": round(
            text_matches / max(len(rows), 1), 6
        ),
        "decisive_collector_within_two": within_two,
        "decisive_collector_within_two_rate": round(within_two / max(len(rows), 1), 6),
        "mean_decisive_collector_position": round(sum(decisive_hits) / len(decisive_hits), 4) if decisive_hits else None,
    }


def _skill_retrieval_metrics(
    rows: list[dict[str, Any]],
    *,
    catalog_skill_ids: set[str],
) -> dict[str, Any]:
    selected = sum(bool(item["skill_retrieval"].get("selected_skill_id")) for item in rows)
    matched = sum(bool(item["skill_retrieval"].get("matched_expected")) for item in rows)
    missing_expected_ids = sorted(
        {
            str(item.get("expected_skill_id"))
            for item in rows
            if item.get("expected_skill_id")
            and str(item.get("expected_skill_id")) not in catalog_skill_ids
        }
    )
    missing_expected_cases = sum(
        bool(item.get("expected_skill_id"))
        and str(item.get("expected_skill_id")) not in catalog_skill_ids
        for item in rows
    )
    return {
        "total": len(rows),
        "catalog_skill_count": len(catalog_skill_ids),
        "selected_any": selected,
        "selected_any_rate": round(selected / max(len(rows), 1), 6),
        "matched_expected_skill": matched,
        "matched_expected_skill_rate": round(matched / max(len(rows), 1), 6),
        "expected_skill_ids_missing_from_catalog": missing_expected_ids,
        "cases_whose_expected_skill_is_missing": missing_expected_cases,
    }


def _clustered_delta_bootstrap(
    rows: list[dict[str, Any]],
    *,
    seed: int = 20260908,
    samples: int = 5000,
) -> dict[str, Any]:
    """Bootstrap contract-level deltas instead of treating variants as independent."""

    grouped: dict[str, list[int]] = {}
    for item in rows:
        grouped.setdefault(str(item["ground_truth_root_cause"]), []).append(
            int(bool(item["skill_enabled"]["root_cause_correct"]))
            - int(bool(item["no_skill"]["root_cause_correct"]))
        )
    cluster_means = [sum(values) / len(values) for values in grouped.values()]
    if not cluster_means:
        return {
            "method": "SCENARIO_CLUSTER_PERCENTILE_BOOTSTRAP_EQUAL_CLUSTER_WEIGHT",
            "level": 0.95,
            "lower_percentage_points": None,
            "upper_percentage_points": None,
            "cluster_count": 0,
            "samples": samples,
            "seed": seed,
        }
    rng = random.Random(seed)
    cluster_count = len(cluster_means)
    estimates = sorted(
        100.0
        * sum(cluster_means[rng.randrange(cluster_count)] for _ in range(cluster_count))
        / cluster_count
        for _ in range(samples)
    )
    lower_index = max(0, int(0.025 * samples) - 1)
    upper_index = min(samples - 1, math.ceil(0.975 * samples) - 1)
    return {
        "method": "SCENARIO_CLUSTER_PERCENTILE_BOOTSTRAP_EQUAL_CLUSTER_WEIGHT",
        "level": 0.95,
        "lower_percentage_points": round(estimates[lower_index], 4),
        "upper_percentage_points": round(estimates[upper_index], 4),
        "cluster_count": cluster_count,
        "samples": samples,
        "seed": seed,
    }


def _dataset_diversity_audit(
    cases: list[dict[str, Any]],
    oracles: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Expose how many independent contracts hide behind the generated variants."""

    exact_queries = {str(item.get("query") or "") for item in cases}

    def normalized_observations(item: dict[str, Any]) -> str:
        payload = json.dumps(
            item.get("observations_by_collector") or {},
            ensure_ascii=False,
            sort_keys=True,
        )
        return re.sub(r"受控采集窗口\s+\d+\s+已完成", "受控采集窗口 N 已完成", payload)

    metadata_to_causes: dict[str, set[str]] = {}
    metadata_signatures: list[str] = []
    for item in cases:
        target = item.get("target") or {}
        signature = json.dumps(
            {
                "runtime": target.get("runtime"),
                "category": item.get("category"),
                "family_hint": item.get("family_hint"),
                "collector_capabilities": sorted(target.get("collector_capabilities") or []),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        metadata_signatures.append(signature)
        metadata_to_causes.setdefault(signature, set()).add(
            str(oracles[str(item["case_id"])]["root_cause_id"])
        )
    uniquely_identifying = {
        signature for signature, causes in metadata_to_causes.items() if len(causes) == 1
    }
    return {
        "exact_unique_query_count": len(exact_queries),
        "normalized_observation_template_count": len(
            {normalized_observations(item) for item in cases}
        ),
        "public_metadata_signature_count": len(metadata_to_causes),
        "one_to_one_metadata_signature_count": len(uniquely_identifying),
        "cases_with_uniquely_identifying_public_metadata": sum(
            signature in uniquely_identifying for signature in metadata_signatures
        ),
    }


def _paired_exact_binomial(improved: int, regressed: int) -> dict[str, Any]:
    """Two-sided exact sign test over discordant paired outcomes."""

    discordant = improved + regressed
    if discordant == 0:
        return {
            "method": "TWO_SIDED_EXACT_BINOMIAL_ON_DISCORDANT_PAIRS",
            "discordant_pairs": 0,
            "p_value": 1.0,
        }
    lower_tail = sum(
        math.comb(discordant, index)
        for index in range(min(improved, regressed) + 1)
    ) / (2**discordant)
    return {
        "method": "TWO_SIDED_EXACT_BINOMIAL_ON_DISCORDANT_PAIRS",
        "discordant_pairs": discordant,
        "p_value": min(1.0, 2.0 * lower_tail),
    }


def _paired_delta_bootstrap(
    rows: list[dict[str, Any]],
    *,
    seed: int = 20260908,
    samples: int = 5000,
) -> dict[str, Any]:
    """Deterministic percentile bootstrap for the paired accuracy delta."""

    differences = [
        int(bool(item["skill_enabled"]["root_cause_correct"]))
        - int(bool(item["no_skill"]["root_cause_correct"]))
        for item in rows
    ]
    if not differences:
        return {
            "method": "PAIRED_PERCENTILE_BOOTSTRAP",
            "level": 0.95,
            "lower_percentage_points": None,
            "upper_percentage_points": None,
            "samples": samples,
            "seed": seed,
        }
    rng = random.Random(seed)
    total = len(differences)
    estimates = sorted(
        100.0
        * sum(differences[rng.randrange(total)] for _ in range(total))
        / total
        for _ in range(samples)
    )
    lower_index = max(0, int(0.025 * samples) - 1)
    upper_index = min(samples - 1, math.ceil(0.975 * samples) - 1)
    return {
        "method": "PAIRED_PERCENTILE_BOOTSTRAP",
        "level": 0.95,
        "lower_percentage_points": round(estimates[lower_index], 4),
        "upper_percentage_points": round(estimates[upper_index], 4),
        "samples": samples,
        "seed": seed,
    }


def evaluate_controlled_root_causes(
    catalog: dict[str, Any],
    public_set: dict[str, Any],
    private_oracles: dict[str, Any],
    *,
    paired_case_count: int = 500,
    tool_budget: int = 2,
) -> dict[str, Any]:
    if public_set.get("schema") != SCHEMA or private_oracles.get("schema") != SCHEMA:
        raise ValueError("unsupported controlled root-cause benchmark schema")
    cases = list(public_set.get("cases") or [])
    oracle_rows = list(private_oracles.get("oracles") or [])
    if len(cases) != 540:
        raise ValueError("controlled root-cause benchmark requires exactly 540 cases")
    if not 1 <= paired_case_count <= len(cases):
        raise ValueError("paired_case_count must fit inside the dataset")
    oracles = {str(item["case_id"]): item for item in oracle_rows}
    if set(oracles) != {str(item.get("case_id")) for item in cases}:
        raise ValueError("public cases and private root-cause oracles do not match")

    skills = [_runtime_skill(item) for item in catalog.get("skills") or []]
    catalog_skill_ids = {str(item.id) for item in skills}
    rows: list[dict[str, Any]] = []
    runtime_counts: Counter[str] = Counter()
    scenario_counts: Counter[str] = Counter()
    for case in cases:
        oracle = oracles[str(case["case_id"])]
        baseline_tools = list(BASELINE_ROUTES.get(str(case.get("category")), ["collect_sys_metrics"]))
        selected_route, selected_skill, retrieval = _skill_route(skills, case)
        auto_tools = _dedupe(selected_route + baseline_tools)
        decisive_collector = str(oracle["decisive_collector"])

        def arm_payload(tools: list[str], *, skill_id: str | None) -> dict[str, Any]:
            collectors = _collector_route(tools)
            observations = _available_observations(case, tools, tool_budget)
            predicted, score = _predict_scenario(case, observations, selected_skill=skill_id)
            prediction_without_skill_bonus, score_without_skill_bonus = _predict_scenario(
                case,
                observations,
                selected_skill=None,
            )
            try:
                decisive_position = collectors.index(decisive_collector) + 1
            except ValueError:
                decisive_position = None
            decisive_reached = decisive_position is not None and decisive_position <= tool_budget
            return {
                "predicted_root_cause": predicted,
                "root_cause_score": score,
                "score_prior_ablation": {
                    "predicted_root_cause_without_skill_bonus": prediction_without_skill_bonus,
                    "score_without_skill_bonus": score_without_skill_bonus,
                    "prediction_changed": predicted != prediction_without_skill_bonus,
                },
                # A lucky text-only guess is not a correct diagnosis in an
                # evidence-first system.  Top-1 only passes after the arm has
                # reached the contract's decisive collector inside the same
                # fixed tool budget.
                "root_cause_correct": predicted == oracle["root_cause_id"] and decisive_reached,
                "text_prediction_matches_oracle": predicted == oracle["root_cause_id"],
                "tool_route": tools,
                "collector_route": collectors,
                "observed_collectors": collectors[:tool_budget],
                "decisive_collector": decisive_collector,
                "decisive_collector_position": decisive_position,
                "decisive_collector_reached": decisive_reached,
            }

        baseline = arm_payload(baseline_tools, skill_id=None)
        auto = arm_payload(auto_tools, skill_id=selected_skill)
        runtime = str((case.get("target") or {}).get("runtime") or "UNKNOWN")
        runtime_counts[runtime] += 1
        scenario_counts[str(oracle["root_cause_id"])] += 1
        rows.append(
            {
                "case_id": case["case_id"],
                "variant_family": case.get("variant_family"),
                "runtime": runtime,
                "ground_truth_root_cause": oracle["root_cause_id"],
                "expected_skill_id": oracle.get("expected_skill_id"),
                "skill_retrieval": {
                    "selected_skill_id": selected_skill,
                    "matched_expected": selected_skill == oracle.get("expected_skill_id"),
                    "detail": retrieval,
                },
                "no_skill": baseline,
                "skill_enabled": auto,
            }
        )

    root_baseline = _arm_metrics(rows, "no_skill")
    root_auto = _arm_metrics(rows, "skill_enabled")
    paired_rows = rows[:paired_case_count]
    paired_baseline = _arm_metrics(paired_rows, "no_skill")
    paired_auto = _arm_metrics(paired_rows, "skill_enabled")
    full_retrieval = _skill_retrieval_metrics(
        rows,
        catalog_skill_ids=catalog_skill_ids,
    )
    paired_retrieval = _skill_retrieval_metrics(
        paired_rows,
        catalog_skill_ids=catalog_skill_ids,
    )

    def score_prior_ablation(items: list[dict[str, Any]]) -> dict[str, int]:
        changed_predictions = 0
        changed_composite_outcomes = 0
        for item in items:
            arm = item["skill_enabled"]
            ablated_prediction = arm["score_prior_ablation"][
                "predicted_root_cause_without_skill_bonus"
            ]
            if arm["predicted_root_cause"] != ablated_prediction:
                changed_predictions += 1
            ablated_correct = (
                ablated_prediction == item["ground_truth_root_cause"]
                and bool(arm["decisive_collector_reached"])
            )
            if bool(arm["root_cause_correct"]) != ablated_correct:
                changed_composite_outcomes += 1
        return {
            "prediction_changed_cases": changed_predictions,
            "composite_outcome_changed_cases": changed_composite_outcomes,
        }
    improved = sum(
        not item["no_skill"]["root_cause_correct"] and item["skill_enabled"]["root_cause_correct"]
        for item in paired_rows
    )
    regressed = sum(
        item["no_skill"]["root_cause_correct"] and not item["skill_enabled"]["root_cause_correct"]
        for item in paired_rows
    )
    route_improved = sum(
        (
            item["skill_enabled"]["decisive_collector_position"] is not None
            and (
                item["no_skill"]["decisive_collector_position"] is None
                or item["skill_enabled"]["decisive_collector_position"]
                < item["no_skill"]["decisive_collector_position"]
            )
        )
        for item in paired_rows
    )
    paired_by_runtime = {
        runtime: {
            "case_count": len(runtime_rows),
            "no_skill": _arm_metrics(runtime_rows, "no_skill"),
            "skill_enabled": _arm_metrics(runtime_rows, "skill_enabled"),
        }
        for runtime in sorted({str(item["runtime"]) for item in paired_rows})
        if (
            runtime_rows := [
                item for item in paired_rows if str(item["runtime"]) == runtime
            ]
        )
    }
    return {
        "schema": REPORT_SCHEMA,
        "benchmark_kind": "CONTROLLED_FAULT_CONTRACT_REPLAY",
        "dataset": {
            "version": public_set.get("version"),
            "case_count": len(cases),
            "paired_ab_case_count": paired_case_count,
            "executable_fault_contract_count": len(SCENARIOS),
            "runtime_counts": dict(sorted(runtime_counts.items())),
            "scenario_counts": dict(sorted(scenario_counts.items())),
            "public_sha256": canonical_sha256(public_set),
            "private_oracle_sha256": canonical_sha256(private_oracles),
            "combined_sha256": canonical_sha256({"public": public_set, "oracle": private_oracles}),
            "generator_seed": public_set.get("generator_seed"),
            "diversity_audit": _dataset_diversity_audit(cases, oracles),
        },
        "root_cause_evaluation_540": {
            "metric": "controlled_closed_set_decisive_route_qualified_proxy_top1",
            "display_name_zh": "受控闭集两次工具预算内证据路线达标率（代理 Top-1）",
            "no_skill": root_baseline,
            "skill_enabled": root_auto,
            "delta_percentage_points": round(
                (root_auto["root_cause_top1_accuracy"] - root_baseline["root_cause_top1_accuracy"]) * 100,
                4,
            ),
            "skill_retrieval": full_retrieval,
            "skill_score_prior_ablation": score_prior_ablation(rows),
        },
        "paired_skill_ab_500": {
            "case_count": paired_case_count,
            "same_cases_and_tool_budget": True,
            "tool_budget_per_arm": tool_budget,
            "no_skill": paired_baseline,
            "skill_enabled": paired_auto,
            "delta_percentage_points": round(
                (paired_auto["root_cause_top1_accuracy"] - paired_baseline["root_cause_top1_accuracy"]) * 100,
                4,
            ),
            "improved_cases": improved,
            "regressed_cases": regressed,
            "unchanged_cases": paired_case_count - improved - regressed,
            "decisive_route_improved_cases": route_improved,
            "paired_significance": _paired_exact_binomial(improved, regressed),
            "delta_bootstrap_95": _paired_delta_bootstrap(paired_rows),
            "scenario_cluster_delta_bootstrap_95": _clustered_delta_bootstrap(
                paired_rows
            ),
            "skill_retrieval": paired_retrieval,
            "skill_score_prior_ablation": score_prior_ablation(paired_rows),
            "by_runtime": paired_by_runtime,
        },
        "measurement_boundary": {
            "truth_source": "21_EXECUTABLE_ALLOWLISTED_FAULT_PLAZA_CONTRACTS",
            "case_variants": "540_DETERMINISTIC_CONTROLLED_REPLAYS",
            "paired_ab": "500_SAME_CASE_SAME_TOOL_BUDGET_PAIRS",
            "root_cause_metric": "CONTROLLED_CLOSED_SET_DECISIVE_ROUTE_QUALIFIED_PROXY_TOP1",
            "evidence_gate": "DECISIVE_COLLECTOR_ROUTE_PROXY_NOT_PRODUCTION_EVIDENCE_ENVELOPE",
            "live_linux_execution": "REPRESENTATIVE_CAMPAIGN_ONLY_NOT_540_LIVE_INJECTIONS",
            "claim": (
                "该报告测量 21 个可执行白名单故障合同衍生的 540 条受控回放，以及其中 500 条同题同预算 Skill A/B。"
                "它不是 540 次公网 Linux 真机注入；真机 Collector、Artifact、Evidence 与 Report 结论必须由独立 live 报告证明。"
            ),
        },
        "cases": rows,
    }


def report_sha256(report: dict[str, Any]) -> str:
    unsigned = dict(report)
    unsigned.pop("report_sha256", None)
    encoded = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
