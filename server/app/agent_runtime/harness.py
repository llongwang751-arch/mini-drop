"""Authority-preserving input/output guards around model decisions."""

from __future__ import annotations

from typing import Any


SCOPE_CANDIDATE_FIELDS = (
    "binding_id",
    "service",
    "environment",
    "instance",
    "process",
    "collector_capabilities",
    "eligible",
)


def safe_scope_candidates(
    candidates: list[dict[str, Any]],
    *,
    limit: int = 100,
) -> tuple[dict[str, Any], ...]:
    """Expose display metadata and opaque authority handles, never raw PID data."""

    return tuple(
        {key: item.get(key) for key in SCOPE_CANDIDATE_FIELDS}
        for item in candidates[:limit]
        if item.get("eligible") is True and str(item.get("binding_id") or "")
    )


def selected_authorized_candidate(
    binding_id: str,
    candidates: tuple[dict[str, Any], ...],
) -> dict[str, Any] | None:
    for candidate in candidates:
        if candidate.get("eligible") is True and candidate.get("binding_id") == binding_id:
            return candidate
    return None
