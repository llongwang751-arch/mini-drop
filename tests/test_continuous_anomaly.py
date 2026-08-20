from agent.mini_drop_agent.collectors.continuous_anomaly import (
    WindowProfile,
    detect_profile_anomaly,
    summarize_top,
)


def _window(index: int, samples: int, top1: float) -> WindowProfile:
    return WindowProfile(index, samples, top1, f"hot_{index}")


def test_summarize_top_keeps_auditable_window_facts():
    result = summarize_top(
        3,
        [
            {"name": "service.hot", "samples": 80, "percent": 72.7},
            {"name": "runtime", "samples": 30, "percent": 27.3},
        ],
    )

    assert result.window_index == 3
    assert result.sample_total == 110
    assert result.top1_percent == 72.7
    assert result.top_function == "service.hot"


def test_detector_requires_baseline_before_triggering():
    result = detect_profile_anomaly([_window(0, 100, 20), _window(1, 500, 80)])

    assert result["triggered"] is False
    assert result["reason"] == "insufficient_baseline_windows"


def test_detector_ignores_normal_profile_variation():
    result = detect_profile_anomaly(
        [_window(0, 100, 22), _window(1, 110, 24), _window(2, 115, 25)]
    )

    assert result["triggered"] is False
    assert result["reason"] == "within_baseline"


def test_detector_triggers_on_sample_surge_with_evidence():
    result = detect_profile_anomaly(
        [_window(0, 100, 20), _window(1, 110, 21), _window(2, 260, 23)]
    )

    assert result["triggered"] is True
    assert "cpu_sample_surge" in result["reason"]
    assert result["deltas"]["sample_ratio"] > 2


def test_detector_triggers_on_hotspot_concentration_shift():
    result = detect_profile_anomaly(
        [_window(0, 100, 15), _window(1, 110, 18), _window(2, 105, 61)]
    )

    assert result["triggered"] is True
    assert "hotspot_concentration_shift" in result["reason"]
    assert result["current"]["top_function"] == "hot_2"
