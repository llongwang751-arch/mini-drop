"""Shared diagnosis benchmark planning, scoring and reporting.

The runner deliberately separates three objects:

* planner input: problem statement and observable runtime scope;
* diagnosis output: hypotheses, evidence snapshots and conclusion;
* oracle: expected answer, loaded only by this evaluator after completion.

This prevents an LLM from seeing the answer while still producing deterministic
metrics that can be compared across diagnosis strategies.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from html import escape
import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from server.app.diagnosis.benchmark_cases import (
    load_benchmark_case,
    load_benchmark_cases,
    score_diagnosis_detail,
)
from server.app.diagnosis.benchmark_catalog import load_benchmark_catalog


STRATEGIES = ("CONSTRAINED_HYBRID", "DECISION_TREE", "EXPLORATORY")
CAMPAIGN_TERMINAL_STATUSES = {
    "COMPLETED",
    "INSUFFICIENT_EVIDENCE",
    "PARTIAL_COMPLETED",
    "BUDGET_EXHAUSTED",
    "TOPOLOGY_UNAVAILABLE",
    "USER_CANCELED",
    "FAILED",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BenchmarkSubmission(_StrictModel):
    case_id: str
    strategy: str
    repetition: int = Field(ge=1)
    diagnosis_detail: dict[str, Any]


def scoring_detail_from_api(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only evaluator inputs and remove API-side oracle metadata.

    The online API legitimately stores an evaluation oracle alongside a
    session, but formal campaign submissions must not carry it.  Persisting a
    compact scoring view also prevents unrelated audit/event growth from
    making the 90-run artifact unnecessarily large.
    """

    detail = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return {
        key: detail[key]
        for key in (
            "diagnosis_id",
            "status",
            "normalized_intent",
            "latest_conclusion",
            "evidence",
            "evidence_snapshots",
        )
        if key in detail
    }


