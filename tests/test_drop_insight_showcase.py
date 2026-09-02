from server.app.drop_insight.showcase import (
    get_mentor_complex_showcase,
    get_showcase_diagnostic_case,
    list_showcase_diagnostic_cases,
)


def test_complex_showcase_preserves_real_exploration_before_skill_generation():
    showcase = get_mentor_complex_showcase()
    first_run = showcase["first_run"]
    skill = showcase["generated_skill"]

    assert showcase["replay_mode"] is True
    assert showcase["status"] == "COMPLETED"
    assert len(first_run["rounds"]) >= 5
    assert len(first_run["nodes"]) >= 14
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


def test_showcase_library_contains_multiple_synchronised_multi_round_trees():
    payload = get_mentor_complex_showcase()
    cases = payload["cases"]

    assert len(cases) >= 3
    assert len({case["case_id"] for case in cases}) == len(cases)
    for case in cases:
        first_run = case["first_run"]
        rounds = first_run["rounds"]
        nodes = first_run["nodes"]
        node_ids = {node["id"] for node in nodes}

        assert case["status"] == "COMPLETED"
        assert len(rounds) >= 5
        assert sum(item["duration_seconds"] for item in rounds) == first_run["duration_seconds"]
        assert {node["state"] for node in nodes} >= {"refuted", "confirmed", "unvisited"}
        assert all(node["parent_id"] is None or node["parent_id"] in node_ids for node in nodes)
        assert len(first_run["switches"]) >= 3
        assert case["generated_skill"]["source_case"] == case["case_id"]
        assert case["counterexample"]["false_transfer"] is False


def test_complex_showcase_is_projected_as_a_multi_round_diagnosis_record():
    rows = list_showcase_diagnostic_cases()
    assert len(rows) >= 3
    assert all(row["source"] == "controlled_showcase" for row in rows)
    assert all(row["canonical_status"] == "COMPLETED" for row in rows)

    detail = get_showcase_diagnostic_case("showcase-python-native-oversubscription-002")
    native = detail["native_payload"]
    rounds = {item["round_index"] for item in native["hypotheses"]}
    report = native["reports"][0]
    assert rounds == {1, 2, 3, 4, 5, 6}
    assert len(native["tool_calls"]) == 6
    assert len(report["exploration_nodes"]) >= 7
    assert len(report["exploration_switches"]) >= 3
    assert report["verification"]["status"] == "VERIFIED"
    assert native["provenance"]["kind"] == "CONTROLLED_REPLAY"
    assert native["provenance"]["live_collection"] is False
    decisions = {item["role"]: item["classification"]["decision"] for item in native["evidence"]}
    assert decisions["SUPPORT"] == "ACCEPT_SUPPORT"
    assert decisions["COUNTER"] == "ACCEPT_COUNTER"

    database = get_showcase_diagnostic_case("showcase-database-lock-chain-003")["native_payload"]
    database_report = database["reports"][0]
    assert len(database["hypotheses"]) == 6
    assert len(database_report["exploration_nodes"]) >= 20
    assert len(database_report["exploration_switches"]) >= 5
