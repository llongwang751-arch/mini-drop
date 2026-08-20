#!/usr/bin/env python3
"""Mini-Drop continuous-profiling soak test.

The script observes the running control plane for a bounded period and writes a
machine-readable report.  It intentionally uses only the Python standard
library so it can run on a clean reviewer machine.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def api_get(base_url: str, path: str, api_key: str = "") -> Any:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    request = Request(f"{base_url.rstrip('/')}/{path.lstrip('/')}", headers=headers)
    with urlopen(request, timeout=10) as response:  # noqa: S310 - operator URL
        body = json.load(response)
    if body.get("code") != 0:
        raise RuntimeError(body.get("message") or f"API failed: {path}")
    return body.get("data")


def items_of(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return value["items"]
    return []


def snapshot(base_url: str, agent_id: str, api_key: str = "") -> dict[str, Any]:
    health = api_get(base_url, "healthz", api_key)
    agents = items_of(api_get(base_url, "agents", api_key))
    tasks = items_of(api_get(base_url, "tasks?limit=1000", api_key))
    triggers = items_of(
        api_get(base_url, "v1/continuous-diagnosis-triggers?limit=1000", api_key)
    )
    selected = next((item for item in agents if item.get("id") == agent_id), None)
    continuous = [
        item for item in tasks if item.get("collector_type") == "continuous_perf"
    ]
    return {
        "captured_at": utcnow(),
        "service": health.get("service"),
        "agent": selected,
        "continuous_task_counts": {
            status: sum(1 for item in continuous if item.get("status") == status)
            for status in ("PENDING", "RUNNING", "DONE", "FAILED", "CANCELLED")
        },
        "continuous_task_total": len(continuous),
        "trigger_total": len(triggers),
        "promoted_trigger_total": sum(
            1 for item in triggers if item.get("status") == "PROMOTED"
        ),
    }


def run_soak(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    started_at = utcnow()
    samples: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    consecutive_failures = 0
    max_consecutive_failures = 0

    while True:
        try:
            item = snapshot(args.base_url, args.agent_id, args.api_key)
            samples.append(item)
            if not item["agent"] or item["agent"].get("status") != "ONLINE":
                raise RuntimeError(f"agent {args.agent_id} is not ONLINE")
            consecutive_failures = 0
        except Exception as exc:  # report infrastructure failures without hiding them
            consecutive_failures += 1
            max_consecutive_failures = max(
                max_consecutive_failures, consecutive_failures
            )
            errors.append({"captured_at": utcnow(), "error": str(exc)})

        elapsed = time.monotonic() - started
        if elapsed >= args.duration_sec:
            break
        time.sleep(min(args.interval_sec, args.duration_sec - elapsed))

    passed = (
        bool(samples)
        and max_consecutive_failures <= args.max_consecutive_failures
        and all(
            sample.get("agent", {}).get("status") == "ONLINE"
            for sample in samples
        )
    )
    return {
        "schema_version": "continuous_soak.v1",
        "status": "PASSED" if passed else "FAILED",
        "started_at": started_at,
        "finished_at": utcnow(),
        "duration_sec": round(time.monotonic() - started, 3),
        "interval_sec": args.interval_sec,
        "agent_id": args.agent_id,
        "sample_count": len(samples),
        "error_count": len(errors),
        "max_consecutive_failures": max_consecutive_failures,
        "samples": samples,
        "errors": errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost/api")
    parser.add_argument("--agent-id", default="agent_local_demo")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--duration-sec", type=float, default=1800)
    parser.add_argument("--interval-sec", type=float, default=10)
    parser.add_argument("--max-consecutive-failures", type=int, default=2)
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("reports/continuous-soak.json"),
    )
    args = parser.parse_args()
    if args.duration_sec <= 0 or args.interval_sec <= 0:
        parser.error("duration and interval must be positive")
    return args


def main() -> int:
    args = parse_args()
    report = run_soak(args)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "status": report["status"],
        "sample_count": report["sample_count"],
        "error_count": report["error_count"],
        "report": str(args.report.resolve()),
    }, ensure_ascii=False))
    return 0 if report["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
