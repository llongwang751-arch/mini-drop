from server.app.drop_insight.showcase import get_mentor_complex_showcase


def test_complex_showcase_preserves_real_exploration_before_skill_generation():
    showcase = get_mentor_complex_showcase()
    first_run = showcase["first_run"]
    skill = showcase["generated_skill"]

    assert showcase["replay_mode"] is True
    assert showcase["status"] == "COMPLETED"
    assert len(first_run["rounds"]) >= 5
    assert sum(item["duration_seconds"] for item in first_run["rounds"]) == first_run["duration_seconds"]
    assert {item["state"] for item in first_run["nodes"]} >= {"refuted", "confirmed", "unvisited"}
    assert len(first_run["switches"]) >= 3
    assert skill["candidate_id"].startswith("skill_candidate_")
    assert skill["source_report_id"].startswith("report_")
    assert all(gate["passed"] == gate["total"] for gate in skill["gate_results"])


def test_complex_showcase_proves_reuse_and_rejects_false_transfer():
    showcase = get_mentor_complex_showcase()
    first_run = showcase["first_run"]
    second_run = showcase["second_run"]
    counterexample = showcase["counterexample"]

    assert second_run["skill_hit"] is True
    assert second_run["root_cause_correct"] is True
    assert second_run["tool_calls"] < first_run["tool_calls"]
    assert second_run["duration_seconds"] < first_run["duration_seconds"]
    assert counterexample["skill_considered"] is True
    assert counterexample["skill_applied"] is False
    assert counterexample["false_transfer"] is False
