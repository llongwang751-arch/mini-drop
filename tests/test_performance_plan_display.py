from copy import deepcopy

import pytest

from server.app.drop_insight.diagnosis_agent import normalize_diagnosis_plan_for_display
from server.app.drop_insight.performance_criteria import performance_observation_plan


@pytest.mark.parametrize("category", ["NETWORK_DEGRADATION", "DOWNSTREAM_DEPENDENCY", "IO_LATENCY",
                                      "MEMORY_PRESSURE", "LOAD_SATURATION", "QUEUE_CONGESTION"])
@pytest.mark.parametrize("model_language", ["zh-CN", "en-US"])
def test_display_normalization_preserves_the_complete_executable_contract(category, model_language):
    plan = performance_observation_plan(category)
    proposal = {"tool_name": "collect_sys_metrics", "reasoning_summary": "使用数值判据检查当前窗口",
                "hypotheses": [{"statement": plan["statement"], "rationale": "仍需真实证据验证",
                    "expected_observations": plan["expected"], "falsification_criteria": plan["falsification"]}]}
    if model_language == "en-US":
        proposal["reasoning_summary"] = "Inspect the actual measured window"
    before = deepcopy(proposal)
    normalized = normalize_diagnosis_plan_for_display(proposal, plan)
    hypothesis = normalized["hypotheses"][0]
    assert hypothesis["expected_observations"] == plan["expected"]
    assert hypothesis["falsification_criteria"] == plan["falsification"]
    assert hypothesis["statement"] == plan["statement"]
    assert proposal == before
    assert (normalized.get("language_normalization") == "SERVER_RULE_FALLBACK") == (model_language == "en-US")
