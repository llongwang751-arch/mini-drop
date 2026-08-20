"""Versioned Golden scenarios evaluation with deterministic quality gates."""

from __future__ import annotations

import json
import hashlib
import argparse
from html import escape
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field
from server.app.diagnosis.actions import collect_action, inspect_session_action
from server.app.diagnosis.domain_analyzers import analyze_observations, assess_cluster, cluster_finding
from server.app.diagnosis.knowledge import retrieve_knowledge
from server.app.diagnosis.report_verifier import verify_report


DEFAULT_SCENARIO_ROOT = Path(__file__).resolve().parents[3] / "golden_scenarios"
MANIFEST_NAME = "manifest.json"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoldenExpected(_StrictModel):
    classification: str
    finding_types: list[str] = Field(default_factory=list)
    knowledge_refs: list[str] = Field(default_factory=list)
    action_collectors: list[str] = Field(default_factory=list)


class GoldenScenario(_StrictModel):
    schema_version: str = "1.0"
    scenario_id: str = Field(min_length=3, max_length=128)
    tags: list[str] = Field(default_factory=list)
    query: str = Field(min_length=3)
    scope: dict[str, Any]
    observations: list[dict[str, Any]]
    expected: GoldenExpected


class GoldenThresholds(_StrictModel):
    scenario_pass_rate: float = Field(ge=0, le=1)
    classification_accuracy: float = Field(ge=0, le=1)
    evidence_reference_integrity: float = Field(ge=0, le=1)
    max_unsafe_auto_execute_count: int = Field(ge=0)
    min_falsification_plan_rate: float = Field(ge=0, le=1)


class GoldenManifest(_StrictModel):
    dataset: str
    version: str
    scenario_schema_version: str
    required_scenarios: list[str]
    thresholds: GoldenThresholds


def load_scenarios(root: Path | None = None) -> list[dict[str, Any]]:
    root = root or DEFAULT_SCENARIO_ROOT
    scenarios: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        if path.name == MANIFEST_NAME:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        scenarios.append(GoldenScenario.model_validate(payload).model_dump(mode="json"))
    return scenarios


def load_manifest(root: Path | None = None) -> GoldenManifest:
    root = root or DEFAULT_SCENARIO_ROOT
    return GoldenManifest.model_validate_json(
        (root / MANIFEST_NAME).read_text(encoding="utf-8")
    )


def evaluate_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    observations = scenario["observations"]
    findings = analyze_observations(observations)
    assessment = assess_cluster(scenario["scope"], observations)
    findings.append(cluster_finding(assessment))
    knowledge = retrieve_knowledge(scenario.get("query", ""), findings)
    knowledge_refs = [item["knowledge_id"] for item in knowledge]
    actions = _evaluation_actions(scenario["scenario_id"], observations, assessment)
    evidence_by_ref = {
        ref: {
            "evidence_id": ref,
            "target": dict(obs.get("target", {})),
            "evidence_role": "incident",
            "data_quality": {
                "completeness": "high",
                "domains": _observation_domains(obs),
            },
        }
        for obs in observations
        for ref in obs.get("evidence_refs", [])
    }
    evidence_refs = sorted(evidence_by_ref)
    evidence = [evidence_by_ref[ref] for ref in evidence_refs]
    conclusion = {
        "summary": assessment["summary"],
        "cluster_assessment": assessment,
        "root_location": assessment["root_location"],
        "domain_cause": assessment["domain_cause"],
        "findings": findings,
        "knowledge_refs": knowledge_refs,
        "knowledge_context": knowledge,
        "actions": actions,
        "limitations": [],
        "coverage": {"observation_count": len(observations), "evidence_count": len(evidence)},
    }
    verification = verify_report(conclusion, evidence, scenario["scope"])

    expected = scenario["expected"]
    actual_finding_types = {item["finding_type"] for item in findings}
    actual_collectors = {item.get("collector_type") for item in actions if item.get("collector_type")}
    checks = {
        "classification": assessment["classification"] == expected["classification"],
        "finding_types": set(expected.get("finding_types", [])).issubset(actual_finding_types),
        "knowledge_refs": set(expected.get("knowledge_refs", [])).issubset(set(knowledge_refs)),
        "action_collectors": set(expected.get("action_collectors", [])).issubset(actual_collectors),
        "report_verification": verification["status"] == "passed",
        "no_auto_execute": all(item.get("auto_execute") is False for item in actions),
        "falsification_plan": any(
            item.get("evidence_purpose") == "FALSIFY"
            for item in actions
            if item.get("action_type") == "collect"
        ),
    }
    return {
        "scenario_id": scenario["scenario_id"],
        "passed": all(checks.values()),
        "checks": checks,
        "expected": expected,
        "actual": {
            "classification": assessment["classification"],
            "confidence_level": assessment["confidence_level"],
            "finding_types": sorted(actual_finding_types),
            "knowledge_refs": knowledge_refs,
            "action_collectors": sorted(actual_collectors),
            "verification": verification,
        },
    }


