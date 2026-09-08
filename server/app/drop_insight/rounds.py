"""Round semantics shared by diagnosis contracts and UI projections.

``DropInsightHypothesisModel.round_index`` records when a tree node was born
(its search depth). LATS can later backtrack to an older unvisited sibling, so
that value is not the same thing as the sequential round in which the node was
actually selected, observed and reported. The durable ``lats.node_selected``
event already carries a monotonic ``iteration``; that is the authoritative
execution round for report-bearing diagnosis progress.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _event_value(event: object, name: str, default: Any = None) -> Any:
    if isinstance(event, Mapping):
        return event.get(name, default)
    return getattr(event, name, default)


def _positive_int(value: object, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def selection_iteration_by_hypothesis(events: Iterable[object]) -> dict[str, int]:
    """Map each selected hypothesis to its durable LATS execution iteration.

    Old fixtures may not carry ``iteration``. Their stable event order is used
    as a compatibility fallback, while persisted production events keep their
    explicit monotonic value.
    """

    selected: dict[str, int] = {}
    ordinal = 0
    for event in events:
        if _event_value(event, "event_type") != "lats.node_selected":
            continue
        ordinal += 1
        payload = _event_value(event, "payload_json", {}) or {}
        if not isinstance(payload, Mapping):
            continue
        node_id = str(payload.get("node_id") or "")
        if not node_id.startswith("hypothesis:"):
            continue
        hypothesis_id = node_id.removeprefix("hypothesis:")
        iteration = _positive_int(payload.get("iteration"), ordinal)
        selected.setdefault(hypothesis_id, iteration)
    return selected


def effective_round_by_hypothesis(
    events: Iterable[object],
    birth_round_by_hypothesis: Mapping[str, int],
) -> dict[str, int]:
    """Return execution rounds, falling back to birth depth for legacy paths."""

    selected = selection_iteration_by_hypothesis(events)
    return {
        hypothesis_id: selected.get(hypothesis_id, max(1, int(birth_round or 1)))
        for hypothesis_id, birth_round in birth_round_by_hypothesis.items()
    }


def report_execution_rounds(
    events: Iterable[object],
    report_hypothesis_ids: Iterable[str],
    birth_round_by_hypothesis: Mapping[str, int],
) -> set[int]:
    """Return distinct real execution rounds that reached a persisted Report."""

    effective = effective_round_by_hypothesis(events, birth_round_by_hypothesis)
    return {
        effective.get(
            hypothesis_id,
            max(1, int(birth_round_by_hypothesis.get(hypothesis_id, 1) or 1)),
        )
        for hypothesis_id in report_hypothesis_ids
        if hypothesis_id
    }
