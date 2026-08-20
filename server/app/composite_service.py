"""Composite task / DAG aggregation (guide §3.8).

A composite task owns a list of child tasks. Child outcomes are reduced to a
single composite status by a declared strategy:

- ALL_REQUIRED  -> every required child must succeed (optional failures tolerated)
- BEST_EFFORT   -> at least one success is enough for a partial success
- QUORUM        -> a target success count must be reached

The aggregation itself is a pure function; the repository drives it over the
task table inside one transaction.
"""

from __future__ import annotations

from typing import Any

TERMINAL_COMPOSITE_STATUSES = {"SUCCEEDED", "FAILED", "PARTIAL", "CANCELLED"}


def child_outcome(task_status: str | None) -> str:
    """Map a task status to a composite-observable outcome."""
    if task_status == "DONE":
        return "succeeded"
    if task_status == "FAILED":
        return "failed"
    if task_status == "CANCELLED":
        return "cancelled"
    return "running"


def aggregate_status(
    strategy: str,
    outcomes: list[dict[str, Any]],
    required_success_count: int | None = None,
) -> str:
    """Reduce child outcomes to a composite status.

    ``outcomes`` items carry ``{"status": <succeeded|failed|cancelled|running>,
    "role": <required|optional>}``. Returns one of
    RUNNING / SUCCEEDED / FAILED / PARTIAL.
    """
    if any(item["status"] == "running" for item in outcomes):
        return "RUNNING"
    successes = sum(1 for item in outcomes if item["status"] == "succeeded")
    required = [item for item in outcomes if item["role"] == "required"]
    required_ok = (
        all(item["status"] == "succeeded" for item in required)
        if required
        else True
    )
    if strategy == "ALL_REQUIRED":
        return "SUCCEEDED" if required_ok and successes > 0 else "FAILED"
    if strategy == "QUORUM":
        target = (
            required_success_count
            if required_success_count is not None
            else len(required)
        )
        return "SUCCEEDED" if successes >= target else "FAILED"
    # BEST_EFFORT
    if successes > 0:
        return "PARTIAL" if successes < len(outcomes) else "SUCCEEDED"
    return "FAILED"