ProgressCallback = Callable[[dict[str, Any]], None]


def run_evaluation(
    root: Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(root)
    scenarios = load_scenarios(root)
    scenario_ids = {item["scenario_id"] for item in scenarios}
    missing = sorted(set(manifest.required_scenarios) - scenario_ids)
    if missing:
        raise ValueError(f"Golden 数据集缺少必需场景: {missing}")
    total_scenarios = len(scenarios)
    if progress_callback:
        progress_callback({
            "stage": "SUITE_LOADED",
            "message": f"已载入 {total_scenarios} 个 Golden 场景及其标准答案（Oracle）",
            "completed": 0,
            "total": total_scenarios,
        })

    results: list[dict[str, Any]] = []
    for index, scenario in enumerate(scenarios, start=1):
        if progress_callback:
            progress_callback({
                "stage": "SCENARIO_STARTED",
                "message": f"开始诊断场景 {scenario['scenario_id']}",
                "scenario_id": scenario["scenario_id"],
                "query": scenario.get("query", ""),
                "tags": scenario.get("tags", []),
                "expected": scenario.get("expected", {}),
                "index": index,
                "completed": index - 1,
                "total": total_scenarios,
            })
        result = evaluate_scenario(scenario)
        results.append(result)
        if progress_callback:
            progress_callback({
                "stage": "DECISION_BRANCH_EVALUATED",
                "message": f"决策树完成分类：{result['actual']['classification']}",
                "scenario_id": scenario["scenario_id"],
                "actual": result["actual"],
                "index": index,
                "completed": index - 1,
                "total": total_scenarios,
            })
            progress_callback({
                "stage": "EVIDENCE_AND_FALSIFICATION_CHECKED",
                "message": "已核验真实证据引用、反证计划与高风险操作门禁",
                "scenario_id": scenario["scenario_id"],
                "checks": result["checks"],
                "index": index,
                "completed": index - 1,
                "total": total_scenarios,
            })
            progress_callback({
                "stage": "SCENARIO_COMPLETED",
                "message": f"场景 {scenario['scenario_id']} {'通过' if result['passed'] else '失败'}",
                "scenario_id": scenario["scenario_id"],
                "result": result,
                "index": index,
                "completed": index,
                "total": total_scenarios,
            })
    total = len(results)
    passed = sum(1 for item in results if item["passed"])
    classification_hits = sum(1 for item in results if item["checks"]["classification"])
    metrics = {
        "scenario_pass_rate": round(passed / total, 4) if total else 0,
        "classification_accuracy": round(classification_hits / total, 4) if total else 0,
        "evidence_reference_integrity": round(
            sum(1 for item in results if item["checks"]["report_verification"]) / total, 4,
        ) if total else 0,
        "unsafe_auto_execute_count": sum(
            1 for item in results if not item["checks"]["no_auto_execute"]
        ),
        "falsification_plan_rate": round(
            sum(1 for item in results if item["checks"]["falsification_plan"]) / total,
            4,
        ) if total else 0,
        # “分析覆盖率”不是模型调用次数，而是每个场景是否完整走过
        # 分类、证据校验、反证规划三段。这样不会把“调用过 LLM”误报成
        # “完成了诊断分析”。
        "diagnostic_analysis_coverage": round(
            sum(
                1
                for item in results
                if item["actual"].get("classification")
                and item["checks"].get("report_verification")
                and item["checks"].get("falsification_plan")
            ) / total,
            4,
        ) if total else 0,
    }
    thresholds = manifest.thresholds.model_dump(mode="json")
    gate_checks = {
        "scenario_pass_rate": metrics["scenario_pass_rate"] >= thresholds["scenario_pass_rate"],
        "classification_accuracy": metrics["classification_accuracy"] >= thresholds["classification_accuracy"],
        "evidence_reference_integrity": (
            metrics["evidence_reference_integrity"]
            >= thresholds["evidence_reference_integrity"]
        ),
        "unsafe_auto_execute_count": (
            metrics["unsafe_auto_execute_count"]
            <= thresholds["max_unsafe_auto_execute_count"]
        ),
        "falsification_plan_rate": (
            metrics["falsification_plan_rate"]
            >= thresholds["min_falsification_plan_rate"]
        ),
    }
    dataset_fingerprint = _dataset_fingerprint(manifest, scenarios)
    report = {
        "suite": manifest.dataset,
        "dataset_version": manifest.version,
        "dataset_fingerprint": dataset_fingerprint,
        "gate_status": "PASSED" if all(gate_checks.values()) else "FAILED",
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "metrics": metrics,
        "thresholds": thresholds,
        "gate_checks": gate_checks,
        "results": results,
    }
    if progress_callback:
        progress_callback({
            "stage": "GATE_COMPLETED",
            "message": f"质量门禁计算完成：{report['gate_status']}",
            "completed": total_scenarios,
            "total": total_scenarios,
            "gate_status": report["gate_status"],
        })
    return report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Mini-Drop Diagnosis Golden Evaluation",
        "",
        f"- Scenarios: {report['total']}",
        f"- Passed: {report['passed']}",
        f"- Classification accuracy: {report['metrics']['classification_accuracy']:.2%}",
        f"- Evidence reference integrity: {report['metrics']['evidence_reference_integrity']:.2%}",
        f"- Unsafe auto execute: {report['metrics']['unsafe_auto_execute_count']}",
        f"- Dataset: {report['suite']}@{report['dataset_version']}",
        f"- Fingerprint: `{report['dataset_fingerprint']}`",
        f"- Quality gate: **{report['gate_status']}**",
        f"- Falsification plan rate: {report['metrics']['falsification_plan_rate']:.2%}",
        f"- Diagnostic analysis coverage: {report['metrics']['diagnostic_analysis_coverage']:.2%}",
        "",
        "| Scenario | Result | Classification | Verification |",
        "|---|---|---|---|",
    ]
    for item in report["results"]:
        lines.append(
            f"| {item['scenario_id']} | {'PASS' if item['passed'] else 'FAIL'} | "
            f"{item['actual']['classification']} | {item['actual']['verification']['status']} |"
        )
    return "\n".join(lines) + "\n"


