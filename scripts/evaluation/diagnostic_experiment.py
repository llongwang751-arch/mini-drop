"""预登记固定重复试验并录入独立人工审阅；不联网、不调用模型、不生成语义自评分。"""

from __future__ import annotations

import argparse
import json
import random
import sys
from copy import deepcopy
from pathlib import Path

import evaluate_diagnostics as evaluation


def build_plan(
    cases: dict,
    repetitions: int,
    seed: int,
    on_mode: str,
    case_ids: list[str] | None = None,
) -> dict:
    if (
        type(repetitions) is not int
        or not 1 <= repetitions <= 30
        or on_mode not in {"skills", "experience"}
    ):
        raise evaluation.EvaluationError("重复次数须为1至30，on模式须明确")
    selected = case_ids or [
        key
        for key, case in cases.items()
        if case["split"] == "holdout" and "reproduction" in case
    ]
    if not selected or len(set(selected)) != len(selected):
        raise evaluation.EvaluationError("预登记case集合为空或重复")
    if any(
        key not in cases
        or cases[key]["split"] != "holdout"
        or "reproduction" not in cases[key]
        for key in selected
    ):
        raise evaluation.EvaluationError("只能预登记明确可复现的holdout案例")
    generator = random.Random(seed)
    blocks = [(key, index) for key in selected for index in range(1, repetitions + 1)]
    generator.shuffle(blocks)
    trials = []
    for case_id, index in blocks:
        modes = ["off", on_mode]
        generator.shuffle(modes)
        for mode in modes:
            trial = {
                "case_id": case_id,
                "replicate_id": str(index),
                "knowledge_mode": mode,
                "case_sha256": evaluation.canonical_sha256(cases[case_id]),
                "report_protocol": 2,
            }
            trial["trial_id"] = evaluation.canonical_sha256(trial)[:24]
            trials.append(trial)
    return {
        "schema_version": 1,
        "kind": "preregistered_diagnostic_experiment",
        "mode": "plan_only_no_network",
        "experiment": {
            "case_ids": selected,
            "repetitions": repetitions,
            "report_protocol": 2,
            "on_mode": on_mode,
            "seed": seed,
            "ordering": "seeded_random_blocks_with_randomized_pair_order",
        },
        "case_manifest": evaluation.manifest(cases),
        "trials": trials,
        "capture_requirements": [
            "每个trial必须独立调用授权测试任务上的真实诊断API；不能复用run_id或丢弃失败结果。",
            "同一case固定问题、输入与分析轮次；off/on成对保持同一任务和来源身份。",
            "固定模型配置与经验种子，不把ground_truth或holdout答案注入问题/经验。",
            "采集结果带experiment和每行replicate_id；实际阶段或环境偏差须另行记录。",
        ],
        "limits": [
            "计划只决定待执行顺序，不证明试验已经运行。",
            "同一合成情境的多次模型运行不等于新增独立业务故障样本。",
            "语义效果仍需独立人工审阅；缺失时保持null。",
        ],
    }


def report_object(row: dict) -> dict:
    return evaluation.parse_embedded(row["run"].get("report") or {}, dict, "report")


def review_template(payload: dict, cases: dict, on_mode: str) -> dict:
    evaluation.evaluate_live(payload, cases, on_mode)
    rows = []
    for row in payload["runs"]:
        run = row["run"]
        if run["status"] != "success":
            continue
        rows.append(
            {
                "run_id": str(run["id"]),
                "case_id": row["case_id"],
                "replicate_id": str(row.get("replicate_id", "1")),
                "report_sha256": evaluation.canonical_sha256(report_object(row)),
                "analysis_sha256": evaluation.canonical_sha256(
                    evaluation.result_projection(
                        evaluation.parse_embedded(
                            row["analysis_result"], dict, "analysis_result"
                        )
                    )
                ),
                "assessment_type": "human_manual_review",
                "reviewer": None,
                "independence_attested": False,
                "blind_to_knowledge_mode": False,
                "category": None,
                "abstained": None,
                "actionable_defect": None,
            }
        )
    return {
        "schema_version": 1,
        "kind": "human_review_input",
        "reviews": rows,
        "instructions": [
            "由独立人类审阅实际报告及对应分析结果后填写；不是Agent自评。",
            "未审阅行保留null。确认独立性后填reviewer并将independence_attested改true。",
            "category为线索类别，不代表根因已证实；abstained指拒绝证据不足的确定结论。",
            "只有真正遮蔽知识模式时才填写blind_to_knowledge_mode=true；模板省略该模式并不保证盲评。",
            "agent_review不能导入为manual_review；本工具也不能验证填写者真实身份。",
        ],
    }


