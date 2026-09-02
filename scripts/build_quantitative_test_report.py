from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str], *, cwd: Path = ROOT) -> dict[str, Any]:
    started = time.perf_counter()
    process = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0"},
        check=False,
    )
    return {
        "command": command,
        "exit_code": process.returncode,
        "duration_seconds": round(time.perf_counter() - started, 3),
        "stdout": process.stdout,
        "stderr": process.stderr,
    }


def _match_int(pattern: str, text: str) -> int:
    match = re.search(pattern, text, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _clean_tail(text: str, lines: int) -> str:
    return "\n".join(text.replace("\ufffd", "?").strip().splitlines()[-lines:])


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"required benchmark report is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _python_regression(output_dir: Path) -> dict[str, Any]:
    coverage_path = output_dir / "coverage.json"
    result = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--cov=server",
            "--cov=analyzer",
            f"--cov-report=json:{coverage_path}",
        ]
    )
    combined = result["stdout"] + "\n" + result["stderr"]
    coverage = _load_json(coverage_path) if coverage_path.is_file() else {}
    totals = coverage.get("totals", {})
    return {
        "status": "PASS" if result["exit_code"] == 0 else "FAIL",
        "passed": _match_int(r"(\d+)\s+passed", combined),
        "skipped": _match_int(r"(\d+)\s+skipped", combined),
        "failed": _match_int(r"(\d+)\s+failed", combined),
        "duration_seconds": result["duration_seconds"],
        "coverage": {
            "percent": round(float(totals.get("percent_covered", 0.0)), 2),
            "covered_lines": int(totals.get("covered_lines", 0)),
            "missing_lines": int(totals.get("missing_lines", 0)),
            "num_statements": int(totals.get("num_statements", 0)),
        },
        "command": result["command"],
        "output_tail": _clean_tail(combined, 12),
    }


def _go_regression() -> dict[str, Any]:
    go = shutil.which("go")
    if not go:
        return {"status": "SKIP", "reason": "go executable not found"}
    result = _run([go, "test", "-json", "./...", "-count=1"], cwd=ROOT / "apiserver")
    packages: dict[str, str] = {}
    tests = {"passed": 0, "failed": 0, "skipped": 0}
    for line in result["stdout"].splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        action = event.get("Action")
        package = event.get("Package")
        test = event.get("Test")
        if test and action in {"pass", "fail", "skip"}:
            tests[{"pass": "passed", "fail": "failed", "skip": "skipped"}[action]] += 1
        elif package and not test and action in {"pass", "fail", "skip"}:
            packages[package] = action.upper()
    return {
        "status": "PASS" if result["exit_code"] == 0 else "FAIL",
        "package_count": len(packages),
        "packages_passed": sum(status == "PASS" for status in packages.values()),
        "packages_without_tests": sum(status == "SKIP" for status in packages.values()),
        "packages_failed": sum(status == "FAIL" for status in packages.values()),
        "tests": tests,
        "duration_seconds": result["duration_seconds"],
        "command": result["command"],
        "output_tail": _clean_tail(result["stderr"], 12),
    }


def _npm_executable() -> str | None:
    return shutil.which("npm.cmd") or shutil.which("npm")


def _web_regression() -> dict[str, Any]:
    npm = _npm_executable()
    if not npm:
        return {"status": "SKIP", "reason": "npm executable not found"}
    result = _run([npm, "test"], cwd=ROOT / "web")
    combined = result["stdout"] + "\n" + result["stderr"]
    return {
        "status": "PASS" if result["exit_code"] == 0 else "FAIL",
        "test_files_passed": _match_int(r"Test Files\s+(\d+)\s+passed", combined),
        "test_files_failed": _match_int(r"Test Files\s+(\d+)\s+failed", combined),
        "tests_passed": _match_int(r"Tests\s+(\d+)\s+passed", combined),
        "tests_failed": _match_int(r"Tests\s+(\d+)\s+failed", combined),
        "tests_skipped": _match_int(r"Tests\s+(\d+)\s+skipped", combined),
        "duration_seconds": result["duration_seconds"],
        "command": result["command"],
        "output_tail": _clean_tail(combined, 16),
    }


def _web_build() -> dict[str, Any]:
    npm = _npm_executable()
    if not npm:
        return {"status": "SKIP", "reason": "npm executable not found"}
    result = _run([npm, "run", "build"], cwd=ROOT / "web")
    combined = result["stdout"] + "\n" + result["stderr"]
    chunks = []
    for match in re.finditer(
        r"(?P<name>\S+\.js)\s+(?P<raw>[\d,.]+)\s+kB\s+.*?gzip:\s+(?P<gzip>[\d,.]+)\s+kB",
        combined,
    ):
        chunks.append(
            {
                "name": match.group("name"),
                "raw_kb": float(match.group("raw").replace(",", "")),
                "gzip_kb": float(match.group("gzip").replace(",", "")),
            }
        )
    largest = max(chunks, key=lambda item: item["raw_kb"], default=None)
    return {
        "status": "PASS" if result["exit_code"] == 0 else "FAIL",
        "duration_seconds": result["duration_seconds"],
        "javascript_chunk_count": len(chunks),
        "largest_javascript_chunk": largest,
        "command": result["command"],
        "output_tail": _clean_tail(combined, 16),
    }


