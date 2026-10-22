"""JD补强评测：固定输入、严格分开的作者oracle、真实DiagnosisRun与人工评分边界。

复用既有评测器的摘要、JSON类型校验、引用统计与比例算法，不另造“模型自评准确率”。
输出只含计数、结构枚举、身份与摘要；报告解释、原始性能文件和凭据不会写入公开评分。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/evaluation"))
import evaluate_diagnostics as evaluation  # noqa: E402
sys.path.insert(0, str(ROOT / "analyzer"))
from drop_analyzer.folded import analyze_folded  # noqa: E402
from drop_analyzer.result import make_result  # noqa: E402

DATA = Path(__file__).with_name("dataset")
KINDS = evaluation.CATEGORIES - {"invalid_evidence"}
TOOLS = evaluation.CURRENT_TOOLS | {
    "propose_verification", "propose_followup_collection", "read_diagnostic_knowledge",
}
MAX_BYTES = 8 * 1024 * 1024
STAGES = frozenset({"missing", "import_requested", "import_rejected", "analysis_pending", "analysis_rejected", "analysis_timeout", "diagnosis_requested", "diagnosis_rejected", "diagnosis_terminal", "diagnosis_timeout"})
RUN_STATUSES = frozenset({"success", "failed", "error", "canceled"})


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise evaluation.EvaluationError("JSON对象存在重复键")
        result[key] = value
    return result


def read_json(path: Path):
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise evaluation.EvaluationError("输入缺失或超过8MiB")
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("非有限JSON")))


def load_dataset(directory: Path = DATA):
    inputs, oracle, frozen = [read_json(directory / name) for name in ("model_inputs.json", "oracle.json", "manifest.json")]
    for name, value in (("model_inputs", inputs), ("oracle", oracle)):
        if frozen[name + "_sha256"] != evaluation.canonical_sha256(value):
            raise evaluation.EvaluationError("冻结数据摘要不一致；新增版本，不覆盖既有清单")
    icases, ocases = {}, {}
    for case in inputs["cases"]:
        if set(case) != {"id", "collapsed_text", "goal"} or not re.fullmatch(r"jd[0-9]{2}", case["id"]) or case["id"] in icases:
            raise evaluation.EvaluationError("模型输入混入标签、重复或非法ID")
        if not 1 <= len(case["collapsed_text"].encode()) <= 8192 or not 1 <= len(case["goal"]) <= 600:
            raise evaluation.EvaluationError("模型输入超过固定预算")
        icases[case["id"]] = case
    for case in oracle["cases"]:
        if case["id"] in ocases or case["expected_outcome"] not in {"diagnosis", "input_rejected"} or not set(case["accepted_kinds"]) <= KINDS or case["top_k"] != 3:
            raise evaluation.EvaluationError("oracle结构无效")
        ocases[case["id"]] = case
    if not 1 <= len(icases) <= 8 or set(icases) != set(ocases) or frozen["case_ids"] != list(icases):
        raise evaluation.EvaluationError("固定输入/oracle/清单集合不一致")
    return icases, ocases, frozen


def build_plan(case_ids=None, repetitions=1):
    inputs, _, frozen = load_dataset()
    selected = case_ids or list(inputs)
    if len(set(selected)) != len(selected) or not selected or any(key not in inputs for key in selected) or type(repetitions) is not int or not 1 <= repetitions <= 3:
        raise evaluation.EvaluationError("case须唯一且来自冻结集，重复次数1至3")
    trials = [{"case_id": key, "replicate": rep, "input_sha256": evaluation.canonical_sha256(inputs[key])} for key in selected for rep in range(1, repetitions + 1)]
    body = {"schema_version": 1, "dataset_id": frozen["dataset_id"], "manifest_sha256": evaluation.canonical_sha256(frozen), "trials": trials, "knowledge_mode": "off", "report_protocol": 2, "author_visible": True, "external_blind_test": False}
    return {**body, "plan_sha256": evaluation.canonical_sha256(body)}


def valid_id(value):
    return isinstance(value, (str, int)) and not isinstance(value, bool) and re.fullmatch(r"[1-9][0-9]{0,18}", str(value)) is not None


def expected_analysis_sha(case):
    """复用真实Analyzer重放固定导入文本，拒绝拿其他分析结果替换此情境。"""
    actual = make_result(analyze_folded(case["collapsed_text"].encode()), "IMPORT_COLLAPSED", {"source": "import"})
    return evaluation.canonical_sha256(evaluation.result_projection(actual))


def score(payload: dict):
    inputs, oracle, frozen = load_dataset()
    plan = payload.get("plan", {})
    selected = list(dict.fromkeys(row.get("case_id") for row in plan.get("trials", [])))
    counts = [row.get("replicate") for row in plan.get("trials", [])]
    expected = build_plan(selected, max(counts, default=0))
    if plan != expected or not isinstance(payload.get("rows"), list):
        raise evaluation.EvaluationError("结果未绑定完整预注册计划")
    indexed = {}
    run_ids = set()
    task_ids = set()
    for row in payload["rows"]:
        if not isinstance(row, dict) or not isinstance(row.get("stage"), str) or row["stage"] not in STAGES:
            raise evaluation.EvaluationError("运行阶段必须为固定枚举，不能复制任意正文到公开评分")
        if "status" in row and (not isinstance(row["status"], str) or row["status"] not in STAGES | RUN_STATUSES):
            raise evaluation.EvaluationError("运行状态必须为固定枚举")
        key = (row.get("case_id"), row.get("replicate"))
        if key in indexed or key not in {(x["case_id"], x["replicate"]) for x in plan["trials"]}:
            raise evaluation.EvaluationError("重复或计划外试验")
        if row.get("input_sha256") != evaluation.canonical_sha256(inputs[key[0]]):
            raise evaluation.EvaluationError("试验输入摘要不一致")
        if row.get("task_id") is not None:
            if not valid_id(row["task_id"]) or str(row["task_id"]) in task_ids:
                raise evaluation.EvaluationError("各试验必须独立导入任务")
            task_ids.add(str(row["task_id"]))
        run = row.get("run")
        if run is not None:
            if not isinstance(run, dict) or not valid_id(run.get("id")) or str(run["id"]) in run_ids or str(run.get("task_id")) != str(row.get("task_id")):
                raise evaluation.EvaluationError("DiagnosisRun身份重复或绑定错误")
            run_ids.add(str(run["id"]))
            if run.get("goal") != inputs[key[0]]["goal"] or run.get("mode") != "model" or run.get("knowledge_mode") != "off" or run.get("report_protocol") != 2 or run.get("status") not in {"success", "failed", "error", "canceled"}:
                raise evaluation.EvaluationError("DiagnosisRun问题/模式/协议/终态不一致")
            if not valid_id(run.get("attempt_id")) or not valid_id(run.get("analysis_job_id")) or type(run.get("analysis_run")) is not int or run["analysis_run"] < 1:
                raise evaluation.EvaluationError("DiagnosisRun缺少实际来源轮次")
            if row.get("analysis_sha256") != expected_analysis_sha(inputs[key[0]]):
                raise evaluation.EvaluationError("实际Analyzer投影与冻结导入文本不一致")
        indexed[key] = row
    rows, hints, acceptance, guard_results, rejected = [], [], [], [], []
    for trial in plan["trials"]:
        key = (trial["case_id"], trial["replicate"])
        row, truth = indexed.get(key, {}), oracle[key[0]]
        result = {"case_id": key[0], "replicate": key[1], "family": truth["family"], "task_id": row.get("task_id"), "status": row.get("stage", "missing"), "controlled_fixture_hint": "unknown"}
        run = row.get("run") or {}
        report = evaluation.parse_embedded(run.get("report") or {}, dict, "report")
        trace = evaluation.parse_embedded(run.get("trace") or [], list, "trace")
        success = run.get("status") == "success"
        result["diagnosis_success"] = success
        if truth["expected_outcome"] == "input_rejected":
            matched = row.get("stage") in {"import_rejected", "analysis_rejected"} and row.get("error_code") in {"INVALID_INPUT", "INVALID_PARAMETERS", "INVALID_COLLAPSED"} and not run
            rejected.append(matched)
            result["input_rejection_matched"] = matched
        else:
            acceptance.append(success)
            kinds = [h.get("kind") for h in report.get("hypotheses", []) if isinstance(h, dict)]
            recognized = bool(kinds) and all(kind in KINDS for kind in kinds)
            matched = success and recognized and bool(set(kinds[:truth["top_k"]]) & set(truth["accepted_kinds"]))
            hints.append(matched)
            result["controlled_fixture_hint"] = {"observed_kinds": kinds[:4], "top_k": truth["top_k"], "matched": matched} if recognized else "unknown"
        if run:
            result["run_id"] = str(run["id"])
            result["report_sha256"] = evaluation.canonical_sha256(report)
            result["trace_sha256"] = evaluation.canonical_sha256(trace)
            if success:
                runtime = report.get("agent_runtime") or {}
                structural = report.get("report_version") == 2 and bool(report.get("facts")) and runtime.get("framework") == "agentscope" and runtime.get("framework_version") == "2.0.10" and runtime.get("agent_class") == "agentscope.agent.Agent" and type(runtime.get("model_calls")) is int and runtime["model_calls"] > 0
                evidence = evaluation.evidence_metrics(trace, report)
                tools_ok = all(step.get("tool") in TOOLS for step in trace)
                guard = structural and tools_ok and evidence["known_reference_coverage"]["rate"] == 1 and evidence["hypotheses_with_current_observation"]["rate"] == 1
                guard_results.append(guard)
                result["objective_report_guard"] = {"passed": guard, "structured_runtime_valid": structural, "tool_allowlist_valid": tools_ok, "fact_values_revalidated": False, **evidence}
        rows.append(result)
    return {"schema_version": 1, "kind": "jd_agent_controlled_scoring", "plan_sha256": plan["plan_sha256"], "manifest_sha256": evaluation.canonical_sha256(frozen), "metrics": {"diagnosis_acceptance": evaluation.ratio(acceptance), "controlled_fixture_hint_top3": evaluation.ratio(hints), "invalid_input_rejection": evaluation.ratio(rejected), "successful_report_objective_guard": evaluation.ratio(guard_results), "semantic_root_cause_topk": None, "normal_load_semantic_false_positive_rate": None, "semantic_abstention_accuracy": None, "independent_human_review_coverage": evaluation.ratio([False for row in rows if "run_id" in row])}, "rows": rows, "limitations": ["固定集为作者可见合成回归，不是外部盲测。", "hint只比较预注册标签与hypotheses.kind，不验证自由解释、因果或真实正常负载。", "失败/缺失保留在预注册分母，模型失败不算拒答。", "JSON文件不是真实性签名；需结合实际HTTP私有回执与发布版本审计。", "结构与工具白名单通过不等于通用提示注入防御已被证明。"]}


def human_template(payload):
    measured = score(payload)
    return {"schema_version": 1, "kind": "independent_human_semantic_review", "plan_sha256": measured["plan_sha256"], "reviews": [{"case_id": row["case_id"], "replicate": row["replicate"], "run_id": row["run_id"], "report_sha256": row["report_sha256"], "assessment_type": "human_manual_review", "reviewer": None, "independence_attested": False, "blind_to_oracle": False, "root_cause_topk_correct": None, "causal_ground_truth_reference": None, "normal_load_false_positive": None, "appropriate_abstention": None, "notes": None} for row in measured["rows"] if "run_id" in row], "instructions": ["独立真人阅读实际报告与输入后填写，不可导入Agent自评。", "没有人工审阅保持null；作者可见数据不称独立盲测。", "报告SHA变化须重新审阅，不得沿用旧标签。"]}


def score_human(payload, reviews):
    """人工输入与客观评分分列；无法验证填写者现实身份，不会自动补审阅标签。"""
    measured = score(payload)
    if not isinstance(reviews, dict) or reviews.get("kind") != "independent_human_semantic_review" or reviews.get("schema_version") != 1 or reviews.get("plan_sha256") != measured["plan_sha256"] or not isinstance(reviews.get("reviews"), list):
        raise evaluation.EvaluationError("人工审阅未绑定本次计划")
    indexed = {row["run_id"]: row for row in measured["rows"] if "run_id" in row}
    seen, adjudicated, root, normal, abstention = set(), set(), [], [], []
    for review in reviews["reviews"]:
        run_id = review.get("run_id")
        if run_id not in indexed or run_id in seen:
            raise evaluation.EvaluationError("未知或重复人工审阅运行")
        seen.add(run_id)
        row = indexed[run_id]
        if any(review.get(field) != row[field] for field in ("case_id", "replicate", "report_sha256")):
            raise evaluation.EvaluationError("人工审阅报告或情境已变")
        labels = [review.get(key) for key in ("root_cause_topk_correct", "normal_load_false_positive", "appropriate_abstention")]
        if all(value is None for value in labels):
            continue
        if review.get("assessment_type") != "human_manual_review" or review.get("independence_attested") is not True or not isinstance(review.get("reviewer"), str) or not 1 <= len(review["reviewer"].strip()) <= 120 or any(value is not None and type(value) is not bool for value in labels):
            raise evaluation.EvaluationError("只能录入明确独立真人的布尔标签，不能以Agent自评代替")
        adjudicated.add(run_id)
        # 仅有采样情境不能证明根因，根因评分还要求独立可查的因果真值来源。
        if labels[0] is not None:
            reference = review.get("causal_ground_truth_reference")
            if not isinstance(reference, str) or not 1 <= len(reference.strip()) <= 300:
                raise evaluation.EvaluationError("根因评分需独立因果真值来源")
            root.append(labels[0])
        if labels[1] is not None:
            if row["family"] != "normal_control":
                raise evaluation.EvaluationError("正常误报只在预注册正常对照上统计")
            normal.append(labels[1])
        if labels[2] is not None:
            if row["family"] not in {"insufficient", "untrusted_symbol_injection"}:
                raise evaluation.EvaluationError("证据不足拒答只在预注册不足情境上统计")
            abstention.append(labels[2])
    measured["human_metrics"] = {"semantic_root_cause_topk": evaluation.ratio(root) if root else None, "normal_load_semantic_false_positive_rate": evaluation.ratio(normal) if normal else None, "semantic_abstention_accuracy": evaluation.ratio(abstention) if abstention else None, "labels_are_independence_attestations_not_identity_verification": True}
    measured["metrics"]["independent_human_review_coverage"] = evaluation.ratio([run_id in adjudicated for run_id in indexed])
    return measured


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "score", "human-template", "human-score"])
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--reviews", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "plan":
            result = build_plan(args.cases, args.repetitions)
        elif args.command == "human-score":
            result = score_human(read_json(args.input), read_json(args.reviews))
        else:
            result = (score if args.command == "score" else human_template)(read_json(args.input))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as target:
            json.dump(result, target, ensure_ascii=False, indent=2, allow_nan=False)
            target.write("\n")
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f"评测未完成：{type(exc).__name__}；请检查输入协议与输出是否已存在。\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
