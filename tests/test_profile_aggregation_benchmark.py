from scripts.benchmark_profile_aggregation import run


def test_lossless_preaggregation_preserves_counts_and_reduces_transport_size():
    report = run(sample_count=20_000)
    assert report["correctness"]["exact_stack_counts_preserved"] is True
    assert report["ratios"]["plain_size_reduction"] > 0.95
    assert report["preaggregated_folded"]["bytes"] < report["raw"]["bytes"]
    assert "does not measure" in report["measurement_boundary"]
