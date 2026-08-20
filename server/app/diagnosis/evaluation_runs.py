"""Observable, in-process Golden evaluation runs.

The regular synchronous endpoint remains available for CI compatibility.  This
module backs the UI-oriented API where each scenario and verification stage is
visible instead of returning only a final percentage.
"""

from __future__ import annotations

import copy
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from server.app.diagnosis.eval_harness import run_evaluation
from server.app.prometheus_metrics import record_golden_evaluation


_RUNS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_MAX_RUNS = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_event(run_id: str, event: dict[str, Any]) -> None:
    with _LOCK:
        run = _RUNS[run_id]
        enriched = {
            "sequence": len(run["events"]) + 1,
            "timestamp": _now(),
            **event,
        }
        run["events"].append(enriched)
        run["stage"] = event["stage"]
        run["message"] = event["message"]
        run["completed"] = event.get("completed", run["completed"])
        run["total"] = event.get("total", run["total"])
        if event["stage"] == "SCENARIO_COMPLETED":
            run["scenario_results"].append(event["result"])

    # The evaluator is deterministic and very fast.  A small UI-only cadence
    # lets clients observe real stage transitions without changing outcomes.
    delay_ms = max(0, min(int(os.getenv("MINI_DROP_EVAL_EVENT_DELAY_MS", "80")), 1000))
    if delay_ms:
        time.sleep(delay_ms / 1000)


def _worker(run_id: str) -> None:
    try:
        report = run_evaluation(progress_callback=lambda event: _append_event(run_id, event))
        record_golden_evaluation(report)
        with _LOCK:
            run = _RUNS[run_id]
            run["status"] = "COMPLETED"
            run["report"] = report
            run["finished_at"] = _now()
    except Exception as exc:  # pragma: no cover - defensive worker boundary
        with _LOCK:
            run = _RUNS[run_id]
            run["status"] = "FAILED"
            run["stage"] = "FAILED"
            run["message"] = str(exc)
            run["error"] = str(exc)
            run["finished_at"] = _now()


def create_evaluation_run() -> dict[str, Any]:
    run_id = f"golden_{uuid.uuid4().hex[:12]}"
    run = {
        "run_id": run_id,
        "status": "RUNNING",
        "stage": "QUEUED",
        "message": "评测任务已创建，等待载入测试集",
        "completed": 0,
        "total": 0,
        "events": [],
        "scenario_results": [],
        "report": None,
        "error": None,
        "created_at": _now(),
        "finished_at": None,
    }
    with _LOCK:
        _RUNS[run_id] = run
        while len(_RUNS) > _MAX_RUNS:
            oldest = min(_RUNS, key=lambda key: _RUNS[key]["created_at"])
            if oldest == run_id:
                break
            del _RUNS[oldest]
    threading.Thread(target=_worker, args=(run_id,), daemon=True).start()
    return copy.deepcopy(run)


def get_evaluation_run(run_id: str) -> dict[str, Any] | None:
    with _LOCK:
        run = _RUNS.get(run_id)
        return copy.deepcopy(run) if run else None
