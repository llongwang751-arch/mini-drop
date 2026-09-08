"""Run strict live Diagnosis closure across the allow-listed Fault Plaza.

This is deliberately different from the 540-case deterministic benchmark.
Every selected row starts a real bounded fault and requires fresh
Diagnosis -> Task/Attempt/Artifact -> Evidence -> Report lineage before the
fault is stopped.  A start/stop-only smoke test is recorded as a failure, not
silently promoted to live diagnosis evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.verify_interview_demo import (
        AcceptanceError,
        Client,
        _get_plaza,
        _run_diagnosis,
        _scenario,
        _start_fault,
        _stop_fault,
        items_of,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from verify_interview_demo import (  # type: ignore[no-redef]
        AcceptanceError,
        Client,
        _get_plaza,
        _run_diagnosis,
        _scenario,
        _start_fault,
        _stop_fault,
        items_of,
    )


PROCESS_NAMES_BY_LAB = {
    "python": ("python-hotspot",),
    "go": ("go-hotspot",),
    "java": ("java", "java-hotspot"),
    "cpp": ("cpp-hotspot",),
}

FAULT_API_MAX_DURATION_SECONDS = 300
FAULT_CLEANUP_MARGIN_SECONDS = 30


# The collector used as the strict evidence checkpoint is part of the test
# contract, not "the second item" in a presentation-oriented probe list.  In
# particular, latency, saturation and noisy-neighbour faults are established
# by bounded application/system counters; a CPU profile may be useful
# counter-evidence but cannot prove those causes by itself.
DECISIVE_COLLECTOR_BY_SCENARIO = {
    "cpu-hotspot": "pyspy",
    "source-hotspot": "pyspy",
    "memory-pressure": "memory_smaps",
    "io-write-latency": "ebpf_io",
    "noisy-neighbor": "sys_metrics",
    "load-saturation": "sys_metrics",
    "queue-backlog": "sys_metrics",
    "go-cpu-hotspot": "go_pprof",
    "go-network-latency": "sys_metrics",
    "go-memory-growth": "memory_smaps",
    "go-file-io": "ebpf_io",
    "java-gc-pressure": "java_async",
    "java-lock-contention": "java_async",
    "java-downstream-latency": "sys_metrics",
    "java-offheap-growth": "memory_smaps",
    "java-file-io": "ebpf_io",
    "cpp-cpu-hotspot": "perf_cpu",
    "cpp-lock-contention": "sys_metrics",
    "cpp-memory-growth": "memory_smaps",
    "cpp-file-io": "ebpf_io",
    "cpp-downstream-latency": "sys_metrics",
}


def _decisive_collector(scenario: dict[str, Any]) -> str:
    scenario_id = str(scenario.get("scenario_id") or "")
    collectors = [
        str(item) for item in scenario.get("recommended_collectors") or []
    ]
    if not collectors:
        raise AcceptanceError(f"scenario {scenario_id} has no collector contract")
    decisive = DECISIVE_COLLECTOR_BY_SCENARIO.get(scenario_id)
    if decisive is None:
        raise AcceptanceError(
            f"scenario {scenario_id} has no explicit decisive collector contract"
        )
    if decisive not in collectors:
        raise AcceptanceError(
            f"scenario {scenario_id} decisive collector {decisive} is not recommended"
        )
    return decisive


def _canonical_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _find_demo_process(
    client: Client,
    *,
    agent_id: str,
    lab_key: str,
) -> dict[str, Any]:
    expected = PROCESS_NAMES_BY_LAB.get(lab_key, ())
    encoded_agent = urllib.parse.quote(agent_id, safe="")
    response = client.request(
        "GET", f"/api/top-processes?agent_id={encoded_agent}&limit=200"
    ) or {}
    if response.get("authoritative") is False or response.get("fresh") is False:
        raise AcceptanceError(f"{lab_key} process snapshot is stale or non-authoritative")
    process = next(
        (
            item
            for item in items_of(response)
            if str(item.get("comm") or "") in expected
            and int(item.get("pid") or 0) > 0
        ),
        None,
    )
    if process is None:
        raise AcceptanceError(
            f"fresh Agent snapshot does not contain {lab_key} target "
            f"({', '.join(expected)})"
        )
    return process


def run_scenario(
    client: Client,
    scenario: dict[str, Any],
    *,
    agent_id: str,
    fault_duration_seconds: int,
    timeout_seconds: int,
    warmup_seconds: float,
    poll_seconds: float,
) -> dict[str, Any]:
    scenario_id = str(scenario["scenario_id"])
    decisive_collector = _decisive_collector(scenario)
    result: dict[str, Any] = {
        "scenario_id": scenario_id,
        "title": scenario.get("title"),
        "runtime": scenario.get("target_runtime"),
        "root_cause_oracle": scenario_id,
        "decisive_collector": decisive_collector,
        "started": False,
        "diagnosis_chain_verified": False,
        "cleanup_verified": False,
        "passed": False,
    }
    fault_started = False
    primary_error: Exception | None = None
    try:
        if scenario.get("active"):
            _stop_fault(client, scenario_id)
        # The lab's duration is a dead-man switch, not the measurement window.
        # It must outlive the verifier deadline; successful and failed runs are
        # still stopped eagerly in ``finally``.  A shorter fault duration used
        # to make later rounds collect recovery data and report honest but
        # misleading INSUFFICIENT_EVIDENCE outcomes.
        effective_timeout_seconds = min(
            timeout_seconds,
            FAULT_API_MAX_DURATION_SECONDS - FAULT_CLEANUP_MARGIN_SECONDS,
        )
        effective_fault_duration_seconds = min(
            FAULT_API_MAX_DURATION_SECONDS,
            max(
                fault_duration_seconds,
                effective_timeout_seconds + FAULT_CLEANUP_MARGIN_SECONDS,
            ),
        )
        result["diagnosis_timeout_seconds"] = {
            "requested": timeout_seconds,
            "effective": effective_timeout_seconds,
        }
        result["fault_duration_seconds"] = {
            "requested": fault_duration_seconds,
            "effective": effective_fault_duration_seconds,
        }
        started = _start_fault(
            client,
            scenario_id,
            effective_fault_duration_seconds,
        )
        fault_started = True
        result["started"] = True
        if warmup_seconds:
            time.sleep(warmup_seconds)
        process = _find_demo_process(
            client,
            agent_id=agent_id,
            lab_key=str(scenario.get("lab_key") or "python"),
        )
        result["target"] = {
            "agent_id": agent_id,
            "pid": int(process["pid"]),
            "comm": process.get("comm"),
        }
        diagnosis = _run_diagnosis(
            client,
            started["diagnosis_request"],
            policy="AUTO",
            demo_agent_id=agent_id,
            demo_pid=int(process["pid"]),
            timeout_seconds=effective_timeout_seconds,
            poll_seconds=poll_seconds,
            minimum_rounds=max(
                3, int(scenario.get("minimum_diagnosis_rounds") or 3)
            ),
            expected_collector=decisive_collector,
            expected_hot_function="",
            profile_validation="lineage_only",
        )
        result["diagnosis"] = diagnosis
        result["diagnosis_chain_verified"] = True
    except Exception as exc:  # Preserve the exact failed stage in the report.
        primary_error = exc
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)
    finally:
        if fault_started:
            try:
                _stop_fault(client, scenario_id)
                result["cleanup_verified"] = True
            except Exception as cleanup_error:
                result["cleanup_error"] = str(cleanup_error)
                if primary_error is None:
                    primary_error = cleanup_error
                    result["error_type"] = type(cleanup_error).__name__
                    result["error"] = str(cleanup_error)
    result["passed"] = bool(
        result["started"]
        and result["diagnosis_chain_verified"]
        and result["cleanup_verified"]
    )
    return result


def run_campaign(
    client: Client,
    *,
    scenario_ids: list[str] | None,
    agent_id: str,
    fault_duration_seconds: int,
    timeout_seconds: int,
    warmup_seconds: float,
    poll_seconds: float,
    checkpoint_path: Path | None = None,
) -> dict[str, Any]:
    health = client.request("GET", "/api/healthz")
    plaza = _get_plaza(client)
    scenarios = items_of(plaza.get("scenarios"))
    selected_ids = scenario_ids or [
        str(item.get("scenario_id")) for item in scenarios if item.get("available")
    ]
    unknown = sorted(set(selected_ids) - {str(item.get("scenario_id")) for item in scenarios})
    if unknown:
        raise AcceptanceError(f"unknown Fault Plaza scenarios: {', '.join(unknown)}")
    results = []
    for scenario_id in selected_ids:
        scenario = _scenario(plaza, scenario_id)
        if not scenario.get("available"):
            results.append(
                {
                    "scenario_id": scenario_id,
                    "title": scenario.get("title"),
                    "runtime": scenario.get("target_runtime"),
                    "passed": False,
                    "started": False,
                    "diagnosis_chain_verified": False,
                    "cleanup_verified": True,
                    "error_type": "SCENARIO_UNAVAILABLE",
                    "error": scenario.get("unavailable_reason") or "scenario unavailable",
                }
            )
        else:
            results.append(
                run_scenario(
                    client,
                    scenario,
                    agent_id=agent_id,
                    fault_duration_seconds=fault_duration_seconds,
                    timeout_seconds=timeout_seconds,
                    warmup_seconds=warmup_seconds,
                    poll_seconds=poll_seconds,
                )
            )
        if checkpoint_path is not None:
            passed_so_far = sum(bool(item.get("passed")) for item in results)
            checkpoint: dict[str, Any] = {
                "schema": "mini-drop.fault-plaza-diagnosis-closure.v1",
                "run_status": "RUNNING",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "health": health,
                "authentication": "X-API-Key_FROM_ENV_REDACTED",
                "fault_plaza_scenario_count": len(scenarios),
                "selected_count": len(selected_ids),
                "completed_count": len(results),
                "passed_count": passed_so_far,
                "failed_count": len(results) - passed_so_far,
                "passed": False,
                "results": results,
                "truth_boundary": (
                    "RUNNING checkpoint. Only completed rows are recorded; this is not "
                    "a final campaign result or a production accuracy claim."
                ),
            }
            checkpoint["report_sha256"] = _canonical_sha256(checkpoint)
            _write_json_atomic(checkpoint_path, checkpoint)
    passed_count = sum(bool(item.get("passed")) for item in results)
    report: dict[str, Any] = {
        "schema": "mini-drop.fault-plaza-diagnosis-closure.v1",
        "run_status": "COMPLETED",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "health": health,
        "authentication": "X-API-Key_FROM_ENV_REDACTED",
        "fault_plaza_scenario_count": len(scenarios),
        "selected_count": len(results),
        "passed_count": passed_count,
        "failed_count": len(results) - passed_count,
        "passed": passed_count == len(results) and bool(results),
        "results": results,
        "truth_boundary": (
            "Each PASS is a fresh live controlled fault with persisted "
            "Diagnosis/Task/Attempt/Artifact/Evidence/Report lineage and verified cleanup. "
            "This report is not the 540-case deterministic replay and does not claim "
            "production traffic accuracy. Any failed row remains failed."
        ),
    }
    report["report_sha256"] = _canonical_sha256(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run strict live closure for all or selected Fault Plaza scenarios"
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key-env", default="MINI_DROP_API_KEY")
    parser.add_argument("--scenario", action="append", dest="scenarios")
    parser.add_argument("--demo-agent", default="control-interview-demo-agent")
    parser.add_argument("--fault-duration-seconds", type=int, default=300)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--warmup-seconds", type=float, default=3)
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    api_key = os.getenv(args.api_key_env, "").strip()
    if not api_key:
        raise SystemExit(f"environment variable {args.api_key_env} is required")
    client = Client(args.base_url, api_key, insecure=args.insecure)
    try:
        report = run_campaign(
            client,
            scenario_ids=args.scenarios,
            agent_id=args.demo_agent,
            fault_duration_seconds=args.fault_duration_seconds,
            timeout_seconds=args.timeout_seconds,
            warmup_seconds=args.warmup_seconds,
            poll_seconds=args.poll_seconds,
            checkpoint_path=args.output,
        )
    except Exception as exc:
        report = {
            "schema": "mini-drop.fault-plaza-diagnosis-closure.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "passed": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "authentication": "X-API-Key_FROM_ENV_REDACTED",
        }
        report["report_sha256"] = _canonical_sha256(report)
    _write_json_atomic(args.output, report)
    print(
        json.dumps(
            {
                "passed": report.get("passed"),
                "passed_count": report.get("passed_count", 0),
                "failed_count": report.get("failed_count", 0),
                "output": str(args.output),
                "report_sha256": report.get("report_sha256"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