def apply_reviews(payload: dict, reviews: dict, cases: dict, on_mode: str) -> dict:
    evaluation.evaluate_live(payload, cases, on_mode)
    if (
        not isinstance(reviews, dict)
        or reviews.get("schema_version") != 1
        or reviews.get("kind") != "human_review_input"
        or not isinstance(reviews.get("reviews"), list)
    ):
        raise evaluation.EvaluationError(
            "必须输入独立human_review_input，不能将agent_review冒充人工标注"
        )
    copied = deepcopy(payload)
    indexed = {str(row["run"]["id"]): row for row in copied["runs"]}
    seen = set()
    for review in reviews["reviews"]:
        if not isinstance(review, dict):
            raise evaluation.EvaluationError("人工审阅行须为对象")
        run_id = review.get("run_id")
        if not isinstance(run_id, str) or run_id not in indexed or run_id in seen:
            raise evaluation.EvaluationError("人工审阅run_id未知或重复")
        seen.add(run_id)
        row = indexed[run_id]
        if review.get("case_id") != row["case_id"] or str(
            review.get("replicate_id")
        ) != str(row.get("replicate_id", "1")):
            raise evaluation.EvaluationError("人工审阅与case/重复试验不一致")
        if review.get("report_sha256") != evaluation.canonical_sha256(
            report_object(row)
        ):
            raise evaluation.EvaluationError("人工审阅绑定的报告已改变")
        actual_analysis = evaluation.result_projection(
            evaluation.parse_embedded(row["analysis_result"], dict, "analysis_result")
        )
        if review.get("analysis_sha256") != evaluation.canonical_sha256(
            actual_analysis
        ):
            raise evaluation.EvaluationError("人工审阅绑定的分析结果已改变")
        if all(
            review.get(key) is None
            for key in ("category", "abstained", "actionable_defect")
        ):
            continue
        if (
            review.get("assessment_type") != "human_manual_review"
            or review.get("independence_attested") is not True
            or not isinstance(review.get("reviewer"), str)
            or not 1 <= len(review["reviewer"].strip()) <= 120
            or type(review.get("blind_to_knowledge_mode")) is not bool
            or review.get("category") not in evaluation.CATEGORIES
            or type(review.get("abstained")) is not bool
            or type(review.get("actionable_defect")) is not bool
            or row["run"]["status"] != "success"
        ):
            raise evaluation.EvaluationError(
                "人工语义标注缺少独立性声明、审阅者或完整有效结论"
            )
        if row.get("assessment") is not None:
            raise evaluation.EvaluationError("原结果已有人工审阅，禁止静默覆盖")
        row["assessment"] = {
            "method": "manual_review",
            "source": "human_manual_review",
            "reviewer": review["reviewer"],
            "independence_attested": True,
            "blind_to_knowledge_mode": review["blind_to_knowledge_mode"],
            "report_sha256": review["report_sha256"],
            "category": review["category"],
            "abstained": review["abstained"],
            "actionable_defect": review["actionable_defect"],
        }
    evaluation.evaluate_live(copied, cases, on_mode)
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "review-template", "apply-reviews"))
    parser.add_argument("--cases", type=Path, default=evaluation.FACT_CASES)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--on-mode", choices=("skills", "experience"), default="experience"
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261022)
    parser.add_argument("--case", action="append", dest="case_ids")
    args = parser.parse_args()
    try:
        cases = evaluation.load_cases(args.cases)
        if args.output.exists():
            raise evaluation.EvaluationError("输出路径已存在，请选择新路径以保留原收据")
        if args.action == "plan":
            result = build_plan(
                cases, args.repetitions, args.seed, args.on_mode, args.case_ids
            )
        else:
            if args.input is None:
                raise evaluation.EvaluationError("审阅操作需要--input实际成对结果")
            payload = evaluation.load_json(args.input)
            if args.action == "review-template":
                result = review_template(payload, cases, args.on_mode)
            else:
                if args.reviews is None:
                    raise evaluation.EvaluationError(
                        "录入需要--reviews独立人工审阅文件"
                    )
                result = apply_reviews(
                    payload, evaluation.load_json(args.reviews), cases, args.on_mode
                )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {"action": args.action, "output": str(args.output)}, ensure_ascii=False
            )
        )
        return 0
    except (evaluation.EvaluationError, OSError, json.JSONDecodeError) as error:
        print(f"评测准备未完成：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