def upsert_submission(
    path: Path,
    submission: dict[str, Any] | BenchmarkSubmission,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Atomically append one unique campaign execution to a resumable file."""

    item = (
        submission
        if isinstance(submission, BenchmarkSubmission)
        else BenchmarkSubmission.model_validate(submission)
    )
    status = item.diagnosis_detail.get("status")
    if status not in CAMPAIGN_TERMINAL_STATUSES:
        raise ValueError(
            "benchmark submission must be terminal: "
            f"status={status!r}"
        )
    if not isinstance(item.diagnosis_detail.get("latest_conclusion"), dict):
        raise ValueError("benchmark submission must contain latest_conclusion")
    current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    validated = [BenchmarkSubmission.model_validate(raw) for raw in current]
    execution_id = f"{item.case_id}:{item.strategy}:{item.repetition}"
    indexes = {
        f"{value.case_id}:{value.strategy}:{value.repetition}": index
        for index, value in enumerate(validated)
    }
    if execution_id in indexes and not overwrite:
        raise ValueError(f"benchmark execution already recorded: {execution_id}")
    payload = item.model_dump(mode="json")
    if execution_id in indexes:
        current[indexes[execution_id]] = payload
        action = "replaced"
    else:
        current.append(payload)
        action = "inserted"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(current, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return {
        "execution_id": execution_id,
        "action": action,
        "recorded": len(current),
        "remaining": build_run_plan()["execution_count"] - len(current),
        "path": str(path.resolve()),
    }


def campaign_progress(submissions: list[dict[str, Any] | BenchmarkSubmission]) -> dict[str, Any]:
    """Return actionable progress without relaxing the formal 90-run gate."""

    plan = build_run_plan()
    expected = {item["execution_id"] for item in plan["executions"]}
    observed = []
    for raw in submissions:
        item = raw if isinstance(raw, BenchmarkSubmission) else BenchmarkSubmission.model_validate(raw)
        observed.append(f"{item.case_id}:{item.strategy}:{item.repetition}")
    observed_set = set(observed)
    duplicates = sorted({item for item in observed if observed.count(item) > 1})
    return {
        "expected": len(expected),
        "recorded": len(observed),
        "unique_recorded": len(observed_set & expected),
        "remaining": len(expected - observed_set),
        "complete": observed_set == expected and not duplicates and len(observed) == len(expected),
        "duplicates": duplicates,
        "unexpected": sorted(observed_set - expected),
        "missing_execution_ids": sorted(expected - observed_set),
    }


def build_run_plan(
    *,
    strategies: list[str] | None = None,
    repetitions: int | None = None,
) -> dict[str, Any]:
    catalog = load_benchmark_catalog()
    cases = load_benchmark_cases()
    selected = strategies or list(STRATEGIES)
    unknown = sorted(set(selected) - set(STRATEGIES))
    if unknown:
        raise ValueError(f"unsupported diagnosis strategies: {unknown}")
    minimum = int(catalog["policy"]["minimum_repetitions"])
    repeat_count = repetitions or minimum
    if repeat_count < minimum:
        raise ValueError(
            f"benchmark requires at least {minimum} repetitions per case and strategy"
        )

    executions = []
    for strategy in selected:
        for case in cases:
            for repetition in range(1, repeat_count + 1):
                executions.append({
                    "execution_id": f"{case['case_id']}:{strategy}:{repetition}",
                    "case_id": case["case_id"],
                    "strategy": strategy,
                    "repetition": repetition,
                    # Only diagnosis-safe data is emitted into the run plan.
                    "planner_input": {
                        "query": case["query"],
                        "trigger": case["trigger"],
                        "topology": case["topology"],
                        "evidence_plan": case["evidence_plan"],
                    },
                    "timeout_seconds": case["execution"]["timeout_seconds"],
                })
    fingerprint_payload = json.dumps(
        executions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return {
        "dataset": catalog["dataset"],
        "dataset_version": catalog["version"],
        "strategies": selected,
        "case_count": len(cases),
        "repetitions": repeat_count,
        "execution_count": len(executions),
        "oracle_in_planner_input": False,
        "plan_fingerprint": hashlib.sha256(
            fingerprint_payload.encode("utf-8")
        ).hexdigest(),
        "executions": executions,
    }


def evaluate_submissions(
    submissions: list[dict[str, Any] | BenchmarkSubmission],
    *,
    require_complete: bool = False,
) -> dict[str, Any]:
    if require_complete:
        validate_campaign_completeness(submissions)
    scored: list[dict[str, Any]] = []
    for raw in submissions:
        submission = (
            raw if isinstance(raw, BenchmarkSubmission)
            else BenchmarkSubmission.model_validate(raw)
        )
        if submission.strategy not in STRATEGIES:
            raise ValueError(f"unsupported diagnosis strategy: {submission.strategy}")
        detail = dict(submission.diagnosis_detail)
        normalized = dict(detail.get("normalized_intent") or {})
        normalized["analysis_strategy"] = submission.strategy
        detail["normalized_intent"] = normalized
        score = score_diagnosis_detail(
            load_benchmark_case(submission.case_id),
            detail,
        )
        score["repetition"] = submission.repetition
        scored.append(score)

    by_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in scored:
        by_strategy[item["strategy"]].append(item)

    strategy_metrics = {}
    for strategy, items in sorted(by_strategy.items()):
        count = len(items)
        strategy_metrics[strategy] = {
            "execution_count": count,
            "average_score_pct": _average(items, "score_pct"),
            "root_cause_exact_match_rate": _rate(items, "root_cause_exact_match"),
            "evidence_integrity_rate": _rate(items, "evidence_integrity"),
            "unsupported_claim_rate": _rate(items, "unsupported_claim"),
            "average_snapshot_role_coverage_pct": _average(
                items, "snapshot_role_coverage_pct"
            ),
            "average_required_evidence_coverage_pct": _average(
                items, "required_evidence_coverage_pct"
            ),
            "completion_calibration_rate": _rate(items, "completion_calibrated"),
            "analysis_output_coverage_rate": round(
                sum(1 for item in items if _has_diagnostic_analysis(item)) / count,
                4,
            ),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "result_count": len(scored),
        "oracle_isolated": all(item["oracle_isolated"] for item in scored),
        "strategy_metrics": strategy_metrics,
        "results": scored,
    }


def validate_campaign_completeness(
    submissions: list[dict[str, Any] | BenchmarkSubmission],
) -> dict[str, Any]:
    """Require exactly one output for every execution in the 90-run plan."""

    expected = {
        execution["execution_id"] for execution in build_run_plan()["executions"]
    }
    observed: list[str] = []
    for raw in submissions:
        item = raw if isinstance(raw, BenchmarkSubmission) else BenchmarkSubmission.model_validate(raw)
        observed.append(f"{item.case_id}:{item.strategy}:{item.repetition}")
    duplicates = sorted({item for item in observed if observed.count(item) > 1})
    observed_set = set(observed)
    missing = sorted(expected - observed_set)
    unexpected = sorted(observed_set - expected)
    if duplicates or missing or unexpected:
        raise ValueError(
            "benchmark campaign is incomplete: "
            f"missing={len(missing)}, duplicates={len(duplicates)}, "
            f"unexpected={len(unexpected)}"
        )
    return {
        "expected": len(expected),
        "observed": len(observed),
        "complete": True,
    }


def write_report(report: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def render_html_report(report: dict[str, Any]) -> str:
    """Render campaign strategy comparison and per-run scoring as HTML."""

    headers = [
        "策略", "执行数", "平均分", "根因精确命中", "证据完整", "反校准完成",
        "分析输出覆盖", "无依据断言",
    ]
    strategy_rows = []
    for strategy, metric in report.get("strategy_metrics", {}).items():
        strategy_rows.append(
            "<tr>"
            f"<td><b>{escape(strategy)}</b></td>"
            f"<td>{metric['execution_count']}</td>"
            f"<td>{metric['average_score_pct']:.1f}</td>"
            f"<td>{metric['root_cause_exact_match_rate']:.1%}</td>"
            f"<td>{metric['evidence_integrity_rate']:.1%}</td>"
            f"<td>{metric['completion_calibration_rate']:.1%}</td>"
            f"<td>{metric['analysis_output_coverage_rate']:.1%}</td>"
            f"<td>{metric['unsupported_claim_rate']:.1%}</td></tr>"
        )
    result_rows = []
    for item in report.get("results", []):
        missing = ", ".join(item.get("missing_required_evidence", [])) or "无"
        result_rows.append(
            "<tr>"
            f"<td>{escape(str(item.get('case_id', '')))}</td>"
            f"<td>{escape(str(item.get('strategy', '')))}</td>"
            f"<td>{item.get('repetition', '-')}</td>"
            f"<td>{float(item.get('score_pct', 0)):.1f}</td>"
            f"<td>{'是' if item.get('root_cause_exact_match') else '否'}</td>"
            f"<td>{escape(missing)}</td></tr>"
        )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Mini-Drop 90 次策略评测</title>
<style>body{{font-family:system-ui,"Microsoft YaHei",sans-serif;background:#f5f7fa;color:#172033;margin:0}}main{{max-width:1280px;margin:30px auto;padding:0 20px}}section{{background:white;border:1px solid #e4e7ec;border-radius:12px;padding:20px;margin:14px 0}}table{{width:100%;border-collapse:collapse}}th,td{{padding:11px;border-bottom:1px solid #eaecf0;text-align:left}}th{{background:#f8fafc}}.ok{{color:#067647}}.warn{{color:#b54708}}.scroll{{overflow:auto}}code{{background:#f2f4f7;padding:2px 5px;border-radius:4px}}</style></head>
<body><main><h1>Mini-Drop 90 次诊断策略评测</h1>
<p>共 <b>{report.get('result_count', 0)}</b> 次执行；Oracle 隔离：<b class="{'ok' if report.get('oracle_isolated') else 'warn'}">{'是' if report.get('oracle_isolated') else '否'}</b>。评分发生在诊断结束后，标准答案不会进入规划器。</p>
<section><h2>策略对比</h2><div class="scroll"><table><thead><tr>{''.join(f'<th>{h}</th>' for h in headers)}</tr></thead><tbody>{''.join(strategy_rows)}</tbody></table></div></section>
<section><h2>逐次执行明细</h2><p>这里公开每次评分及缺失证据，避免只展示最终百分比。</p><div class="scroll"><table><thead><tr><th>场景</th><th>策略</th><th>重复轮次</th><th>得分</th><th>根因命中</th><th>缺失证据</th></tr></thead><tbody>{''.join(result_rows)}</tbody></table></div></section>
</main></body></html>"""


def _has_diagnostic_analysis(item: dict[str, Any]) -> bool:
    checks = item.get("checks") or []
    relevant = {
        check.get("name"): check.get("actual")
        for check in checks
        if check.get("name") in {"location_type", "domain_type", "classification"}
    }
    return bool(relevant) and all(value not in (None, "") for value in relevant.values())


def _rate(items: list[dict[str, Any]], field: str) -> float:
    return round(sum(bool(item[field]) for item in items) / len(items), 4)


def _average(items: list[dict[str, Any]], field: str) -> float:
    return round(sum(float(item[field]) for item in items) / len(items), 2)
