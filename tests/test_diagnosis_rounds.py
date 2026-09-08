from __future__ import annotations

from server.app.drop_insight.rounds import (
    effective_round_by_hypothesis,
    report_execution_rounds,
    selection_iteration_by_hypothesis,
)


def _selected(hypothesis_id: str, iteration: int) -> dict:
    return {
        "event_type": "lats.node_selected",
        "payload_json": {
            "node_id": f"hypothesis:{hypothesis_id}",
            "iteration": iteration,
        },
    }


def test_selection_iteration_is_the_real_round_after_backtracking() -> None:
    events = [
        _selected("root", 1),
        _selected("child", 2),
        _selected("old-sibling", 3),
    ]
    birth_rounds = {"root": 1, "child": 2, "old-sibling": 2}

    assert selection_iteration_by_hypothesis(events) == {
        "root": 1,
        "child": 2,
        "old-sibling": 3,
    }
    assert effective_round_by_hypothesis(events, birth_rounds) == {
        "root": 1,
        "child": 2,
        "old-sibling": 3,
    }
    assert report_execution_rounds(
        events,
        ["root", "child", "old-sibling"],
        birth_rounds,
    ) == {1, 2, 3}


def test_legacy_diagnosis_falls_back_to_hypothesis_birth_round() -> None:
    birth_rounds = {"legacy-one": 1, "legacy-two": 2}

    assert report_execution_rounds(
        [],
        ["legacy-one", "legacy-two"],
        birth_rounds,
    ) == {1, 2}
