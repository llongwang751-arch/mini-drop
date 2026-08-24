#!/usr/bin/env python3
"""Run the current Mini-Drop RCA pipeline for one multitrack evidence input.

The benchmark adapter passes only public incident/replay evidence to stdin or
to a JSON file.  Private oracle files are intentionally outside this driver.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
# The delivered benchmark runner supplies DeepSeek-shaped defaults. The
# project may use another OpenAI-compatible endpoint, so its own deployment
# configuration remains authoritative for a native Mini-Drop evaluation.
load_dotenv(ROOT / ".env", override=True)

from server.app.rca.report import run_diagnosis_context


def _load_input() -> dict:
    if len(sys.argv) > 1:
        return json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    return json.load(sys.stdin)


def main() -> int:
    payload = _load_input()
    metadata = payload.get("task_metadata") or {}
    task = SimpleNamespace(
        id=metadata.get("task_id", "multitrack-replay"),
        status=metadata.get("status", "DONE"),
        status_reason=metadata.get("status_reason", "benchmark replay"),
        collector_type=metadata.get("collector_type", "benchmark_replay"),
        agent_id=metadata.get("agent_id", "benchmark-agent"),
        target_pid=metadata.get("target_pid", 1),
        duration_sec=metadata.get("duration_sec", 15),
        sample_rate=metadata.get("sample_rate", 99),
    )
    outcome = run_diagnosis_context(
        task_id=str(task.id),
        task_record=task,
        top_functions=payload.get("top_functions"),
        ebpf_metrics=payload.get("ebpf_metrics"),
        sys_metrics=payload.get("sys_metrics"),
        suggestions=payload.get("suggestions"),
        failure_events=payload.get("failure_events"),
        baseline_diff=payload.get("baseline_diff"),
        agent_stats=payload.get("agent_stats"),
        external_tool_results=payload.get("tool_results"),
        auto_execute_safe=False,
    )
    print(
        json.dumps(
            {
                "report": outcome.report.report.model_dump(mode="json"),
                "validation": {
                    "generation_mode": outcome.report.generation_mode,
                    "schema_validated": outcome.report.schema_validated,
                    "reference_validated": outcome.report.reference_validated,
                    "semantic_validated": outcome.report.semantic_validated,
                    "model_invoked": outcome.report.model_invoked,
                    "fallback_reason": outcome.report.fallback_reason,
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
