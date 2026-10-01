from types import SimpleNamespace

import pytest

from server.app.drop_insight.performance_criteria import evaluate_performance_criterion
from server.app.drop_insight.hypothesis_predicate import _structured_signal_predicate
from server.app.metric_analyzers import _derive_signals, _application_window_average


def hypothesis(expected, falsification=None):
    return SimpleNamespace(statement="性能待验证", expected_observations_json=expected,
                           falsification_criteria_json=falsification or [])


@pytest.mark.parametrize("criterion", [
    "网络收发队列积压", "丢包和重传率升高", "network_latency.packet_loss >= 1",
    "network_latency.average_latency_ms >= 100 and packet loss is high",
])
def test_http_wait_cannot_prove_packet_loss_queue_or_compound_claim(criterion):
    metadata = {"signals": {"network_latency": {"detected": True, "metrics": {"average_latency_ms": 240}}}}
    assert _structured_signal_predicate(hypothesis([criterion]), metadata) is None


def test_writing_bytes_cannot_prove_fsync_or_disk_latency():
    metadata = {"signals": {"io_activity": {"detected": True, "metrics": {"io_bytes_written_delta": 999999}}}}
    assert _structured_signal_predicate(hypothesis(["fsync 延迟超过100ms"]), metadata) is None
    assert _structured_signal_predicate(hypothesis(["io_latency.latency_p95_us >= 100000"]), metadata) is None
    result = _structured_signal_predicate(hypothesis(["io_activity.io_bytes_written_delta >= 65536"]), metadata)
    assert result["criterion_indexes"] == [0]
    assert result["claim_scope"] == "BOUNDED_OBSERVATION"


@pytest.mark.parametrize("value", [None, True, "240", -1, float("nan"), float("inf")])
def test_missing_or_invalid_measurement_never_becomes_zero_or_support(value):
    signals = {"downstream_latency": {"metrics": {"average_latency_ms": value}}}
    assert evaluate_performance_criterion("downstream_latency.average_latency_ms >= 100", signals) is None
    assert evaluate_performance_criterion("downstream_latency.average_latency_ms < 100", signals) is None


def test_only_satisfied_exact_slots_earn_coverage_and_counter_is_retained():
    metadata = {"signals": {"downstream_latency": {"metrics": {"average_latency_ms": 240}}}}
    result = _structured_signal_predicate(hypothesis([
        "downstream_latency.average_latency_ms >= 100", "downstream_latency.average_latency_ms >= 500",
        "downstream_latency.request_count_delta > 0", "下游导致全部延迟",
    ]), metadata)
    assert result["criterion_indexes"] == [0]
    result = _structured_signal_predicate(hypothesis([], ["downstream_latency.average_latency_ms < 300"]), metadata)
    assert result["outcome"] == "COUNTER"


def test_retained_memory_is_not_growth_and_compute_activity_is_not_high_cpu():
    signals = _derive_signals({"process_cpu_core_usage": 0.5, "vmrss_mb_delta": 0}, {
        "max": {"retained_memory_mb": 96}, "delta": {"retained_memory_mb": 0, "cpu_operations": 1000},
    })
    assert "memory_growth" not in signals and "cpu_hotspot" not in signals
    assert signals["memory_retention"]["metrics"]["retained_memory_mb"] == 96
    signals = _derive_signals({"vmrss_mb_delta": 10}, None)
    assert signals["memory_growth"]["metrics"]["rss_delta_mb"] == 10


def test_old_slow_calls_do_not_make_recovered_window_abnormally_slow():
    app = {"before": {"network_requests": 100, "network_average_latency_ms": 240},
           "after": {"network_requests": 110, "network_average_latency_ms": 219.09},
           "delta": {"network_requests": 10}, "max": {"network_average_latency_ms": 240}}
    assert _application_window_average(app, "network_requests", "network_average_latency_ms") == pytest.approx(9.99)
    signal = _derive_signals({}, app)["network_latency"]
    assert signal["detected"] is False
    assert signal["metrics"]["average_latency_ms"] == pytest.approx(9.99)
    assert _structured_signal_predicate(hypothesis([], ["network_latency.average_latency_ms < 100"]),
        {"signals": {"network_latency": signal}})["outcome"] == "COUNTER"


@pytest.mark.parametrize("before,after,mean", [(10, 10, 100), (10, 5, 100), (10, 11, 0), (None, 11, 100), (1.5, 11, 100)])
def test_invalid_or_incomplete_latency_window_is_unknown(before, after, mean):
    app = {"before": {"network_requests": before, "network_average_latency_ms": 100},
           "after": {"network_requests": after, "network_average_latency_ms": mean}}
    assert _application_window_average(app, "network_requests", "network_average_latency_ms") is None
