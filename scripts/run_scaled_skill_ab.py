#!/usr/bin/env python3
"""Run the large deterministic Skill A/B, calibration, and stability benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from server.app.drop_insight.retrieval_benchmark import load_benchmark
from server.app.drop_insight.scaled_skill_ab import (
    calibrate_retrieval_gates,
    evaluate_scaled_ab,
    expand_catalog,
    run_retrieval_stability,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _render(report: dict) -> str:
    paired = report["paired_ab"]
    baseline = paired["baseline_no_skill"]
    skill = paired["skill_enabled"]
    delta = paired["delta"]
    shadow = paired["shadow_ab"]
    calibration = report["calibration"]
    stability = report["stability"]
    return "\n".join(
        [
            "# Mini-Drop 大规模 Skill A/B 评测",
            "",
            f"- 生成时间：`{report['generated_at']}`",
            f"- 随机种子：`{report['generation']['seed']}`",
            f"- 总 Case：`{paired['dataset']['case_count']}`",
            f"- 变体构成：`{json.dumps(paired['dataset']['family_counts'], ensure_ascii=False)}`",
            "",
            "## 成对离线对照",
            "",
            f"- 无 Skill 静态规则先验：`{baseline['correct']}/{baseline['total']}`，准确率 `{baseline['accuracy']:.4%}`。",
            f"- 启用 Skill 混合检索：`{skill['correct']}/{skill['total']}`，准确率 `{skill['accuracy']:.4%}`。",
            f"- 增量：`{delta['accuracy_percentage_points']:+.4f}` 个百分点。",
            f"- 只看正例的根因路线 Top-1：无 Skill `{baseline['positive_root_cause_prior_accuracy']:.4%}`，Skill `{skill['positive_root_cause_prior_accuracy']:.4%}`，增加 `{delta['positive_accuracy_percentage_points']:+.4f}` 个百分点。",
            f"- 改善 `{delta['improved']}`，退化 `{delta['regressed']}`，不变 `{delta['unchanged']}`。",
            f"- Skill 负例拒绝率：`{skill['negative_rejection_rate']:.4%}`；误激活率 `{skill['false_activation_rate']:.4%}`。",
            "",
            "## 固定分桶 Shadow A/B",
            "",
            f"- A 组无 Skill：`n={shadow['no_skill']['total']}`，准确率 `{shadow['no_skill']['accuracy']:.4%}`。",
            f"- B 组启用 Skill：`n={shadow['skill_enabled']['total']}`，准确率 `{shadow['skill_enabled']['accuracy']:.4%}`。",
            "- 分桶由 diagnosis_id 的 SHA256 决定，同一个诊断始终进入同一组。",
            "",
            "## 门槛校准",
            "",
            f"- 当前配置：阈值 `{calibration['current_production_gates']['match_threshold']}`，歧义差值 `{calibration['current_production_gates']['ambiguity_margin']}`。",
            f"- 网格搜索候选：阈值 `{calibration['selected']['match_threshold']}`，歧义差值 `{calibration['selected']['ambiguity_margin']}`。",
            "- 生成集只能发现明显不安全的配置，最终生产阈值必须使用真实反馈校准。",
            "",
            "## 稳定性",
            "",
            f"- 范围：`{stability['scope']}`",
            f"- 持续：`{stability['duration_seconds']:.2f}s`，迭代 `{stability['iterations']}`，决策 `{stability['decisions']}`。",
            f"- 错误：`{stability['errors']}`，结果漂移：`{stability['decision_drift_iterations']}`，通过：`{stability['passed']}`。",
            f"- 单轮 p95：`{stability['latency_ms_per_iteration']['p95']:.2f} ms`。",
            "",
            "## 结论边界",
            "",
            "- 本报告测的是症状到根因路线先验的选择准确率，以及环境/能力漂移时的拒绝能力。",
            "- 它不等于 Linux 遥测证据验证后的真实根因准确率。",
            "- 真正生产 A/B 还需要用报告 VERIFIED/人工反馈作为结果标签，并持续观察误用、耗时和成本。",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "skill_retrieval_benchmark.json",
    )
    parser.add_argument("--positive-variants", type=int, default=60)
    parser.add_argument("--negative-variants", type=int, default=60)
    parser.add_argument("--drift-variants", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260902)
    parser.add_argument("--stability-seconds", type=float, default=0)
    parser.add_argument("--stability-iterations", type=int, default=5)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "skill-ab-large" / "report.json",
    )
    args = parser.parse_args()

    catalog_path = args.catalog.resolve()
    catalog = load_benchmark(catalog_path)
    cases = expand_catalog(
        catalog,
        positive_variants=args.positive_variants,
        negative_variants=args.negative_variants,
        drift_variants=args.drift_variants,
        seed=args.seed,
    )
    paired = evaluate_scaled_ab(catalog, cases)
    calibration = calibrate_retrieval_gates(catalog, cases)
    stability = run_retrieval_stability(
        catalog,
        cases,
        duration_seconds=args.stability_seconds,
        minimum_iterations=args.stability_iterations,
    )
    report = {
        "schema": "mini-drop.scaled-skill-ab.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generation": {
            "seed": args.seed,
            "positive_variants": args.positive_variants,
            "negative_variants": args.negative_variants,
            "drift_variants": args.drift_variants,
        },
        "provenance": {
            "catalog_path": str(catalog_path.relative_to(ROOT)),
            "catalog_sha256": _sha256(catalog_path),
            "runner_sha256": _sha256(Path(__file__).resolve()),
        },
        "paired_ab": paired,
        "calibration": calibration,
        "stability": stability,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown = output.with_suffix(".md")
    markdown.write_text(_render(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "case_count": paired["dataset"]["case_count"],
                "baseline_accuracy": paired["baseline_no_skill"]["accuracy"],
                "skill_accuracy": paired["skill_enabled"]["accuracy"],
                "delta_percentage_points": paired["delta"]["accuracy_percentage_points"],
                "skill_false_activation_rate": paired["skill_enabled"]["false_activation_rate"],
                "calibrated_gates": calibration["selected"],
                "stability": stability,
                "report": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if stability["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