def render_html(report: dict[str, Any]) -> str:
    """Render a standalone, reviewer-friendly evaluation report.

    The report exposes every scenario's expected answer, actual decision and
    individual checks. It is intentionally dependency-free so a clean Linux
    host can generate and open it without a frontend build.
    """

    def pct(value: Any) -> str:
        return f"{float(value or 0):.1%}"

    metric_cards = [
        ("场景通过率", pct(report["metrics"]["scenario_pass_rate"])),
        ("根因分类准确率", pct(report["metrics"]["classification_accuracy"])),
        ("证据引用完整率", pct(report["metrics"]["evidence_reference_integrity"])),
        ("反证计划覆盖率", pct(report["metrics"]["falsification_plan_rate"])),
        ("诊断分析覆盖率", pct(report["metrics"]["diagnostic_analysis_coverage"])),
        ("不安全自动执行", str(report["metrics"]["unsafe_auto_execute_count"])),
    ]
    cards = "".join(
        f'<section class="metric"><span>{escape(label)}</span><strong>{escape(value)}</strong></section>'
        for label, value in metric_cards
    )
    rows = []
    for item in report["results"]:
        checks = "".join(
            f'<li class="{ "ok" if passed else "bad" }">'
            f'{escape(name)}：{ "通过" if passed else "失败" }</li>'
            for name, passed in item["checks"].items()
        )
        expected = escape(json.dumps(item["expected"], ensure_ascii=False, indent=2))
        actual = escape(json.dumps(item["actual"], ensure_ascii=False, indent=2))
        rows.append(
            '<details class="scenario"{}>'.format(" open" if not item["passed"] else "")
            + f'<summary><b>{escape(item["scenario_id"])}</b>'
            + f'<em class="{ "pass" if item["passed"] else "fail" }">'
            + ("PASS" if item["passed"] else "FAIL")
            + '</em></summary>'
            + f'<div class="checks"><h3>逐项判定</h3><ul>{checks}</ul></div>'
            + f'<div class="compare"><div><h3>标准答案（Oracle）</h3><pre>{expected}</pre></div>'
            + f'<div><h3>系统输出</h3><pre>{actual}</pre></div></div></details>'
        )
    gate_class = "pass" if report["gate_status"] == "PASSED" else "fail"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mini-Drop 统一诊断评测报告</title><style>
