#!/usr/bin/env python3
"""Run and persist the independent diagnostic-strategy evolution benchmark."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _diagnosis_worker_evidence() -> dict:
    worker_path = ROOT / "server" / "app" / "diagnosis_worker.py"
    rpc_path = ROOT / "server" / "app" / "diagnostic_ai_rpc.py"
    proto_path = ROOT / "proto" / "diagnostic_ai.proto"
    return {
        "integration": "PRIVATE_GRPC_DIAGNOSIS_WORKER",
        "public_http_owner": "GO_APISERVER",
        "collector_executor": "NATIVE_CPP_AGENT",
        "diagnosis_modes": ["OBSERVE_ONLY", "ASSISTED", "AUTO"],
        "worker_sha256": _sha256(worker_path),
        "rpc_sha256": _sha256(rpc_path),
        "proto_sha256": _sha256(proto_path),
    }


def _render_markdown(report: dict, report_path: Path) -> str:
    replay = report["independent_replay"]
    claim = replay["claim_card"]
    baseline = claim["baseline"]
    enabled = claim["skill_enabled"]
    delta = claim["delta"]
    boundary = claim["measurement_boundary"]
    paired = replay["paired_evidence"]
    baseline_interval = paired["baseline_pass_rate_interval"]
    skill_interval = paired["skill_pass_rate_interval"]
    runtime = report["provenance"]["diagnostic_ai"]
    return "\n".join([
        "# Mini-Drop AI 诊断与 Skill A/B 证据卡",
        "",
        f"- 生成时间：`{report['provenance']['generated_at']}`",
        f"- JSON 产物：`{report_path}`",
        f"- AI 运行边界：`{runtime['integration']}`，公开入口 `{runtime['public_http_owner']}`。",
        f"- 采集执行：`{runtime['collector_executor']}`。",
        f"- 诊断模式：`{' / '.join(runtime['diagnosis_modes'])}`。",
        "",
        "## 可支持的结论",
        "",
        f"- 基线：`{baseline['passed']}/{baseline['total']}`，即 `{baseline['percent']:.2f}%`。",
        f"- 启用 Skill：`{enabled['passed']}/{enabled['total']}`，即 `{enabled['percent']:.2f}%`。",
        f"- 增量：`+{delta['percentage_points']:.2f}` 个百分点，不是启用后只有 60%。",
        f"- 原始机器值：`delta.rate={delta['rate']}`。",
        f"- 成对变化：改善 `{paired['improved']}`，退化 `{paired['regressed']}`，不变 `{paired['unchanged']}`。",
        f"- 成对精确检验：`p={paired['paired_test']['p_value_two_sided']:.8f}`；仅描述该冻结离线集。",
        (
            "- 95% Wilson 区间：基线 "
            f"`[{baseline_interval['lower']:.3f}, {baseline_interval['upper']:.3f}]`，"
            f"Skill `[{skill_interval['lower']:.3f}, {skill_interval['upper']:.3f}]`。"
        ),
        "",
        "## 不能外推的结论",
        "",
        f"- 根因准确率：`{boundary['root_cause_accuracy']}`。",
        f"- 真实诊断耗时：`{boundary['diagnosis_duration_ms']}`。",
        f"- 工具调用数：`{boundary['tool_call_count']}`，当前属于离线投影成本。",
        "- 因此该结果证明离线路由和生命周期契约，不证明 Linux 线上根因准确率。",
        "- 40% 是冻结的通用初筛参考策略，不是当前生产 Planner 的历史线上成功率。",
        f"- 基线边界：{claim['baseline_boundary']}",
        "",
        "## 可复核血缘",
        "",
        f"- 数据集 SHA256：`{report['provenance']['dataset_sha256']}`",
        f"- 评分器 SHA256：`{report['provenance']['scorer_sha256']}`",
        f"- Diagnosis Worker SHA256：`{runtime['worker_sha256']}`",
        f"- 内部 RPC SHA256：`{runtime['rpc_sha256']}`",
        f"- RPC Proto SHA256：`{runtime['proto_sha256']}`",
        "",
    ])


def _load_benchmark_module():
    path = ROOT / "tests" / "test_diagnostic_skill_benchmark.py"
    spec = importlib.util.spec_from_file_location("skill_evolution_benchmark", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load skill evolution benchmark")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "skill-evolution" / "benchmark-report.json",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=None,
        help="Optional human-readable evidence card; defaults next to the JSON report.",
    )
    args = parser.parse_args()
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"

    benchmark = _load_benchmark_module()
    benchmark.reset_engine()
    benchmark.init_db()
    try:
        cases = benchmark._load_cases()
        result = benchmark.compare_benchmark_runs(
            cases,
            benchmark._baseline_observations(cases),
            benchmark._skill_enabled_observations(cases),
        )
        # The ordered campaign must start from a clean registry. Otherwise the
        # independent replay above would leak skill versions into its state.
        benchmark.reset_engine()
        benchmark.init_db()
        longitudinal = benchmark.run_longitudinal_adversarial_campaign()
    finally:
        benchmark.reset_engine()

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    dataset_path = ROOT / "tests" / "fixtures" / "diagnostic_skill_evolution" / "cases.json"
    scorer_path = ROOT / "server" / "app" / "drop_insight" / "skill_benchmark.py"
    report = {
        "schema": "mini-drop.skill-evolution-benchmark.v2",
        "provenance": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset_path": str(dataset_path.relative_to(ROOT)),
            "dataset_sha256": _sha256(dataset_path),
            "scorer_path": str(scorer_path.relative_to(ROOT)),
            "scorer_sha256": _sha256(scorer_path),
            "diagnostic_ai": _diagnosis_worker_evidence(),
        },
        "independent_replay": result,
        "longitudinal_campaign": longitudinal,
    }
    output.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            default=str,
        ) + "\n",
        encoding="utf-8",
    )
    markdown_output = (
        args.markdown_output.resolve()
        if args.markdown_output is not None
        else output.with_suffix(".md")
    )
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(_render_markdown(report, output), encoding="utf-8")
    summary = {
        "benchmark_kind": result["benchmark_kind"],
        "case_count": result["skill_enabled"]["total"],
        "baseline_passed": result["baseline"]["passed"],
        "skill_enabled_passed": result["skill_enabled"]["passed"],
        "delta": result["delta"],
        "claim_card": result["claim_card"],
        "metrics": result["skill_enabled"]["metrics"],
        "claim_boundary": result["claim_boundary"],
        "longitudinal_campaign": {
            "case_count": longitudinal["case_count"],
            "passed": longitudinal["passed"],
            "pass_rate": longitudinal["pass_rate"],
            "capabilities": longitudinal["capabilities"],
            "claim_boundary": longitudinal["claim_boundary"],
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"REPORT={output}")
    print(f"EVIDENCE_CARD={markdown_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