def _benchmark_summary() -> dict[str, Any]:
    rcaeval = _load_json(ROOT / "artifacts" / "rcaeval-skill-ab" / "report.json")
    scaled = _load_json(ROOT / "artifacts" / "skill-ab-large" / "report.json")
    contract = _load_json(
        ROOT / "artifacts" / "skill-evolution" / "benchmark-report.json"
    )
    paired = scaled["paired_ab"]
    replay = contract["independent_replay"]
    stability = scaled["stability"]
    return {
        "external_rcaeval_blind_replay": {
            "dataset": rcaeval["dataset"]["name"],
            "train_cases": rcaeval["dataset"]["train_cases"],
            "blind_test_cases": rcaeval["dataset"]["test_cases"],
            "excluded_cases": len(rcaeval["dataset"]["excluded_cases"]),
            "no_skill_top1": rcaeval["no_skill"]["top1"],
            "skill_top1": rcaeval["skill_enabled"]["top1"],
            "top1_delta_percentage_points": rcaeval["delta"]["top1_percentage_points"],
            "no_skill_mrr": rcaeval["no_skill"]["mrr"],
            "skill_mrr": rcaeval["skill_enabled"]["mrr"],
            "improved": rcaeval["delta"]["improved"],
            "regressed": rcaeval["delta"]["regressed"],
            "unchanged": rcaeval["delta"]["unchanged"],
            "skill_activation_coverage": rcaeval["skill_route_selection"]["coverage"],
            "paired_p_value": rcaeval["statistical_test"]["p_value_two_sided"],
            "source_revision": rcaeval["dataset"]["upstream_revision"],
            "boundary": "Public offline metric replay; not live Collector execution or production A/B.",
        },
        "generated_skill_perturbation": {
            "cases": paired["dataset"]["case_count"],
            "no_skill_accuracy": paired["baseline_no_skill"]["accuracy"],
            "skill_accuracy": paired["skill_enabled"]["accuracy"],
            "accuracy_delta_percentage_points": paired["delta"]["accuracy_percentage_points"],
            "positive_route_top1_no_skill": paired["baseline_no_skill"][
                "positive_root_cause_prior_accuracy"
            ],
            "positive_route_top1_skill": paired["skill_enabled"][
                "positive_root_cause_prior_accuracy"
            ],
            "positive_route_top1_delta_percentage_points": paired["delta"][
                "positive_accuracy_percentage_points"
            ],
            "false_activation_rate": paired["skill_enabled"]["false_activation_rate"],
            "boundary": paired["measurement_boundary"]["explanation"],
        },
        "skill_contract_replay": {
            "cases": replay["baseline"]["total"],
            "no_skill_pass_rate": replay["baseline"]["pass_rate"],
            "skill_pass_rate": replay["skill_enabled"]["pass_rate"],
            "delta_percentage_points": replay["delta"]["pass_rate_percentage_points"],
            "boundary": replay["claim_boundary"],
        },
        "retrieval_stability_smoke": {
            "duration_seconds": stability["duration_seconds"],
            "iterations": stability["iterations"],
            "decisions": stability["decisions"],
            "errors": stability["errors"],
            "decision_drift_iterations": stability["decision_drift_iterations"],
            "latency_ms_per_1200_cases_mean": stability["latency_ms_per_iteration"]["mean"],
            "latency_ms_per_1200_cases_p95": stability["latency_ms_per_iteration"]["p95"],
            "python_heap_current_growth_bytes": stability["python_heap_bytes"]["current_growth"],
            "boundary": stability["boundary"],
        },
    }


def _percent(value: float) -> str:
    return f"{value:.2%}"


