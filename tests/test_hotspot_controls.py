from copy import deepcopy

import pytest

from scripts.verify_hotspot_controls import evaluate, pprof_result


def case(runtime="python"):
    return {"runtime": runtime, "windows": {
        name: {"pid": 42, "create_time": 100, "duration_seconds": 4, "cpu_percent": cpu}
        for name, cpu in [("baseline", 1), ("fault", 80), ("recovery", 2)]},
        "injection_active_through_fault_window": True, "cleanup_verified": True,
        "profile": {"function": "source_hot_function", "new_hot_samples": 100, "new_total_samples": 100}
        if runtime == "python" else {"function": "main.goCPUHotFunction", "sample_cpu_seconds": 4.5, "cumulative_percent": 98}}


@pytest.mark.parametrize("runtime", ["python", "go"])
def test_independent_control_never_promotes_ai_or_code_fix(runtime):
    result = evaluate(case(runtime))
    assert result["status"] == "CONTROL_VERIFIED"
    assert result["ai_root_cause_verified"] is False and result["fix_verified"] is False


@pytest.mark.parametrize("field,value,reason", [
    ("pid", 43, "TARGET_CHANGED"), ("create_time", 101, "TARGET_CHANGED"),
    ("duration_seconds", 2.99, "INVALID_CPU_WINDOW"), ("cpu_percent", float("nan"), "INVALID_CPU_WINDOW"),
    ("cpu_percent", 10, "CPU_EFFECT_NOT_OBSERVED"),
])
def test_wrong_target_short_window_and_missing_effect_rejected(field, value, reason):
    data = case()
    data["windows"]["fault"][field] = value
    result = evaluate(data)
    assert result["status"] == "REJECTED" and reason in result["reasons"]


def test_counter_must_show_recovery_and_cleanup():
    data = case()
    data["windows"]["recovery"]["cpu_percent"] = 50
    data["cleanup_verified"] = False
    assert set(evaluate(data)["reasons"]) == {"CPU_NOT_RECOVERED", "CLEANUP_NOT_CONFIRMED"}


def test_injection_must_span_profile_window_and_booleans_are_strict():
    data = case()
    data["injection_active_through_fault_window"] = "true"
    data["cleanup_verified"] = "false"
    assert set(evaluate(data)["reasons"]) == {"INJECTION_NOT_CONFIRMED", "CLEANUP_NOT_CONFIRMED"}


@pytest.mark.parametrize("profile", [
    {"function": "unrelated", "new_hot_samples": 100, "new_total_samples": 100},
    {"function": "source_hot_function", "new_hot_samples": 19, "new_total_samples": 100},
    {"function": "source_hot_function", "new_hot_samples": 100, "new_total_samples": 20},
    {"function": "source_hot_function", "new_hot_samples": float("nan"), "new_total_samples": 100},
])
def test_stale_or_wrong_python_samples_cannot_support_control(profile):
    data = case()
    data["profile"] = profile
    assert "SOURCE_SAMPLES_INSUFFICIENT" in evaluate(data)["reasons"]


def test_pprof_requires_target_symbol_and_actual_sample_duration():
    text = "Duration: 5.12s, Total samples = 4.96s (96.9%)\n  0.01s  0.2% 0.2% 4.90s 98.8% main.goCPUHotFunction\n"
    assert pprof_result(text) == {"function": "main.goCPUHotFunction", "cumulative_percent": 98.8,
                                  "sample_cpu_seconds": 4.96}
    assert pprof_result(text.replace("4.96s", "4960ms"))["sample_cpu_seconds"] == 4.96
    for value in ["", text.replace("main.goCPUHotFunction", "runtime.foo"), text.replace("4.96s", "0.5s")]:
        data = case("go")
        data["profile"] = pprof_result(value)
        assert evaluate(data)["status"] == "REJECTED"


def test_same_high_cpu_without_contrast_is_not_confirmation():
    data = case()
    data["windows"]["baseline"]["cpu_percent"] = 70
    before = deepcopy(data)
    assert "CPU_EFFECT_NOT_OBSERVED" in evaluate(data)["reasons"]
    assert before == data


def test_go_profile_cannot_substitute_an_unrelated_hot_function():
    data = case("go")
    data["profile"]["function"] = "unrelated.hotLoop"
    assert "PPROF_HOTSPOT_INSUFFICIENT" in evaluate(data)["reasons"]