body{{font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;margin:0;background:#f5f7fa;color:#172033}}
main{{max-width:1180px;margin:32px auto;padding:0 20px 48px}}header,.scenario{{background:#fff;border:1px solid #e5e9f0;border-radius:12px;margin-bottom:14px}}
header{{padding:24px}}h1{{margin:0 0 8px}}.meta{{color:#667085;font-size:14px}}.gate{{float:right;padding:6px 12px;border-radius:999px;font-weight:700}}
.pass{{color:#067647;background:#ecfdf3}}.fail,.bad{{color:#b42318;background:#fef3f2}}.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-top:20px}}
.metric{{padding:14px;background:#f8fafc;border-radius:10px}}.metric span{{display:block;color:#667085;font-size:13px}}.metric strong{{display:block;font-size:24px;margin-top:5px}}
.scenario summary{{cursor:pointer;padding:16px 18px;display:flex;justify-content:space-between;align-items:center}}.scenario summary em{{font-style:normal;padding:4px 9px;border-radius:999px}}
.checks,.compare{{padding:0 18px 18px}}ul{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;padding:0;list-style:none}}li{{padding:8px;border-radius:7px}}li.ok{{color:#067647;background:#ecfdf3}}
.compare{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}pre{{white-space:pre-wrap;word-break:break-word;background:#0b1220;color:#d6e0f0;padding:14px;border-radius:9px;max-height:420px;overflow:auto}}
@media(max-width:760px){{.compare{{grid-template-columns:1fr}}.gate{{float:none;display:inline-block;margin-bottom:10px}}}}
</style></head><body><main><header><span class="gate {gate_class}">{escape(report['gate_status'])}</span>
<h1>Mini-Drop 统一诊断评测报告</h1><div class="meta">数据集 {escape(report['suite'])}@{escape(report['dataset_version'])} · 场景 {report['total']} · 指纹 {escape(report['dataset_fingerprint'][:16])}…</div>
<div class="metrics">{cards}</div></header>{''.join(rows)}</main></body></html>"""


def _dataset_fingerprint(
    manifest: GoldenManifest,
    scenarios: list[dict[str, Any]],
) -> str:
    payload = {
        "manifest": manifest.model_dump(mode="json"),
        "scenarios": sorted(scenarios, key=lambda item: item["scenario_id"]),
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _evaluation_actions(
    diagnosis_id: str,
    observations: list[dict[str, Any]],
    assessment: dict[str, Any],
) -> list[dict[str, Any]]:
    actions = [inspect_session_action(diagnosis_id, assessment.get("evidence_refs", []))]
    if not observations:
        return actions
    target = observations[0]["target"]
    refs = observations[0].get("evidence_refs", [])
    actions.append(collect_action(
        action_id="act_low_risk_metrics", title="补充低风险系统指标",
        collector_type="sys_metrics", target=target, duration_sec=15, sample_rate=11,
        comment="复核当前判断。", risk_level="R1", evidence_refs=refs, confidence_level="高",
        evidence_purpose="VERIFY",
    ))
    if assessment["classification"] in {"self_code_or_process_pressure", "insufficient_evidence"}:
        actions.append(collect_action(
            action_id="act_cpu_profile", title="申请一次 CPU Profile",
            collector_type="perf_cpu", target=target, duration_sec=15, sample_rate=49,
            comment="需要单次人工审批。", risk_level="R2", evidence_refs=refs, confidence_level="中",
            evidence_purpose="FALSIFY",
        ))
    if assessment["classification"] in {"same_host_noisy_neighbor", "host_resource_contention", "insufficient_evidence"}:
        actions.append(collect_action(
            action_id="act_io_latency", title="申请一次 I/O 延迟探针",
            collector_type="ebpf_io", target=target, duration_sec=15, sample_rate=11,
            comment="需要单次人工审批。", risk_level="R2",
            evidence_refs=assessment.get("evidence_refs", []), confidence_level="中",
            evidence_purpose="FALSIFY",
        ))
    if (
        assessment["classification"] in {"same_host_noisy_neighbor", "downstream_dependency"}
        and len(observations) > 1
    ):
        comparison = observations[1]
        actions.append(collect_action(
            action_id="act_cross_target_counter",
            title="采集对照目标指标以证伪跨节点归因",
            collector_type="sys_metrics",
            target=comparison["target"],
            duration_sec=15,
            sample_rate=11,
            comment="对照目标若未同步异常，则当前跨节点根因假设需要降级。",
            risk_level="R1",
            evidence_refs=comparison.get("evidence_refs", []),
            confidence_level="中",
            evidence_purpose="FALSIFY",
        ))
    return actions


def _observation_domains(observation: dict[str, Any]) -> list[str]:
    collector = observation.get("collector_type")
    if collector == "database_metrics":
        return ["dependency"]
    if collector in {"perf_cpu", "jvm_metrics"}:
        return ["host", "process"]
    if collector == "network_metrics":
        return ["host"]
    return ["host", "process", "container"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Mini-Drop Golden evaluation")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--format", choices=("json", "markdown", "html"), default="json")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = run_evaluation(args.root)
    if args.format == "markdown":
        content = render_markdown(report)
    elif args.format == "html":
        content = render_html(report)
    else:
        content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(content.encode("utf-8"))
    else:
        print(content, end="")
    raise SystemExit(0 if report["gate_status"] == "PASSED" else 1)


if __name__ == "__main__":
    main()
