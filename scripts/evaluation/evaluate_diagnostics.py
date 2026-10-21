"""运行真实服务离线防线评测，或对显式导入的真实 API 成对结果评分。

本工具不联网、不登录、不触发采集或模型。live 仅表示调用方提供实际诊断 API
结果；JSON 不是加密证明。语义类别必须由独立人工审阅给出，不做关键词猜分。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = Path(__file__).with_name("diagnostic_cases.json")
CURRENT_TOOLS = {
    "read_profile_summary",
    "inspect_hotspots",
    "check_collection_quality",
    "inspect_call_paths",
    "compare_profiles",
}
CATEGORIES = {
    "cpu_hotspot",
    "allocation_gc",
    "lock_io",
    "normal_load",
    "insufficient_evidence",
    "invalid_evidence",
}
MAX_INPUT_BYTES = 8 * 1024 * 1024


class EvaluationError(ValueError):
    """输入或来源边界不满足评测协议。"""


def canonical_sha256(value: Any) -> str:
    """固定 case 的字节协议：UTF-8、键排序、无多余空白，不允许 NaN。"""
    body = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def load_json(path: Path) -> Any:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise EvaluationError("输入超过 8 MiB 上限")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_cases(path: Path = DEFAULT_CASES) -> dict[str, dict[str, Any]]:
    dataset = load_json(path)
    if not isinstance(dataset, dict) or dataset.get("schema_version") != 1:
        raise EvaluationError("不支持的固定集版本")
    cases: dict[str, dict[str, Any]] = {}
    profile_splits: dict[str, str] = {}
    for case in dataset.get("cases", []):
        if not isinstance(case, dict):
            raise EvaluationError("固定案例须为对象")
        case_id = case.get("id")
        p = case.get("provenance", {})
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id in cases
            or case.get("split") not in {"dev", "holdout"}
            or p.get("source") != "imported_synthetic"
            or p.get("imported") is not True
            or p.get("synthetic") is not True
            or p.get("production_data") is not False
        ):
            raise EvaluationError("固定集必须有唯一 ID、分区及明确非生产导入来源")
        digest = canonical_sha256({"task": case["task"], "profile": case["profile"]})
        old_split = profile_splits.setdefault(digest, case["split"])
        if old_split != case["split"]:
            raise EvaluationError("dev 与 holdout 不能包含同一份输入")
        cases[case_id] = case
    if not cases:
        raise EvaluationError("固定集为空")
    for case in cases.values():
        for prior in case.get("prior_candidates", []):
            source = cases.get(prior.get("source_case_id"))
            if (
                source is None
                or source["id"] == case["id"]
                or source["split"] != "dev"
                or prior.get("source_split") != "dev"
            ):
                raise EvaluationError("经验背景只能来自不同 case 的 dev 分区")
    return cases


def manifest(cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "dataset_id": "drop-diagnostic-boundaries-v1",
        "cases": [
            {
                "case_id": case["id"],
                "case_sha256": canonical_sha256(case),
                "split": case["split"],
                "family": case["family"],
                "live_eligible": "reproduction" in case,
            }
            for case in cases.values()
        ],
        "limits": [
            "固定集是作者可见的合成回归集，holdout 不等于外部盲测。",
            "live 结果须明确绑定授权导入测试任务，不允许自动使用既有生产历史。",
        ],
    }


def ratio(values: list[bool]) -> dict[str, Any]:
    return {
        "numerator": sum(values),
        "denominator": len(values),
        "rate": sum(values) / len(values) if values else None,
    }


def pooled_ratio(values: list[dict[str, Any]]) -> dict[str, Any]:
    numerator = sum(value["numerator"] for value in values)
    denominator = sum(value["denominator"] for value in values)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def offline(cases: dict[str, dict[str, Any]], fixture_path: Path) -> dict[str, Any]:
    # Go 测试读取仓库固定路径，禁止 CLI 指向另一份数据却把结果标成同一套固定集。
    if fixture_path.resolve() != DEFAULT_CASES.resolve():
        raise EvaluationError("offline 只运行仓库固定集，替换数据须显式修改版本")
    with tempfile.TemporaryDirectory(prefix="drop-diagnostic-evaluation-") as directory:
        receipt = Path(directory) / "observations.json"
        env = dict(os.environ, DROP_DIAGNOSTIC_EVAL_OUTPUT=str(receipt))
        command = [
            "go",
            "test",
            "./internal/service",
            "-run",
            "^TestDiagnosticEvaluation",
            "-count=1",
            "-json",
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT / "api",
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=240,
            check=False,
        )
        if completed.returncode or not receipt.exists():
            # 错误时只返回命令与退出码；避免把未来测试可能打印的敏感正文复制到公开结果。
            raise EvaluationError(
                f"真实 Go 评测未通过，退出码 {completed.returncode}；请直接运行 go test 查看错误"
            )
        measured = load_json(receipt)
    observations = measured.get("observations", [])
    if {o["case_id"] for o in observations} != set(cases):
        raise EvaluationError("Go 实测案例与固定集不一致")
    routing, rejection, guards = [], [], []
    rows = []
    for o in observations:
        case = cases[o["case_id"]]
        truth = case["ground_truth"]
        rejection.append(o["matcher_rejected"] == truth["matcher_rejects"])
        if not truth["matcher_rejects"]:
            routing.append(sorted(o["skill_ids"]) == sorted(truth["skill_ids"]))
        for variant in o["variants"]:
            guards.extend(
                g["actual_accepted"] == g["expected_accepted"]
                for g in variant["guards"]
            )
        rows.append(
            {
                **o,
                "case_sha256": canonical_sha256(case),
                "skill_routing_scored": not truth["matcher_rejects"],
            }
        )
    return {
        "schema_version": 1,
        "mode": "deterministic_offline",
        "controller_oracle": True,
        "model_calls": 0,
        "dataset_manifest": manifest(cases),
        "metrics": {
            "skill_routing_exact_match": ratio(routing),
            "input_accept_reject_contract": ratio(rejection),
            "report_guard_contract": ratio(guards),
            "root_cause_category_hit": None,
            "abstention_accuracy": None,
            "normal_load_false_positive_rate": None,
            "model_tokens": None,
            "estimated_cost": None,
        },
        "prior_database_scope_checks": "not_executed_by_this_offline_suite",
        "observations": rows,
        "limits": [
            "调用真正的服务函数，但报告由明确的控制器样例构造，不是模型效果。",
            "off 不加载知识；不能把其 Skill 为空记为 0 分并宣称模型准确率提升。",
            "耗时仅为本机纯服务路由和协议测试耗时，不可与模型端到端耗时直接比较。",
            "过期/异主早期拒绝有真实入口验证；跨项目/版本/撤权的数据库链路由独立集成测试覆盖。",
            "恶意历史用例验证仅先验引用不能过关，不保证模型解释或混合引用的语义正确。",
        ],
    }


def parse_embedded(value: Any, expected: type, field: str) -> Any:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, expected):
        raise EvaluationError(f"{field} 类型无效")
    return value


def number(value: Any, field: str, *, integer: bool = False) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise EvaluationError(f"{field} 须为有限非负数字")
    if integer and not isinstance(value, int):
        raise EvaluationError(f"{field} 须为非负整数")
    return value


def evidence_metrics(trace: list, report: dict) -> dict[str, Any]:
    known, current = set(), set()
    for step in trace:
        if not isinstance(step, dict) or not isinstance(step.get("evidence_id"), str):
            raise EvaluationError("trace 缺少明确 E 编号")
        evidence_id = step["evidence_id"]
        if evidence_id in known:
            raise EvaluationError("trace 的 E 编号重复")
        known.add(evidence_id)
        # 新 live 数据要求真实工具名；不会把空 Tool 的旧兼容模式当作新观测。
        tool = step.get("tool")
        if not isinstance(tool, str):
            raise EvaluationError("新 live trace 必须有明确工具名")
        if tool in CURRENT_TOOLS:
            current.add(evidence_id)
    hypotheses = report.get("hypotheses", [])
    if not isinstance(hypotheses, list):
        raise EvaluationError("hypotheses 类型无效")
    supported, valid_references = [], []
    for hypothesis in hypotheses:
        if not isinstance(hypothesis, dict):
            raise EvaluationError("hypothesis 须为对象")
        refs = hypothesis.get("evidence_ids", [])
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
            raise EvaluationError("证据引用必须是编号数组")
        supported.append(any(ref in current for ref in refs))
        valid_references.extend(ref in known for ref in refs)
    return {
        "hypotheses_with_current_observation": ratio(supported),
        "known_reference_coverage": ratio(valid_references),
        "semantic_truth_verified": False,
    }


def result_projection(result: dict) -> dict[str, Any]:
    """只比较分析语义；运行环境与动态规则元数据不作为同一输入的必要条件。"""
    summary = result.get("summary")
    if not isinstance(summary, dict):
        raise EvaluationError("analysis_result 缺少 summary")
    fields = ("kind", "total_samples", "node_count", "top_functions", "flamegraph")
    if (
        any(key not in result for key in fields)
        or "unit" not in summary
        or "source" not in summary
    ):
        raise EvaluationError("analysis_result 缺少稳定语义投影字段")
    return {
        **{key: result[key] for key in fields},
        "summary": {"unit": summary["unit"], "source": summary["source"]},
    }


def evaluate_live(
    payload: dict,
    cases: dict[str, dict[str, Any]],
    on_mode: str = "experience",
) -> dict[str, Any]:
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("mode") != "live"
        or payload.get("controller_oracle") is True
    ):
        raise EvaluationError("live 输入必须明确来自真实 API，不能使用控制器或离线样例")
    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs:
        raise EvaluationError("live runs 不能为空")
    indexed: dict[tuple[str, str], dict] = {}
    rows = []
    for row in runs:
        if not isinstance(row, dict):
            raise EvaluationError("每条 run 须为对象")
        case_id, mode = row.get("case_id"), row.get("knowledge_mode")
        if (
            not isinstance(case_id, str)
            or case_id not in cases
            or not isinstance(mode, str)
            or mode not in {"off", on_mode}
        ):
            raise EvaluationError("未知 case 或知识模式；每次只比较 off 与指定 on 模式")
        if (case_id, mode) in indexed:
            raise EvaluationError("同一 case/知识模式不能有重复结果")
        case = cases[case_id]
        if case["ground_truth"]["matcher_rejects"]:
            raise EvaluationError(
                "非法证据夹具仅用于离线拒绝测试，不能伪装成成功导入任务"
            )
        if "reproduction" not in case:
            raise EvaluationError(
                "此 case 只有最小离线 envelope，不能假装成在线 Analyzer 复现数据"
            )
        actual_result = parse_embedded(
            row.get("analysis_result"), dict, "analysis_result"
        )
        if canonical_sha256(result_projection(actual_result)) != canonical_sha256(
            result_projection(case["profile"])
        ):
            raise EvaluationError("实际 Analyzer 结果与固定输入的语义投影不一致")
        p = row.get("fixture_provenance", {})
        if (
            not isinstance(p, dict)
            or p.get("source") != "imported_synthetic"
            or p.get("case_sha256") != canonical_sha256(case)
            or not isinstance(p.get("task_id"), str)
            or not p["task_id"].isascii()
            or not p["task_id"].isdigit()
            or int(p["task_id"]) <= 0
        ):
            raise EvaluationError(
                "live 缺少显式导入来源、固定 case SHA 或授权测试任务 ID"
            )
        run = row.get("run", {})
        if (
            not isinstance(run, dict)
            or str(run.get("task_id")) != p["task_id"]
            or run.get("mode") != "model"
            or run.get("knowledge_mode") != mode
            or run.get("status") not in {"success", "failed", "error"}
        ):
            raise EvaluationError(
                "真实 DiagnosisRun 与 fixture/知识模式不一致或尚未完成"
            )
        # 同一 case 的 off/on 必须绑定同一分析轮次；InputDigest 含知识开关，不能要求它们相同。
        for key in ("attempt_id", "analysis_job_id", "analysis_run", "id", "goal"):
            if run.get(key) is None:
                raise EvaluationError("真实 DiagnosisRun 缺少来源轮次或运行 ID")
        prior_sources = row.get("experience_sources", [])
        if not isinstance(prior_sources, list):
            raise EvaluationError("experience_sources 必须是显式来源数组")
        for prior in prior_sources:
            if not isinstance(prior, dict):
                raise EvaluationError("经验来源须为对象")
            source_case = cases.get(prior.get("case_id"))
            if (
                source_case is None
                or source_case["split"] != "dev"
                or source_case["id"] == case_id
                or prior.get("case_sha256") != canonical_sha256(source_case)
            ):
                raise EvaluationError("经验来源泄漏：只接受不同 case 的 dev 固定集来源")
        elapsed = number(row.get("elapsed_ms"), "elapsed_ms")
        success = run["status"] == "success"
        report = parse_embedded(run.get("report") or {}, dict, "report")
        trace = parse_embedded(run.get("trace") or [], list, "trace")
        runtime = report.get("agent_runtime") or {}
        if not isinstance(runtime, dict):
            raise EvaluationError("agent_runtime 须为对象")
        if success and (
            runtime.get("framework") != "agentscope"
            or runtime.get("framework_version") != "2.0.10"
            or runtime.get("agent_class") != "agentscope.agent.Agent"
            or number(runtime.get("model_calls"), "model_calls", integer=True) < 1
        ):
            raise EvaluationError("成功的 live 结果缺少真实 AgentScope 模型运行元数据")
        usage = runtime.get("usage")
        tokens = None
        if usage is not None:
            usage = parse_embedded(usage, dict, "usage")
            tokens = {
                key: number(usage.get(key), key, integer=True)
                for key in ("input_tokens", "output_tokens")
            }
        knowledge = report.get("knowledge") or {}
        if not isinstance(knowledge, dict):
            raise EvaluationError("knowledge 须为对象")
        actual_priors = knowledge.get("cases") or []
        actual_skills = knowledge.get("skills") or []
        if not isinstance(actual_priors, list) or not isinstance(actual_skills, list):
            raise EvaluationError("knowledge cases/skills 须为数组")
        if any(
            not isinstance(skill, dict) or not isinstance(skill.get("id"), str)
            for skill in actual_skills
        ):
            raise EvaluationError("knowledge Skill 缺少 ID")
        if mode == "off" and (knowledge.get("skills") or actual_priors):
            raise EvaluationError("off 结果不能附带已加载的知识")
        if mode == "skills" and actual_priors:
            raise EvaluationError("skills 结果不能加载历史经验")
        if actual_priors and not prior_sources:
            raise EvaluationError(
                "真实结果加载了经验，须提供显式的 dev 来源清单以审计泄漏"
            )
        assessment = row.get("assessment")
        semantic = None
        if assessment is not None:
            if (
                not isinstance(assessment, dict)
                or assessment.get("method") != "manual_review"
                or not isinstance(assessment.get("reviewer"), str)
                or not assessment["reviewer"].strip()
                or assessment.get("category") not in CATEGORIES
                or type(assessment.get("abstained")) is not bool
                or type(assessment.get("actionable_defect")) is not bool
                or not success
            ):
                raise EvaluationError(
                    "语义评分须有独立人工审阅、有效类别与明确布尔结论"
                )
            truth = case["ground_truth"]
            semantic = {
                "category_hit": assessment["category"] in truth["accepted_categories"],
                "abstention_required": truth["abstention_required"],
                "abstained": assessment["abstained"],
                "normal_load": truth["normal_load"],
                "actionable_defect": assessment["actionable_defect"],
                "method": "manual_review",
            }
        measured = {
            "case_id": case_id,
            "case_sha256": canonical_sha256(case),
            "split": case["split"],
            "knowledge_mode": mode,
            "success": success,
            "model": report.get("model")
            if isinstance(report.get("model"), str)
            and len(report["model"]) <= 100
            and "://" not in report["model"]
            else None,
            "framework_version": runtime.get("framework_version") if success else None,
            "elapsed_ms": elapsed,
            "model_tokens": tokens,
            "evidence": evidence_metrics(trace, report),
            "semantic_assessment": semantic,
            "loaded_experience_count": len(actual_priors),
            "skill_routing_exact_match": sorted(s["id"] for s in actual_skills)
            == sorted(case["ground_truth"]["skill_ids"])
            if success and mode != "off"
            else None,
        }
        indexed[case_id, mode] = {"source": run, "metrics": measured}
        rows.append(measured)
    case_ids = {case_id for case_id, _ in indexed}
    for case_id in case_ids:
        if (case_id, "off") not in indexed or (case_id, on_mode) not in indexed:
            raise EvaluationError("每个 live case 必须有完整 off/on 成对结果")
        a, b = (indexed[case_id, mode]["source"] for mode in ("off", on_mode))
        if any(
            str(a[key]) != str(b[key])
            for key in (
                "task_id",
                "attempt_id",
                "analysis_job_id",
                "analysis_run",
                "goal",
            )
        ):
            raise EvaluationError("off/on 必须使用相同任务和当前分析轮次")
        if str(a["id"]) == str(b["id"]):
            raise EvaluationError("off/on 不能复用同一次诊断运行")
    by_mode = {}
    for mode in ("off", on_mode):
        variant = [row for row in rows if row["knowledge_mode"] == mode]
        assessed = [
            row["semantic_assessment"]
            for row in variant
            if row["semantic_assessment"] is not None
        ]
        latency = sorted(row["elapsed_ms"] for row in variant)
        by_mode[mode] = {
            "runs": len(variant),
            "success": ratio([row["success"] for row in variant]),
            "root_cause_category_hit": ratio([a["category_hit"] for a in assessed]),
            "required_abstention_recall": ratio(
                [a["abstained"] for a in assessed if a["abstention_required"]]
            ),
            "normal_load_false_positive_rate": ratio(
                [a["actionable_defect"] for a in assessed if a["normal_load"]]
            ),
            "manual_review_coverage": ratio(
                [row["semantic_assessment"] is not None for row in variant]
            ),
            "runs_with_loaded_experience": ratio(
                [row["loaded_experience_count"] > 0 for row in variant]
            ),
            "skill_routing_exact_match": ratio(
                [
                    row["skill_routing_exact_match"]
                    for row in variant
                    if row["skill_routing_exact_match"] is not None
                ]
            ),
            "hypotheses_with_current_observation": pooled_ratio(
                [
                    row["evidence"]["hypotheses_with_current_observation"]
                    for row in variant
                ]
            ),
            "known_reference_coverage": pooled_ratio(
                [row["evidence"]["known_reference_coverage"] for row in variant]
            ),
            "elapsed_ms": {
                "median": median(latency),
                "min": min(latency),
                "max": max(latency),
            },
            "model_tokens": {
                key: sum(
                    row["model_tokens"][key]
                    for row in variant
                    if row["model_tokens"] is not None
                )
                if all(row["model_tokens"] is not None for row in variant)
                else None
                for key in ("input_tokens", "output_tokens")
            },
            "estimated_cost": None,
        }
    return {
        "schema_version": 1,
        "mode": "live_external_api_pairs",
        "source_authenticity": "declared_by_external_collector_not_cryptographically_attested",
        "pair_count": len(case_ids),
        "by_knowledge_mode": by_mode,
        "observations": sorted(rows, key=lambda r: (r["case_id"], r["knowledge_mode"])),
        "limits": [
            "本工具不联网；实际模型/API来源由外部采集器负责，输入JSON不是加密证明。",
            "根因类别、拒答与误报只来自显式独立人工审阅，未审阅为 null，不从文本关键词造分。",
            "证据覆盖只核对编号及当前观测来源，不保证解释语义、因果关系或模型抗注入。",
            "合成固定集与可见 holdout 的小样本不能外推生产准确率；成对模型采样也可能随机波动。",
            "没有核验定价来源，不估算模型费用；缺少 usage 时不把 token 计成零。",
        ],
    }


def median(values: list[int | float]) -> int | float:
    middle = len(values) // 2
    return (
        values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("offline", "live"), required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument(
        "--input", type=Path, help="live 实际 API pair JSON；不会访问地址或读取密钥"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--on-mode", choices=("skills", "experience"), default="experience"
    )
    parser.add_argument(
        "--manifest",
        action="store_true",
        help="只输出固定案例 SHA 清单，不执行 Go 或模型",
    )
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases)
        if args.manifest:
            result = manifest(cases)
        elif args.mode == "offline":
            result = offline(cases, args.cases)
        else:
            if args.input is None:
                raise EvaluationError(
                    "live 需要外部真实 API 结果 --input，不会自动联网采集"
                )
            result = evaluate_live(load_json(args.input), cases, args.on_mode)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {"mode": result.get("mode", "manifest"), "output": str(args.output)},
                ensure_ascii=False,
            )
        )
        return 0
    except (
        EvaluationError,
        OSError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"评测未完成：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
