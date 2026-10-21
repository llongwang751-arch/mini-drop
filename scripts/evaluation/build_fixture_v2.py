"""追加事实协议评测情境，复用原案例不变的独立经验种子；不改写v1固定集。"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    base = Path(__file__).resolve().parent
    root = base.parents[1]
    sys.path.insert(0, str(root / "analyzer"))
    from drop_analyzer.folded import analyze_folded
    from drop_analyzer.result import make_result

    original = json.loads((base / "diagnostic_cases.json").read_text(encoding="utf-8"))
    cases = [
        case
        for case in original["cases"]
        if case["id"] in {"seed-cpu-folded-dev", "seed-allocation-folded-dev"}
    ]
    rows = [
        (
            "live-normal-load-v2-holdout",
            "正常高负载不能仅凭热点判缺陷",
            "normal_load",
            "fixture;fixture.expected_batch 900\nfixture;fixture.dispatch 100\n",
            ["cpu_hotspot", "sampling_quality"],
            ["normal_load", "insufficient_evidence"],
            False,
            True,
        ),
        (
            "live-insufficient-v2-holdout",
            "低权重且现场元数据未知，必须保留不足结论",
            "insufficient_evidence",
            "fixture;fixture.work 2\nfixture;fixture.dispatch 1\n",
            ["cpu_hotspot", "sampling_quality"],
            ["insufficient_evidence"],
            True,
            False,
        ),
        (
            "live-compound-v2-holdout",
            "分配与等待复合线索需要分别核验",
            "mixed_signals",
            "fixture;malloc 500\nfixture;futex_wait 300\nfixture;fixture.work 200\n",
            ["allocation_gc", "lock_io", "sampling_quality"],
            ["mixed_signals", "allocation_gc", "lock_io", "insufficient_evidence"],
            False,
            False,
        ),
        (
            "live-attribution-control-v2-holdout",
            "无分配提示的错误归因负控",
            "normal_load",
            "fixture;fixture.calculate_totals 650\nfixture;fixture.dispatch 350\n",
            ["cpu_hotspot", "sampling_quality"],
            ["normal_load", "insufficient_evidence"],
            False,
            True,
        ),
    ]
    for case_id, title, family, folded, skills, categories, abstain, normal in rows:
        result = make_result(
            analyze_folded(folded.encode()), "IMPORT_COLLAPSED", {"source": "import"}
        )
        profile = {
            key: result[key]
            for key in (
                "kind",
                "total_samples",
                "node_count",
                "top_functions",
                "flamegraph",
            )
        }
        profile["summary"] = {
            "unit": result["summary"]["unit"],
            "source": result["summary"]["source"],
        }
        cases.append(
            {
                "id": case_id,
                "title": title,
                "split": "holdout",
                "family": family,
                "provenance": {
                    "source": "imported_synthetic",
                    "imported": True,
                    "synthetic": True,
                    "production_data": False,
                    "scope": {
                        "project": "evaluation-fixture",
                        "runtime": "offline-fixed-profile",
                        "source_revision": "a" * 64,
                    },
                },
                "task": {
                    "kind": "IMPORT_COLLAPSED",
                    "parameters": {"source": "import"},
                },
                "profile": profile,
                "scenario_context": "作者设定的合成负控/线索情境，不是现场事实，不可将ground_truth标签传入模型问题。",
                "prior_candidates": [],
                "ground_truth": {
                    "skill_ids": sorted(skills),
                    "matcher_rejects": False,
                    "accepted_categories": categories,
                    "abstention_required": abstain,
                    "normal_load": normal,
                    "causal_root_proven": False,
                    "label_source": "synthetic_scenario_author",
                },
                "reproduction": {
                    "method": "real_folded_analyzer_semantic_projection_v1",
                    "collapsed_text": folded,
                    "projection_fields": [
                        "kind",
                        "total_samples",
                        "node_count",
                        "top_functions",
                        "flamegraph",
                        "summary.unit",
                        "summary.source",
                    ],
                    "excluded_fields": [
                        "metadata",
                        "suggestions",
                        "summary.duration_seconds",
                        "summary.frequency_hz",
                    ],
                },
                "fact_protocol_controls": {
                    "report_protocol": 2,
                    "positive_self_items": sum(
                        row["self_percent"] > 0 for row in profile["top_functions"]
                    ),
                    "manual_import_has_collection_parameters": False,
                    "forbidden_tool_plan_parameters": {
                        "duration_seconds": 60,
                        "frequency_hz": 99,
                    },
                    "allocation_hypothesis_without_allocation_fact_rejected": family
                    == "normal_load",
                },
            }
        )
    dataset = {
        "schema_version": 1,
        "dataset_id": "drop-diagnostic-facts-v2",
        "description": "4个新增合成情境与2个未改动dev经验种子；v1案例和历史评测收据保持不变。",
        "holdout_policy": "作者可见的预留回归分区，不是独立盲测；holdout不用于播种经验。",
        "cases": cases,
    }
    (base / "diagnostic_cases_v2.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
