"""Run independent live acceptance for continuous perf and eBPF I/O.

This script does not reuse a Diagnosis result.  Each row starts its own
allow-listed fault, creates a fresh Task through the public API, waits for the
real native Agent and Analyzer, verifies artifact integrity, then stops the
fault in ``finally``.  The API key is read only from an environment variable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from verify_interview_demo import (
    AcceptanceError,
    Client,
    _artifact_sample_count,
    _collect_task_lineage,
    _start_fault,
    _stop_fault,
    items_of,
)


TERMINAL_TASK = {"DONE", "FAILED", "CANCELLED"}
DEMO_AGENT_ID = "control-interview-demo-agent"
CAMPAIGNS = (
    {
        "name": "long-continuous-perf",
        "scenario_id": "cpp-cpu-hotspot",
        "process_names": ("cpp-hotspot",),
        "collector": "continuous_perf",
        "duration_sec": 60,
        "sample_rate": 49,
        "options": {
            "timeout_sec": 100,
            "event": "cpu-cycles",
            "callgraph": "fp",
            "window_seconds": 15,
            "trigger_cpu_percent": 10,
            "trigger_consecutive_samples": 2,
            "trigger_wait_seconds": 20,
            "retention_tier": "short",
            "source": "priority_collector_acceptance",
        },
        "required_artifacts": (
            "continuous_bundle",
            "continuous_summary",
            "continuous_flamegraph_json",
            "continuous_top_json",
        ),
    },
    {
        "name": "independent-ebpf-io",
        "scenario_id": "cpp-file-io",
        "process_names": ("cpp-hotspot",),
        "collector": "ebpf_io",
        "duration_sec": 20,
        "sample_rate": 11,
        "options": {
            "timeout_sec": 45,
            "source": "priority_collector_acceptance",
        },
        "required_artifacts": ("ebpf_metrics", "ebpf_raw"),
    },
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


def _canonical_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _demo_process(client: Client, process_names: tuple[str, ...]) -> dict[str, Any]:
    encoded = urllib.parse.quote(DEMO_AGENT_ID, safe="")
    snapshot = client.request(
        "GET", f"/api/top-processes?agent_id={encoded}&limit=100"
    ) or {}
    _require(snapshot.get("authoritative") is not False, "process snapshot is not authoritative")
    _require(snapshot.get("fresh") is not False, "process snapshot is stale")
    process = next(
        (
            item
            for item in items_of(snapshot)
            if item.get("comm") in process_names and int(item.get("pid") or 0) > 0
        ),
        None,
    )
    _require(process is not None, f"demo snapshot lacks process {process_names}")
    return process


def _wait_task(client: Client, task_id: str, timeout_seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    task: dict[str, Any] = {}
    while time.monotonic() < deadline:
        task = client.request("GET", f"/api/tasks/{urllib.parse.quote(task_id, safe='')}") or {}
        if str(task.get("status") or "").upper() in TERMINAL_TASK:
            return task
        time.sleep(2)
    raise TimeoutError(f"task {task_id} did not finish in {timeout_seconds}s")


def _run_campaign(client: Client, campaign: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    process = _demo_process(client, tuple(campaign["process_names"]))
    fault_started = False
    try:
        _start_fault(client, str(campaign["scenario_id"]), 180)
        fault_started = True
        time.sleep(3)
        created = client.request(
            "POST",
            "/api/tasks",
            {
                "name": f"priority-acceptance:{campaign['name']}:{int(time.time())}",
                "agent_id": DEMO_AGENT_ID,
                "target_pid": int(process["pid"]),
                "collector_type": campaign["collector"],
                "sample_rate": int(campaign["sample_rate"]),
                "duration_sec": int(campaign["duration_sec"]),
                "options": dict(campaign["options"]),
                "resource_budget": {
                    "max_cpu_percent": 35,
                    "max_memory_mb": 512,
                    "max_output_mb": 128,
                    "max_duration_sec": int(campaign["duration_sec"]),
                },
            },
        ) or {}
        task_id = str(created.get("task_id") or "")
        _require(bool(task_id), "create Task returned no task_id")
        task = _wait_task(client, task_id, timeout_seconds)
        lineage = _collect_task_lineage(client, task_id)
        artifacts = lineage.pop("_raw_artifacts")
        artifact_types = {str(item.get("artifact_type")) for item in artifacts}
        missing = set(campaign["required_artifacts"]) - artifact_types
        _require(task.get("status") == "DONE", f"{campaign['collector']} ended as {task.get('status')}")
        _require(not missing, f"{campaign['collector']} missing artifacts: {sorted(missing)}")
        required = [item for item in artifacts if item.get("artifact_type") in campaign["required_artifacts"]]
        _require(
            all(item.get("integrity_status") == "VERIFIED" for item in required),
            f"{campaign['collector']} has an unverified required artifact",
        )
        if campaign["collector"] == "ebpf_io":
            metrics = next(item for item in artifacts if item.get("artifact_type") == "ebpf_metrics")
            _require(_artifact_sample_count(metrics) > 0, "eBPF artifact has zero real samples")
        if campaign["collector"] == "continuous_perf":
            summary = next(item for item in artifacts if item.get("artifact_type") == "continuous_summary")
            metadata = summary.get("metadata") if isinstance(summary.get("metadata"), dict) else {}
            windows = metadata.get("windows") if isinstance(metadata.get("windows"), list) else []
            _require(len(windows) >= 3, "continuous perf produced fewer than three windows")
        return {
            "name": campaign["name"],
            "scenario_id": campaign["scenario_id"],
            "collector": campaign["collector"],
            "task_id": task_id,
            "target": {"pid": process.get("pid"), "comm": process.get("comm")},
            "status": task.get("status"),
            "collection_status": task.get("collection_status"),
            "analysis_status": task.get("analysis_status"),
            "requested_duration_seconds": campaign["duration_sec"],
            "artifacts": [
                {
                    key: item.get(key)
                    for key in ("artifact_type", "size_bytes", "sha256", "integrity_status")
                }
                for item in required
            ],
        }
    finally:
        if fault_started:
            _stop_fault(client, str(campaign["scenario_id"]))


def run_acceptance(client: Client, timeout_seconds: int) -> dict[str, Any]:
    health = client.request("GET", "/api/healthz")
    rows = [_run_campaign(client, campaign, timeout_seconds) for campaign in CAMPAIGNS]
    report: dict[str, Any] = {
        "schema": "mini-drop.priority-collectors-live.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "authentication": "X-API-Key_FROM_ENV_REDACTED",
        "passed": True,
        "health": health,
        "results": rows,
        "cleanup": {"all_faults_stopped": True},
        "measurement_boundary": (
            "两个独立公网 API Task：continuous_perf 在 C++ CPU 故障中运行 60 秒并产生至少三个窗口；"
            "ebpf_io 在 C++ 真实文件写入故障中采集非零块 I/O 样本。每个必需产物均校验 SHA-256。"
        ),
    }
    report["report_sha256"] = _canonical_sha256(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="验收长时间 continuous perf 与独立 eBPF Task")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key-env", default="MINI_DROP_API_KEY")
    parser.add_argument("--timeout-seconds", type=int, default=240)
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    api_key = os.getenv(args.api_key_env, "").strip()
    if not api_key:
        raise SystemExit(f"missing API key environment variable: {args.api_key_env}")
    client = Client(args.base_url, api_key, args.insecure)
    try:
        report = run_acceptance(client, args.timeout_seconds)
    except (AcceptanceError, TimeoutError) as error:
        report = {
            "schema": "mini-drop.priority-collectors-live.v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "authentication": "X-API-Key_FROM_ENV_REDACTED",
            "passed": False,
            "error_type": type(error).__name__,
            "error": str(error)[:2000],
        }
    report["base_url"] = args.base_url.rstrip("/")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "passed": report["passed"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
