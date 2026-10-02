"""Verify that every interview fault runtime is exposed and safely controllable.

The API key is accepted only through an environment variable and is never
printed or written to the report.  The script asserts the exact public catalog
size, then starts and stops every newly added allow-listed fault in Go, Java,
and C++.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from verify_interview_demo import AcceptanceError, Client, items_of


EXPECTED_SCENARIO_COUNT = 21
DEFAULT_SCENARIOS = (
    "go-memory-growth",
    "go-file-io",
    "java-offheap-growth",
    "java-file-io",
    "cpp-file-io",
    "cpp-downstream-latency",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


def run_smoke(
    client: Client,
    *,
    scenario_ids: tuple[str, ...],
    duration_seconds: int,
) -> dict[str, Any]:
    plaza = client.request("GET", "/api/v2/showcases/fault-plaza") or {}
    scenarios = items_of(plaza.get("scenarios"))
    _require(
        len(scenarios) == EXPECTED_SCENARIO_COUNT,
        f"故障广场应有 {EXPECTED_SCENARIO_COUNT} 个场景，实际为 {len(scenarios)} 个",
    )
    by_id = {str(item.get("scenario_id")): item for item in scenarios}
    results: list[dict[str, Any]] = []

    for scenario_id in scenario_ids:
        scenario = by_id.get(scenario_id) or {}
        _require(bool(scenario), f"故障广场缺少场景 {scenario_id}")
        _require(
            scenario.get("available") is True,
            f"场景 {scenario_id} 当前不可用：{scenario.get('unavailable_reason') or '服务端未说明'}",
        )
        started: dict[str, Any] = {}
        try:
            started = client.request(
                "POST",
                f"/api/v2/showcases/fault-plaza/{scenario_id}/start",
                {"duration_seconds": duration_seconds},
            ) or {}
            _require(started.get("status") == "RUNNING", f"场景 {scenario_id} 未进入运行状态")
            _require(
                bool((started.get("scenario") or {}).get("active")),
                f"场景 {scenario_id} 启动响应未标记为活动",
            )
        finally:
            stopped = client.request(
                "POST",
                f"/api/v2/showcases/fault-plaza/{scenario_id}/stop",
            ) or {}
            _require(
                not bool((stopped.get("scenario") or {}).get("active")),
                f"场景 {scenario_id} 停止后仍处于活动状态",
            )
        results.append(
            {
                "scenario_id": scenario_id,
                "title": scenario.get("title"),
                "target_runtime": scenario.get("target_runtime"),
                "minimum_diagnosis_rounds": scenario.get("minimum_diagnosis_rounds"),
                "started": True,
                "stopped": True,
                "auto_stop_seconds": started.get("auto_stop_seconds"),
            }
        )

    return {
        "schema": "mini-drop.fault-plaza-runtime-smoke.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "authentication": "X-API-Key_FROM_ENV_REDACTED",
        "passed": True,
        "catalog": {
            "status": plaza.get("status"),
            "scenario_count": len(scenarios),
            "labs": plaza.get("labs"),
        },
        "runtime_smoke": results,
        "cleanup": {"all_started_faults_stopped": True},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="验收故障广场的多运行时启动与安全停止链路")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key-env", default="MINI_DROP_API_KEY")
    parser.add_argument("--duration-seconds", type=int, default=30)
    parser.add_argument("--scenario", action="append", dest="scenarios")
    parser.add_argument("--insecure", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not 15 <= args.duration_seconds <= 300:
        raise SystemExit("duration-seconds 必须在 15 到 300 之间")
    api_key = os.getenv(args.api_key_env, "").strip()
    if not api_key:
        raise SystemExit(f"缺少 API Key 环境变量：{args.api_key_env}")
    scenarios = tuple(args.scenarios or DEFAULT_SCENARIOS)
    client = Client(args.base_url, api_key, args.insecure)
    try:
        report = run_smoke(
            client,
            scenario_ids=scenarios,
            duration_seconds=args.duration_seconds,
        )
    except AcceptanceError as error:
        report = {
            "schema": "mini-drop.fault-plaza-runtime-smoke.v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "authentication": "X-API-Key_FROM_ENV_REDACTED",
            "passed": False,
            "error": str(error)[:2000],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output), "passed": report["passed"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
