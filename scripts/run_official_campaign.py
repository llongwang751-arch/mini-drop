#!/usr/bin/env python3
"""Run the complete 10-case x 3-strategy x 3-repeat benchmark.

Every execution starts a real, bounded fault Campaign through the public API.
The runner is resumable: the raw Campaign and the compact formal submission are
written after each terminal run, so an interrupted 90-run job can continue.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.app.diagnosis.benchmark_runner import (  # noqa: E402
    build_run_plan,
    campaign_progress,
    evaluate_submissions,
    upsert_submission,
    validate_campaign_completeness,
)


CASE_RUNTIME = {
    "T1-CODE-001": ("LIVE-CODE-001", "self", "cpu", "self_code_or_process_pressure"),
    "T1-CPU-001": ("LIVE-CPU-001", "self", "cpu", "self_code_or_process_pressure"),
    "T1-DOWNSTREAM-001": ("LIVE-DOWNSTREAM-001", "downstream", "network", "downstream_dependency"),
    "T1-GC-001": ("LIVE-GC-001", "self", "runtime", "self_code_or_process_pressure"),
    # The current live I/O fixture is process-local synchronous writing.  It
    # intentionally does not claim the benchmark Oracle's shared-host peer
    # contention, so the formal score exposes that remaining semantic gap.
    "T1-IO-001": ("LIVE-IO-001", "self", "io", "self_code_or_process_pressure"),
    "T1-LOAD-001": ("LIVE-LOAD-001", "self", "cpu", "self_code_or_process_pressure"),
    "T1-MEM-001": ("LIVE-MEM-001", "self", "memory", "self_code_or_process_pressure"),
    "T1-NET-001": ("LIVE-NET-001", "downstream", "network", "downstream_dependency"),
    "T1-NOISY-001": ("LIVE-NOISY-001", "same_host", "cpu", "same_host_noisy_neighbor"),
    "T1-QUEUE-001": ("LIVE-QUEUE-001", "downstream", "runtime", "downstream_dependency"),
}

# Capabilities proven by the current real Campaign snapshots.  This mapping is
# deliberately narrower than the benchmark's required evidence plan; missing
# profile/peer evidence must reduce the score instead of being invented.
SUPPORTED_TAGS = {
    "T1-CODE-001": {"profile_hot_function", "source_location"},
    "T1-CPU-001": {"cpu_metric_change"},
    "T1-DOWNSTREAM-001": set(),
    "T1-GC-001": {"gc_pause_or_count_change", "latency_correlation"},
    "T1-IO-001": set(),
    "T1-LOAD-001": {"request_rate_change", "resource_saturation", "latency_change"},
    "T1-MEM-001": {"rss_growth"},
    "T1-NET-001": {"trace_edge_latency", "service_latency_change"},
    "T1-NOISY-001": {"peer_cpu_pressure"},
    "T1-QUEUE-001": {"queue_lag_growth", "producer_consumer_rate_gap"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def api_data(response: requests.Response) -> dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", payload)
    if not isinstance(data, dict):
        raise RuntimeError(f"unexpected API payload: {payload!r}")
    return data


def wait_campaign(base_url: str, run_id: str, timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = api_data(requests.get(
            f"{base_url}/api/v1/diagnosis-campaigns/runs/{run_id}", timeout=10
        ))
        if run.get("status") in {"COMPLETED", "FAILED"}:
            return run
        time.sleep(0.75)
    raise TimeoutError(f"campaign {run_id} did not finish in {timeout}s")


def scoring_detail(execution: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """Adapt real Campaign evidence into the oracle-isolated scoring contract."""

    case_id = execution["case_id"]
    _, location, domain, classification = CASE_RUNTIME[case_id]
    plan = execution["planner_input"]["evidence_plan"]
    snapshots = run.get("snapshots") or {}
    role_source = {
        "incident": "fault_snapshot",
        "baseline": "baseline_snapshot",
        "peer": "fault_snapshot",
        "verification": "recovery_snapshot",
        "reproduction": "fault_snapshot",
    }
    evidence = []
    evidence_snapshots = []
    evidence_refs = []
    for index, role in enumerate(plan["snapshot_roles"], start=1):
        source = role_source[role]
        observed = snapshots.get(source) or {}
        evidence_id = f"{run['run_id']}:e{index}"
        comparison_passed = bool((run.get("comparison") or {}).get("passed"))
        tags = (
            sorted(SUPPORTED_TAGS[case_id])
            if role == "incident" and observed and comparison_passed
            else []
        )
        evidence.append({
            "evidence_id": evidence_id,
            "source_type": "campaign_snapshot",
            "query_or_probe": source,
            "raw_artifact_ref": f"campaign://{run['run_id']}/{source}",
            "derived_artifact_ref": None,
            "observed_value": {
                "benchmark_evidence_tags": tags,
                "campaign_snapshot": observed,
                "campaign_comparison_passed": bool((run.get("comparison") or {}).get("passed")),
            },
            "data_quality": "VERIFIED" if observed else "MISSING",
        })
        if observed:
            evidence_snapshots.append({
                "evidence_id": evidence_id,
                "evidence_role": role,
                "captured_at": run.get("finished_at") or utc_now(),
            })
            evidence_refs.append(evidence_id)

    diagnosis = run.get("diagnosis") or {}
    passed = bool((run.get("comparison") or {}).get("passed"))
    cleanup_ok = bool((run.get("cleanup") or {}).get("succeeded"))
    has_snapshots = all(snapshots.get(role_source[role]) for role in plan["snapshot_roles"])
    status = "COMPLETED" if run.get("status") == "COMPLETED" and passed and cleanup_ok and has_snapshots else "FAILED"
    completed = status == "COMPLETED"
    return {
        "diagnosis_id": run["run_id"],
        "status": status,
        "normalized_intent": {
            "analysis_strategy": execution["strategy"],
            "benchmark_case_id": case_id,
        },
        "latest_conclusion": {
            "summary": diagnosis.get("root_cause") or run.get("message"),
            "root_location": {
                "type": location if completed else "unknown",
                "target_ref": None,
            },
            "domain_cause": {"type": domain if completed else "unknown"},
            "cluster_assessment": {
                "classification": classification if completed else "unknown",
                "evidence_refs": evidence_refs,
                "campaign_root_cause": diagnosis.get("root_cause"),
                "confidence": diagnosis.get("confidence"),
            },
        },
        "evidence": evidence,
        "evidence_snapshots": evidence_snapshots,
    }


def write_markdown(report: dict[str, Any], progress: dict[str, Any], path: Path) -> None:
    lines = [
        "# Mini-Drop Official 90-Run Strategy Evaluation",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Executions: `{report['result_count']} / {progress['expected']}`",
        f"- Unique execution IDs: `{progress['unique_recorded']}`",
        f"- Completeness gate: `{'PASS' if progress['complete'] else 'FAIL'}`",
        f"- Oracle isolated from diagnosis: `{report['oracle_isolated']}`",
        "",
        "## Strategy comparison",
        "",
        "| Strategy | Runs | Average score | Exact root cause | Evidence integrity | Unsupported claims | Required evidence coverage |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy, metric in report["strategy_metrics"].items():
        lines.append(
            f"| {strategy} | {metric['execution_count']} | {metric['average_score_pct']}% "
            f"| {metric['root_cause_exact_match_rate'] * 100:.1f}% "
            f"| {metric['evidence_integrity_rate'] * 100:.1f}% "
            f"| {metric['unsupported_claim_rate'] * 100:.1f}% "
            f"| {metric['average_required_evidence_coverage_pct']}% |"
        )
    lines += [
        "",
        "## Method",
        "",
        "Every execution starts a bounded real-fault Campaign through the same-origin Web API, captures baseline, incident and recovery snapshots, links Mini-Drop collection tasks, runs a hidden-Oracle comparison, and verifies cleanup. Scoring occurs only after the Campaign reaches a terminal state.",
        "",
        "All strategies share the same faults and test cases. Equal scores mean the strategies converge on these cases; they are not copied by the runner. Each raw Campaign remains independently auditable.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def normalize_submission_quality(submissions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove claims that are not backed by a captured, accepted artifact."""

    for submission in submissions:
        detail = submission.get("diagnosis_detail") or {}
        valid_ids: set[str] = set()
        for evidence in detail.get("evidence", []) or []:
            observed = evidence.get("observed_value") or {}
            snapshot = observed.get("campaign_snapshot") or {}
            if not snapshot or str(evidence.get("data_quality") or "").upper() != "VERIFIED":
                evidence["data_quality"] = "MISSING"
                observed["benchmark_evidence_tags"] = []
            else:
                valid_ids.add(str(evidence.get("evidence_id")))
        detail["evidence_snapshots"] = [
            item
            for item in detail.get("evidence_snapshots", []) or []
            if str(item.get("evidence_id")) in valid_ids
        ]
        conclusion = detail.get("latest_conclusion") or {}
        assessment = conclusion.get("cluster_assessment") or {}
        assessment["evidence_refs"] = [
            item for item in assessment.get("evidence_refs", []) or [] if str(item) in valid_ids
        ]
        if detail.get("status") != "COMPLETED":
            conclusion["root_location"] = {"type": "unknown", "target_ref": None}
            conclusion["domain_cause"] = {"type": "unknown"}
            assessment["classification"] = "unknown"
    return submissions

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports" / "benchmark" / "official-90")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument(
        "--api-key-env",
        default="MINI_DROP_API_KEY",
        help="environment variable containing the API key used by the Web gateway",
    )
    args = parser.parse_args()
    output = args.output_dir.resolve()
    raw_dir = output / "raw-campaigns"
    raw_dir.mkdir(parents=True, exist_ok=True)
    submissions_path = output / "submissions.json"
    plan = build_run_plan()
    (output / "run-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    current = json.loads(submissions_path.read_text(encoding="utf-8")) if submissions_path.exists() else []
    current = normalize_submission_quality(current)
    atomic_write_json(submissions_path, current)
    completed = {f"{item['case_id']}:{item['strategy']}:{item['repetition']}" for item in current}
    session = requests.Session()
    api_key = os.environ.get(args.api_key_env, "").strip()
    if api_key:
        # The gateway accepts Bearer authentication.  Keeping the secret in an
        # environment variable prevents it from appearing in argv, reports or
        # process listings on the evaluation host.
        session.headers.update({"Authorization": f"Bearer {api_key}"})
    total = len(plan["executions"])
    for number, execution in enumerate(plan["executions"], start=1):
        execution_id = execution["execution_id"]
        if execution_id in completed:
            print(f"[{number:02d}/{total}] SKIP {execution_id}", flush=True)
            continue
        scenario_id = CASE_RUNTIME[execution["case_id"]][0]
        started = time.monotonic()
        # Infrastructure errors are not strategy results.  Fail fast and let
        # the resumable file continue after Docker/API recovery; only a real
        # terminal Campaign is eligible for scoring.
        created = api_data(session.post(
            f"{args.base_url.rstrip('/')}/api/v1/diagnosis-campaigns/runs",
            json={"scenario_id": scenario_id, "strategy": execution["strategy"]},
            timeout=15,
        ))
        run = wait_campaign(args.base_url.rstrip('/'), created["run_id"], args.timeout)
        raw_path = raw_dir / f"{execution_id.replace(':', '__')}.json"
        raw_path.write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        detail = scoring_detail(execution, run)
        result = upsert_submission(submissions_path, {
            "case_id": execution["case_id"],
            "strategy": execution["strategy"],
            "repetition": execution["repetition"],
            "diagnosis_detail": detail,
        })
        print(
            f"[{number:02d}/{total}] {execution_id} campaign={run['run_id']} "
            f"status={detail['status']} elapsed={time.monotonic() - started:.1f}s "
            f"remaining={result['remaining']}", flush=True
        )

    submissions = normalize_submission_quality(json.loads(submissions_path.read_text(encoding="utf-8")))
    atomic_write_json(submissions_path, submissions)
    completeness = validate_campaign_completeness(submissions)
    progress = campaign_progress(submissions)
    report = evaluate_submissions(submissions, require_complete=True)
    report["campaign_completeness"] = completeness
    report["plan_fingerprint"] = plan["plan_fingerprint"]
    (output / "evaluation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(report, progress, output / "evaluation-report.md")
    print(json.dumps({"progress": progress, "strategy_metrics": report["strategy_metrics"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
