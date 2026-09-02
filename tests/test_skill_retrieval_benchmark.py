from server.app.drop_insight.retrieval_benchmark import load_benchmark, run_benchmark


def test_hybrid_retrieval_beats_legacy_on_frozen_regression_set() -> None:
    report = run_benchmark(
        load_benchmark("tests/fixtures/skill_retrieval_benchmark.json")
    )
    metrics = report["metrics"]

    assert report["case_count"] >= 16
    assert metrics["hybrid"]["accuracy"] > metrics["legacy_structured"]["accuracy"]
    assert metrics["hybrid"]["negative_rejection_rate"] == 1.0
    assert metrics["hybrid"]["positive_recall_at_1"] >= 0.9
