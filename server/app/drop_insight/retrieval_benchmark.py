"""Deterministic comparison for Skill retrieval strategies."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.app.models import DiagnosticSkillModel

from .skill_evolution import (
    _MATCH_THRESHOLD,
    _match_score,
    _rank_hybrid_skills,
    _select_ranked_skill,
)


def load_benchmark(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _skill(item: dict[str, Any]) -> DiagnosticSkillModel:
    timestamp = datetime(2026, 8, 29, tzinfo=timezone.utc)
    return DiagnosticSkillModel(
        id=item["id"],
        family_key=item.get("family_key") or item["id"],
        category=item["category"],
        version=1,
        status="ACTIVE",
        source_diagnosis_ids_json=[],
        trigger_json={
            "environment": item["environment"],
            "service": item["service"],
            "source_query": item["text"],
        },
        strategy_json={"probe_order": item["probe_order"]},
        gate_metrics_json={"eligible": True},
        created_by="benchmark-fixture",
        created_at=timestamp,
        updated_at=timestamp,
        published_at=timestamp,
    )


def _component_choice(
    ranked: list[tuple[float, dict, DiagnosticSkillModel]],
    component: str,
    *,
    threshold: float,
) -> str | None:
    candidates = sorted(
        ((float(reason.get(component) or 0), skill.id) for _, reason, skill in ranked),
        key=lambda item: (-item[0], item[1]),
    )
    if not candidates or candidates[0][0] < threshold:
        return None
    if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < 0.03:
        return None
    return candidates[0][1]


def _legacy_choice(
    skills: list[DiagnosticSkillModel], category: str, target: dict[str, Any]
) -> tuple[str | None, bool]:
    ranked = sorted(
        (
            (_match_score(skill, category, target)[0], index, skill.id)
            for index, skill in enumerate(skills)
        ),
        key=lambda item: (-item[0], item[1]),
    )
    eligible = [item for item in ranked if item[0] >= _MATCH_THRESHOLD]
    if not eligible:
        return None, False
    tied = len(eligible) > 1 and eligible[0][0] == eligible[1][0]
    return eligible[0][2], tied


def run_benchmark(catalog: dict[str, Any]) -> dict[str, Any]:
    """Compare old structured matching, BM25, vectors and production hybrid."""
    skills = [_skill(item) for item in catalog["skills"]]
    methods = ("legacy_structured", "bm25", "feature_vector", "hybrid")
    totals = {
        name: {"correct": 0, "positive_correct": 0, "negative_correct": 0}
        for name in methods
    }
    details: list[dict[str, Any]] = []
    legacy_ties = 0
    positive_total = 0
    negative_total = 0

    for case in catalog["cases"]:
        target = {**case["target"], "_baseline_tool": "collect_sys_metrics"}
        expected = case.get("expected_skill_id")
        if expected is None:
            negative_total += 1
        else:
            positive_total += 1
        ranked = _rank_hybrid_skills(skills, case["category"], target, case["query"])
        legacy, tied = _legacy_choice(skills, case["category"], target)
        legacy_ties += int(tied)
        hybrid = _select_ranked_skill(ranked)
        predictions = {
            "legacy_structured": legacy,
            "bm25": _component_choice(ranked, "bm25", threshold=0.18),
            "feature_vector": _component_choice(ranked, "vector", threshold=0.18),
            "hybrid": hybrid[2].id if hybrid else None,
        }
        for method, prediction in predictions.items():
            correct = prediction == expected
            totals[method]["correct"] += int(correct)
            if expected is None:
                totals[method]["negative_correct"] += int(correct)
            else:
                totals[method]["positive_correct"] += int(correct)
        details.append(
            {
                "case_id": case["id"],
                "query": case["query"],
                "expected_skill_id": expected,
                "predictions": predictions,
                "hybrid_ranking": [
                    {
                        "skill_id": skill.id,
                        "score": round(score, 4),
                        "bm25": reason.get("bm25"),
                        "vector": reason.get("vector"),
                        "structured": reason.get("structured"),
                    }
                    for score, reason, skill in ranked[:3]
                ],
            }
        )

    case_count = len(catalog["cases"])
    metrics = {}
    for method, values in totals.items():
        metrics[method] = {
            "accuracy": round(values["correct"] / max(case_count, 1), 4),
            "positive_recall_at_1": round(
                values["positive_correct"] / max(positive_total, 1), 4
            ),
            "negative_rejection_rate": round(
                values["negative_correct"] / max(negative_total, 1), 4
            ),
            "correct": values["correct"],
            "total": case_count,
        }
    metrics["legacy_structured"]["top_score_tie_cases"] = legacy_ties
    return {
        "benchmark": catalog.get("name", "skill-retrieval"),
        "dataset_type": catalog.get("dataset_type", "frozen_regression_fixture"),
        "case_count": case_count,
        "positive_cases": positive_total,
        "negative_cases": negative_total,
        "vector_backend": "HASHED_NGRAM_CONCEPT_VECTOR",
        "metrics": metrics,
        "cases": details,
    }
