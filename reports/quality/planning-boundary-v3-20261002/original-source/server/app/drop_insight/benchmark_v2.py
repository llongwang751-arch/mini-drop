"""Deterministic, blinded benchmark for the production Skill selector.

The dataset is generated from a versioned taxonomy rather than copied from the
retired benchmark fixtures.  Public prompts and private oracles are stored in
separate files.  This evaluator imports the same hybrid retrieval and safety
gates used by ``apply_active_skill``; it does not replace live Linux campaign
evidence and never labels a route match as a verified root cause.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.app.models import DiagnosticSkillModel

from .skill_evolution import _rank_hybrid_skills, _select_ranked_skill


SCHEMA = "mini-drop.diagnosis-benchmark.v2"
REPORT_SCHEMA = "mini-drop.skill-reuse-report.v2"


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _runtime_skill(item: dict[str, Any]) -> DiagnosticSkillModel:
    """Project a repository catalog entry into the production ranking model."""

    timestamp = datetime(2026, 9, 5, tzinfo=timezone.utc)
    slug = str(item["slug"])
    return DiagnosticSkillModel(
        id=slug,
        family_key=f"repository:{slug}",
        category=str(item["category"]),
        version=1,
        status="ACTIVE",
        source_diagnosis_ids_json=[],
        trigger_json={
            "environment": "*",
            "service": "",
            "source_query": " ".join(str(v) for v in item.get("query_terms") or []),
            "query_terms": list(item.get("query_terms") or []),
            "required_query_terms_any": list(item.get("required_query_terms_any") or []),
            "repository_builtin": True,
        },
        strategy_json={"probe_order": list(item["probe_order"])},
        gate_metrics_json={"eligible": True},
        created_by="benchmark-v2",
        created_at=timestamp,
        updated_at=timestamp,
        published_at=timestamp,
    )


def evaluate_skill_reuse(
    catalog: dict[str, Any],
    public_set: dict[str, Any],
    private_oracles: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate the real selector against hidden labels.

    The no-Skill baseline always abstains from route reuse.  It is deliberately
    conservative: negative cases pass, while positive reuse cases do not.  The
    treatment arm invokes the production hybrid ranker and ambiguity gate.
    """

    if public_set.get("schema") != SCHEMA or private_oracles.get("schema") != SCHEMA:
        raise ValueError("unsupported diagnosis benchmark schema")
    public_cases = list(public_set.get("cases") or [])
    oracle_rows = list(private_oracles.get("oracles") or [])
    if len(public_cases) < 300:
        raise ValueError("benchmark v2 requires at least 300 cases")
    oracles = {str(row["case_id"]): row for row in oracle_rows}
    if set(oracles) != {str(row.get("case_id")) for row in public_cases}:
        raise ValueError("public cases and private oracles do not match")

    skills = [_runtime_skill(item) for item in catalog.get("skills") or []]
    rows: list[dict[str, Any]] = []
    family_stats: dict[str, dict[str, int]] = {}
    baseline_correct = 0
    enabled_correct = 0
    false_activations = 0
    positive_total = 0
    positive_correct = 0
    negative_total = 0
    negative_correct = 0

    for case in public_cases:
        case_id = str(case["case_id"])
        oracle = oracles[case_id]
        expected = oracle.get("expected_skill_id")
        target = {**dict(case.get("target") or {}), "_baseline_tool": "collect_sys_metrics"}
        ranked = _rank_hybrid_skills(
            skills, str(case["category"]), target, str(case["query"])
        )
        selected = _select_ranked_skill(ranked)
        predicted = selected[2].id if selected else None
        baseline_prediction = None
        baseline_ok = baseline_prediction == expected
        enabled_ok = predicted == expected
        baseline_correct += int(baseline_ok)
        enabled_correct += int(enabled_ok)
        family = str(case["case_family"])
        stats = family_stats.setdefault(
            family,
            {"total": 0, "baseline_correct": 0, "skill_correct": 0},
        )
        stats["total"] += 1
        stats["baseline_correct"] += int(baseline_ok)
        stats["skill_correct"] += int(enabled_ok)
        if expected is None:
            negative_total += 1
            negative_correct += int(predicted is None)
            false_activations += int(predicted is not None)
        else:
            positive_total += 1
            positive_correct += int(predicted == expected)
        rows.append(
            {
                "case_id": case_id,
                "case_family": family,
                "expected_skill_id": expected,
                "baseline_prediction": baseline_prediction,
                "skill_prediction": predicted,
                "passed": enabled_ok,
                "top_score": round(float(ranked[0][0]), 6) if ranked else None,
                "top_reason": ranked[0][1] if ranked else None,
            }
        )

    total = len(public_cases)
    dataset_unsigned = {
        "public": public_set,
        "private_oracle_digest": canonical_sha256(private_oracles),
    }
    return {
        "schema": REPORT_SCHEMA,
        "benchmark_kind": "SYNTHETIC_BLINDED_ROUTE_SELECTION",
        "dataset": {
            "version": public_set.get("version"),
            "case_count": total,
            "public_sha256": canonical_sha256(public_set),
            "private_oracle_sha256": canonical_sha256(private_oracles),
            "combined_sha256": canonical_sha256(dataset_unsigned),
            "generator_seed": public_set.get("generator_seed"),
            "training_overlap": "NEW_PROMPTS_WITH_CATALOG_DERIVED_TAXONOMY",
        },
        "baseline_no_skill": {
            "correct": baseline_correct,
            "total": total,
            "accuracy": round(baseline_correct / total, 6),
            "policy": "ABSTAIN_FROM_ROUTE_REUSE",
        },
        "skill_enabled": {
            "correct": enabled_correct,
            "total": total,
            "accuracy": round(enabled_correct / total, 6),
            "positive_correct": positive_correct,
            "positive_total": positive_total,
            "positive_reuse_rate": round(positive_correct / max(positive_total, 1), 6),
            "negative_correct": negative_correct,
            "negative_total": negative_total,
            "negative_rejection_rate": round(negative_correct / max(negative_total, 1), 6),
            "false_activations": false_activations,
            "false_activation_rate": round(false_activations / max(negative_total, 1), 6),
        },
        "family_results": {
            name: {
                **stats,
                "baseline_accuracy": round(stats["baseline_correct"] / stats["total"], 6),
                "skill_accuracy": round(stats["skill_correct"] / stats["total"], 6),
            }
            for name, stats in sorted(family_stats.items())
        },
        "measurement_boundary": {
            "skill_selection_and_safety_gate": "MEASURED",
            "dynamic_tree_execution": "COVERED_BY_PRODUCT_INTEGRATION_TESTS",
            "evidence_verified_root_cause_accuracy": "REQUIRES_LIVE_LINUX_CAMPAIGN",
            "production_diagnosis_duration": "REQUIRES_LIVE_LINUX_CAMPAIGN",
            "claim": (
                "This report measures route-memory selection and rejection only. "
                "A matching Skill is not incident evidence and is never scored as a root cause."
            ),
        },
        "cases": rows,
    }