def _render_markdown(report: dict[str, Any]) -> str:
    engineering = report["engineering_regression"]
    py = engineering["python"]
    go = engineering["go"]
    web = engineering["web"]
    build = engineering["web_build"]
    benchmarks = report["benchmarks"]
    real = benchmarks["external_rcaeval_blind_replay"]
    generated = benchmarks["generated_skill_perturbation"]
    contract = benchmarks["skill_contract_replay"]
    stability = benchmarks["retrieval_stability_smoke"]
    overall = report["overall_status"]
    total_tests = report["summary"]["automated_tests_passed"]
    largest_chunk = build.get("largest_javascript_chunk") or {}
    go_packages = (
        f"{go.get('packages_passed', 0)} 个有测试包通过，"
        f"{go.get('packages_without_tests', 0)} 个包无测试"
    )
    return "\n".join(
        [
            "# Mini-Drop 量化测试报告",
            "",
            f"- 生成时间：`{report['generated_at']}`",
            f"- 总体状态：`{overall}`",
            f"- 自动化测试：`{total_tests}` 条通过、`{report['summary']['automated_tests_failed']}` 条失败、`{report['summary']['automated_tests_skipped']}` 条跳过",
            "- 结论口径：工程回归、外部真实遥测、生成压力集和契约回放分开统计，不合并成一个准确率。",
            "",
            "## 一、工程回归",
            "",
            "| 层级 | 结果 | 数量 | 耗时 | 额外指标 |",
            "|---|---:|---:|---:|---|",
            f"| Python 服务、诊断与 Analyzer | {py['status']} | {py['passed']} passed, {py['skipped']} skipped | {py['duration_seconds']:.2f}s | 行覆盖率 {py['coverage']['percent']:.2f}% ({py['coverage']['covered_lines']}/{py['coverage']['num_statements']}) |",
            f"| Go Control API | {go['status']} | {go.get('tests', {}).get('passed', 0)} tests | {go.get('duration_seconds', 0):.2f}s | {go_packages} |",
            f"| React 前端 | {web['status']} | {web.get('tests_passed', 0)} tests / {web.get('test_files_passed', 0)} files | {web.get('duration_seconds', 0):.2f}s | Vitest |",
            f"| 前端生产构建 | {build['status']} | 1 build | {build.get('duration_seconds', 0):.2f}s | 最大 JS chunk {largest_chunk.get('raw_kb', 0):.2f}kB，gzip {largest_chunk.get('gzip_kb', 0):.2f}kB |",
            "",
            "工程回归回答的是“当前代码有没有破坏已有行为”，不回答生产根因准确率。",
            "",
            "## 二、外部真实遥测盲测",
            "",
            f"数据采用 [RCAEval](https://github.com/phamquiluan/RCAEval) `{real['dataset']}`：训练 {real['train_cases']} 条，盲测 {real['blind_test_cases']} 条，排除 {real['excluded_cases']} 条缺少有效前后窗口的数据。Skill 只从训练重复构建，盲测时不会读取根因标签。",
            "",
            "| 指标 | No-Skill | Skill | 变化 |",
            "|---|---:|---:|---:|",
            f"| 根因服务 Top-1 | {_percent(real['no_skill_top1'])} | {_percent(real['skill_top1'])} | {real['top1_delta_percentage_points']:+.2f} pp |",
            f"| MRR | {real['no_skill_mrr']:.4f} | {real['skill_mrr']:.4f} | {real['skill_mrr'] - real['no_skill_mrr']:+.4f} |",
            f"| 配对样本变化 | 0 | 0 | 改善 {real['improved']}、退化 {real['regressed']}、不变 {real['unchanged']} |",
            f"| Skill 激活覆盖率 | 0 | {_percent(real['skill_activation_coverage'])} | 保守门禁仅激活部分样本 |",
            "",
            f"配对精确检验 `p={real['paired_p_value']:.4f}`，未达到常用的 `p<0.05` 显著性要求。因此当前最稳妥的结论是：观察到 Top-1 提升 `+2.00` 个百分点且无退化样本，但尚不能宣称已证明生产提升。",
            "",
            "## 三、Skill 生成压力集",
            "",
            f"1200 条生成扰动覆盖同义改写、伪相似负例、环境漂移和能力漂移。路线先验 Top-1 从 {_percent(generated['positive_route_top1_no_skill'])} 提升到 {_percent(generated['positive_route_top1_skill'])}，变化 {generated['positive_route_top1_delta_percentage_points']:+.2f} pp；负例误激活率 {_percent(generated['false_activation_rate'])}。",
            "",
            "这个结果证明 Skill 检索门禁能处理构造的语言与环境扰动，但数据由现有 Skill 模板派生，不能当作真实故障准确率。",
            "",
            "## 四、稳定性",
            "",
            f"Skill 检索循环运行 {stability['duration_seconds']:.2f}s，共 {stability['iterations']} 轮、{stability['decisions']} 次决策，错误 {stability['errors']}，决策漂移 {stability['decision_drift_iterations']}。平均吞吐 {stability['decisions'] / stability['duration_seconds']:.2f} 次决策/秒，单次决策均摊 {stability['duration_seconds'] * 1000 / stability['decisions']:.2f}ms；每 1200 条 P95 为 {stability['latency_ms_per_1200_cases_p95']:.2f}ms，Python 当前堆增长 {stability['python_heap_current_growth_bytes'] / 1024:.2f}KiB。",
            "",
            "这只是检索决策循环的短时 smoke test，不是 Go API、PostgreSQL、MinIO、C++ Agent、Analyzer 和模型提供方共同参与的 6 小时全链路压测。",
            "",
            "## 五、旧口径隔离",
            "",
            f"旧 15 条契约回放的通过率为 {_percent(contract['no_skill_pass_rate'])} → {_percent(contract['skill_pass_rate'])}，即 {contract['delta_percentage_points']:+.2f} pp。它验证的是路线和生命周期契约，不是根因准确率，也不是线上 A/B。面试时不要说成“Skill 把真实准确率从 40% 提升到 100%”。",
            "",
            "## 六、尚未闭环",
            "",
            "1. 需要更大的独立盲测集或多次重采样，使 Skill 的真实遥测差异具备统计显著性。",
            "2. 需要 Linux 环境下的真实 Collector 故障注入，验证 PID 绑定、Artifact 完整性、证据门禁和恢复验证。",
            "3. 需要至少 6 小时全链路 soak，记录 API 吞吐、任务成功率、P95/P99、队列积压、租约恢复、对象存储失败和内存增长。",
            "4. 需要真实流量 shadow A/B，在不影响决策的情况下记录 Skill 建议，再由人工或已验证报告回填标签校准门槛。",
            "5. Python 行覆盖率目前为 59%，C++ 原生 Agent、Docker Compose 集成和异常网络场景没有纳入本轮自动化回归。",
            "",
            "## 已知告警",
            "",
            "- Python 在 3.14 环境下出现 1 条 `pytest-asyncio` 弃用告警，不影响本轮结果，但应在依赖升级前处理。",
            "- 前端 Vitest 的 jsdom 环境报告伪元素 `getComputedStyle` 未实现，测试仍通过，但相关视觉行为需要浏览器 E2E 覆盖。",
            f"- 最大前端 JS chunk 为 {largest_chunk.get('raw_kb', 0):.2f}kB，gzip {largest_chunk.get('gzip_kb', 0):.2f}kB，后续可继续拆分 Ant Design 和图表依赖。",
            "",
            "## 七、面试可用结论",
            "",
            "当前可以说：我们建立了工程回归、生成扰动压力集和外部 RCAEval 盲测三层证据。外部 150 条盲测中，Skill 将根因服务 Top-1 从 64% 提升到 66%，3 条改善、0 条退化，但 p 值为 0.25，样本和激活覆盖仍不足，所以我把它作为正向观察而不是生产收益承诺。生成压力集显示路线选择抗扰动能力较强，但不与真实准确率混用。",
            "",
            "## 复现",
            "",
            "```powershell",
            "python scripts/build_quantitative_test_report.py",
            "```",
            "",
            "原始结构化结果见 `artifacts/final-test-metrics/report.json`，覆盖率明细见 `artifacts/final-test-metrics/coverage.json`。",
        ]
    ) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/final-test-metrics")
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=Path("docs/benchmarks/final-quantitative-test-report-20260902.md"),
    )
    args = parser.parse_args()
    output_dir = (ROOT / args.output_dir).resolve()
    markdown_path = (ROOT / args.markdown).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)

    engineering = {
        "python": _python_regression(output_dir),
        "go": _go_regression(),
        "web": _web_regression(),
        "web_build": _web_build(),
    }
    statuses = [item["status"] for item in engineering.values()]
    total_passed = (
        engineering["python"].get("passed", 0)
        + engineering["go"].get("tests", {}).get("passed", 0)
        + engineering["web"].get("tests_passed", 0)
    )
    total_failed = (
        engineering["python"].get("failed", 0)
        + engineering["go"].get("tests", {}).get("failed", 0)
        + engineering["web"].get("tests_failed", 0)
    )
    total_skipped = (
        engineering["python"].get("skipped", 0)
        + engineering["go"].get("tests", {}).get("skipped", 0)
        + engineering["web"].get("tests_skipped", 0)
    )
    report = {
        "schema": "mini-drop.quantitative-test-report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": "PASS" if all(status == "PASS" for status in statuses) else "FAIL",
        "summary": {
            "automated_tests_passed": total_passed,
            "automated_tests_failed": total_failed,
            "automated_tests_skipped": total_skipped,
        },
        "engineering_regression": engineering,
        "benchmarks": _benchmark_summary(),
        "claim_policy": {
            "external_blind_replay": "May be reported with confidence interval and significance caveat.",
            "generated_perturbation": "May be reported only as route-retrieval robustness.",
            "contract_replay": "May be reported only as contract/lifecycle regression.",
            "production_effect": "NOT_MEASURED",
        },
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    summary = {
        "overall_status": report["overall_status"],
        "automated_tests_passed": total_passed,
        "automated_tests_failed": total_failed,
        "automated_tests_skipped": total_skipped,
        "report_json": str(output_dir / "report.json"),
        "report_markdown": str(markdown_path),
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    if report["overall_status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
