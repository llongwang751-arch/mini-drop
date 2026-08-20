#!/usr/bin/env python3
"""Run and score the 18-execution orchestrator-backed OTel live subset."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from html import escape
import json
from pathlib import Path
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.otel_experiment_common import (  # noqa: E402
    DEFAULT_OTEL_ROOT,
    atomic_write_json,
    resolve_compose_service_container,
)
from scripts.run_cpu_campaign_window import (  # noqa: E402
    execute_campaign_window as execute_cpu_window,
)
from scripts.run_memory_campaign_window import (  # noqa: E402
    execute_campaign_window as execute_memory_window,
)
from server.app.diagnosis.benchmark_runner import (  # noqa: E402
    BenchmarkSubmission,
    STRATEGIES,
    evaluate_submissions,
    render_html_report,
)


LIVE_CASES = ("T1-CPU-001", "T1-MEM-001")
REPETITIONS = (1, 2, 3)
SUBSET_SCHEMA_VERSION = "otel-live-subset.v1"
WindowExecutor = Callable[..., dict[str, Any]]


def execution_id(case_id: str, strategy: str, repetition: int) -> str:
    return f"{case_id}:{strategy}:{repetition}"


def build_live_subset_plan() -> dict[str, Any]:
    executions = [
        {
            "execution_id": execution_id(case_id, strategy, repetition),
            "case_id": case_id,
            "strategy": strategy,
            "repetition": repetition,
        }
        for case_id in LIVE_CASES
        for strategy in STRATEGIES
        for repetition in REPETITIONS
    ]
    serialized = json.dumps(
        executions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return {
        "schema_version": SUBSET_SCHEMA_VERSION,
        "title": "Orchestrator-backed OTel live subset",
        "case_ids": list(LIVE_CASES),
        "strategies": list(STRATEGIES),
        "repetitions": len(REPETITIONS),
        "execution_count": len(executions),
        "oracle_in_runner_input": False,
        "plan_fingerprint": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "executions": executions,
    }


def load_submissions(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("live subset submissions must be a JSON array")
    return payload


def subset_progress(
    submissions: list[dict[str, Any] | BenchmarkSubmission],
) -> dict[str, Any]:
    plan = build_live_subset_plan()
    expected = {item["execution_id"] for item in plan["executions"]}
    observed: list[str] = []
    invalid: list[str] = []
    for index, raw in enumerate(submissions):
        try:
            item = (
                raw
                if isinstance(raw, BenchmarkSubmission)
                else BenchmarkSubmission.model_validate(raw)
            )
        except Exception as exc:
            invalid.append(f"submission[{index}]: {type(exc).__name__}: {exc}")
            continue
        observed.append(execution_id(item.case_id, item.strategy, item.repetition))
    counts = Counter(observed)
    observed_set = set(observed)
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    unexpected = sorted(observed_set - expected)
    missing = sorted(expected - observed_set)
    complete = (
        not invalid
        and not duplicates
        and not unexpected
        and not missing
        and len(observed) == len(expected)
    )
    return {
        "expected": len(expected),
        "recorded": len(observed),
        "unique_recorded": len(observed_set & expected),
        "remaining": len(missing),
        "complete": complete,
        "duplicates": duplicates,
        "unexpected": unexpected,
        "missing_execution_ids": missing,
        "invalid": invalid,
    }


def validate_subset_completeness(
    submissions: list[dict[str, Any] | BenchmarkSubmission],
) -> dict[str, Any]:
    progress = subset_progress(submissions)
    if not progress["complete"]:
        raise ValueError(
            "live subset is incomplete: "
            f"missing={len(progress['missing_execution_ids'])}, "
            f"duplicates={len(progress['duplicates'])}, "
            f"unexpected={len(progress['unexpected'])}, "
            f"invalid={len(progress['invalid'])}"
        )
    return {
        "expected": progress["expected"],
        "observed": progress["recorded"],
        "complete": True,
    }


def validate_raw_manifests(
    submissions: list[dict[str, Any] | BenchmarkSubmission],
) -> dict[str, Any]:
    checked: dict[str, str] = {}
    manifest_groups: dict[str, set[str]] = {}
    errors: list[str] = []
    for raw in submissions:
        item = (
            raw
            if isinstance(raw, BenchmarkSubmission)
            else BenchmarkSubmission.model_validate(raw)
        )
        item_id = execution_id(item.case_id, item.strategy, item.repetition)
        campaign = item.diagnosis_detail.get("campaign_window")
        if not isinstance(campaign, dict):
            errors.append(f"{item_id}: missing campaign_window lineage")
            continue
        manifest_value = campaign.get("manifest")
        if not manifest_value:
            errors.append(f"{item_id}: missing raw manifest path")
            continue
        manifest_path = Path(str(manifest_value)).resolve()
        if not manifest_path.is_file():
            errors.append(f"{item_id}: raw manifest not found: {manifest_path}")
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("case_id") != item.case_id:
            errors.append(f"{item_id}: manifest case_id mismatch")
        if manifest.get("repetition") != item.repetition:
            errors.append(f"{item_id}: manifest repetition mismatch")
        if not manifest.get("publication", {}).get("published"):
            errors.append(f"{item_id}: manifest was not published")
        if manifest.get("fixture_failure"):
            errors.append(f"{item_id}: manifest contains fixture_failure")
        if manifest.get("cleanup", {}).get("errors"):
            errors.append(f"{item_id}: manifest cleanup contains errors")
        window_id = str(manifest.get("window_id") or "")
        if not window_id or campaign.get("window_id") != window_id:
            errors.append(f"{item_id}: campaign window_id mismatch")
        manifest_groups.setdefault(str(manifest_path), set()).add(item.strategy)
        checked[item_id] = str(manifest_path)
    expected_manifests = len(LIVE_CASES) * len(REPETITIONS)
    if len(manifest_groups) != expected_manifests:
        errors.append(
            f"expected {expected_manifests} unique manifests, got {len(manifest_groups)}"
        )
    for path, strategies in manifest_groups.items():
        if strategies != set(STRATEGIES):
            errors.append(
                f"{path}: manifest must cover all strategies, got {sorted(strategies)}"
            )
    if errors:
        raise ValueError("raw manifest validation failed: " + "; ".join(errors))
    return {
        "validated": len(checked),
        "unique_manifests": len(manifest_groups),
        "execution_manifests": checked,
    }


def _attach_manifest_lineage(manifest: dict[str, Any], manifest_path: Path) -> None:
    if not manifest.get("publication", {}).get("published"):
        return
    submissions_path = None
    for record in manifest["publication"].get("records", []):
        if record.get("path"):
            submissions_path = Path(record["path"])
            break
    if submissions_path is None or not submissions_path.exists():
        return
    submissions = load_submissions(submissions_path)
    changed = False
    for submission in submissions:
        if (
            submission.get("case_id") == manifest.get("case_id")
            and submission.get("repetition") == manifest.get("repetition")
        ):
            detail = submission.get("diagnosis_detail") or {}
            campaign = detail.get("campaign_window") or {}
            if campaign.get("window_id") == manifest.get("window_id"):
                campaign["manifest"] = str(manifest_path.resolve())
                detail["campaign_window"] = campaign
                changed = True
    if changed:
        atomic_write_json(submissions_path, submissions)


def _completed_windows(submissions: list[dict[str, Any]]) -> set[tuple[str, int]]:
    groups: dict[tuple[str, int], list[str]] = {}
    for raw in submissions:
        item = BenchmarkSubmission.model_validate(raw)
        key = (item.case_id, item.repetition)
        groups.setdefault(key, []).append(item.strategy)
    partial = {
        key: strategies
        for key, strategies in groups.items()
        if len(strategies) != len(STRATEGIES) or set(strategies) != set(STRATEGIES)
    }
    if partial:
        formatted = ", ".join(f"{case}:r{rep}" for case, rep in sorted(partial))
        raise ValueError(f"partial campaign windows cannot be resumed automatically: {formatted}")
    return set(groups)


def execute_live_subset(
    *,
    otel_root: Path,
    base_url: str,
    agent_id: str,
    submissions_path: Path,
    output_dir: Path,
    approve_r2: bool,
    diagnosis_timeout: int = 35,
    load_duration: int = 120,
    cpu_container: str = "ad",
    memory_container: str = "email",
    cpu_project_name: str | None = None,
    cpu_compose_files: list[Path] | None = None,
    memory_project_name: str | None = None,
    memory_compose_files: list[Path] | None = None,
    environment_file: Path | None = None,
    cpu_executor: WindowExecutor = execute_cpu_window,
    memory_executor: WindowExecutor = execute_memory_window,
) -> dict[str, Any]:
    if not approve_r2:
        raise ValueError("formal live subset requires explicit R2 approval")
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = build_live_subset_plan()
    atomic_write_json(output_dir / "run-plan.json", plan)
    current = load_submissions(submissions_path)
    completed = _completed_windows(current)
    expected_windows = {(case_id, repetition) for case_id in LIVE_CASES for repetition in REPETITIONS}
    unexpected_windows = completed - expected_windows
    if unexpected_windows:
        raise ValueError(f"submissions contain cases outside live subset: {sorted(unexpected_windows)}")

    window_results: list[dict[str, Any]] = []
    case_configs = (
        (
            "T1-CPU-001",
            cpu_executor,
            cpu_container,
            "ad",
            cpu_project_name,
            cpu_compose_files,
        ),
        (
            "T1-MEM-001",
            memory_executor,
            memory_container,
            "email",
            memory_project_name,
            memory_compose_files,
        ),
    )
    for case_id, executor, container, service, project_name, compose_files in case_configs:
        if (project_name is None) != (compose_files is None):
            raise ValueError(
                f"{case_id} requires project_name and compose_files together"
            )
        resolved_container = container
        has_pending_window = any(
            (case_id, repetition) not in completed for repetition in REPETITIONS
        )
        if (
            has_pending_window
            and project_name is not None
            and compose_files is not None
        ):
            resolved_container = resolve_compose_service_container(
                project_name=project_name,
                compose_files=compose_files,
                service=service,
                environment_file=environment_file,
            )
        for repetition in REPETITIONS:
            if (case_id, repetition) in completed:
                window_results.append(
                    {"case_id": case_id, "repetition": repetition, "status": "SKIPPED"}
                )
                continue
            window_id = f"{case_id}-r{repetition}"
            manifest = executor(
                repetition=repetition,
                otel_root=otel_root,
                base_url=base_url,
                agent_id=agent_id,
                load_duration=load_duration,
                load_workers=1,
                diagnosis_timeout=diagnosis_timeout,
                approve_r2=approve_r2,
                overwrite=False,
                submissions=submissions_path,
                output_dir=output_dir / "campaign-windows",
                container=resolved_container,
                project_name=project_name,
                compose_files=compose_files,
                environment_file=environment_file,
                window_id=window_id,
            )
            manifest_path = output_dir / "campaign-windows" / f"{window_id}-manifest.json"
            _attach_manifest_lineage(manifest, manifest_path)
            window_results.append(
                {
                    "case_id": case_id,
                    "repetition": repetition,
                    "status": (
                        "PUBLISHED"
                        if manifest.get("publication", {}).get("published")
                        else "FIXTURE_FAILED"
                    ),
                    "manifest": str(manifest_path.resolve()),
                }
            )
            if not manifest.get("publication", {}).get("published"):
                return {
                    "complete": False,
                    "stopped_on_fixture_failure": True,
                    "windows": window_results,
                    "progress": subset_progress(load_submissions(submissions_path)),
                }
    return finalize_live_subset(
        submissions_path=submissions_path,
        output_dir=output_dir,
        plan=plan,
        window_results=window_results,
    )


def finalize_live_subset(
    *,
    submissions_path: Path,
    output_dir: Path,
    plan: dict[str, Any] | None = None,
    window_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    plan = plan or build_live_subset_plan()
    submissions = load_submissions(submissions_path)
    completeness = validate_subset_completeness(submissions)
    manifests = validate_raw_manifests(submissions)
    report = evaluate_submissions(submissions, require_complete=False)
    if not report.get("oracle_isolated"):
        raise ValueError("live subset evaluator did not preserve Oracle isolation")
    report.update(
        {
            "title": "2 case / 18 run / orchestrator-backed live subset",
            "schema_version": SUBSET_SCHEMA_VERSION,
            "campaign_completeness": completeness,
            "raw_manifest_validation": manifests,
            "plan_fingerprint": plan["plan_fingerprint"],
            "historical_official_90_modified": False,
        }
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_dir / "evaluation-report.json", report)
    html = render_live_subset_html(report)
    (output_dir / "evaluation-report.html").write_text(html, encoding="utf-8")
    result = {
        "complete": True,
        "windows": window_results or [],
        "progress": subset_progress(submissions),
        "report": str((output_dir / "evaluation-report.json").resolve()),
        "html_report": str((output_dir / "evaluation-report.html").resolve()),
    }
    atomic_write_json(output_dir / "run-result.json", result)
    return result


def render_live_subset_html(report: dict[str, Any]) -> str:
    html = render_html_report(report)
    html = html.replace("Mini-Drop 90 次策略评测", "Mini-Drop OTel Live Subset")
    html = html.replace("Mini-Drop 90 次诊断策略评测", "Mini-Drop OTel Live Subset")
    notice = (
        "<p><b>2 case / 18 run / orchestrator-backed live subset.</b> "
        "This report is independent of the historical static official-90 artifacts. "
        f"Plan fingerprint: <code>{escape(str(report['plan_fingerprint']))}</code>.</p>"
    )
    return html.replace("<section><h2>策略对比", notice + "<section><h2>策略对比", 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--otel-root", type=Path, default=DEFAULT_OTEL_ROOT)
    parser.add_argument("--base-url", default="http://localhost")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--approve-r2", action="store_true")
    parser.add_argument("--diagnosis-timeout", type=int, default=35)
    parser.add_argument("--load-duration", type=int, default=120)
    parser.add_argument("--cpu-container", default="ad")
    parser.add_argument("--memory-container", default="email")
    parser.add_argument("--cpu-project-name", default=None)
    parser.add_argument("--cpu-compose-file", type=Path, action="append")
    parser.add_argument("--memory-project-name", default=None)
    parser.add_argument("--memory-compose-file", type=Path, action="append")
    parser.add_argument("--environment-file", type=Path, default=None)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports" / "benchmark" / "live-subset",
    )
    parser.add_argument("--finalize-only", action="store_true")
    args = parser.parse_args()
    submissions_path = args.output_dir / "submissions.json"
    if args.finalize_only:
        result = finalize_live_subset(
            submissions_path=submissions_path,
            output_dir=args.output_dir,
        )
    else:
        result = execute_live_subset(
            otel_root=args.otel_root,
            base_url=args.base_url,
            agent_id=args.agent_id,
            submissions_path=submissions_path,
            output_dir=args.output_dir,
            approve_r2=args.approve_r2,
            diagnosis_timeout=args.diagnosis_timeout,
            load_duration=args.load_duration,
            cpu_container=args.cpu_container,
            memory_container=args.memory_container,
            cpu_project_name=args.cpu_project_name,
            cpu_compose_files=args.cpu_compose_file,
            memory_project_name=args.memory_project_name,
            memory_compose_files=args.memory_compose_file,
            environment_file=args.environment_file,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("complete") else 1


if __name__ == "__main__":
    raise SystemExit(main())
