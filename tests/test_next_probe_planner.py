from __future__ import annotations

from unittest.mock import Mock, patch

from server.app.diagnosis.next_probe_planner import propose_next_probe


def _kwargs():
    return {
        "query": "订单服务 CPU 升高",
        "symptom": "cpu_saturation",
        "hypotheses": [{"hypothesis_id": "h1", "statement": "用户态热点"}],
        "evidence_summary": [{"artifact_types": ["sys_metrics"]}],
        "missing_evidence": ["调用栈"],
        "allowed_probes": [{"probe_id": "process_cpu_profile", "name": "CPU Profile"}],
        "allowed_targets": [{"instance_id": "order-1", "runtime": "cpp"}],
        "attempted_probe_ids": ["host_process_metrics"],
        "route_priors": {"process_cpu_profile": 0.8},
        "round_index": 2,
    }


@patch("server.app.diagnosis.next_probe_planner.is_feature_enabled", return_value=True)
@patch("server.app.diagnosis.next_probe_planner.get_ai_settings")
@patch("server.app.diagnosis.next_probe_planner.chat_completions")
def test_model_can_only_select_registered_probe(chat, settings, _enabled):
    settings.return_value = Mock(provider="deepseek", model="deepseek-chat")
    chat.return_value = Mock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"tool_calls": [{"function": {"arguments": (
            '{"probe_id":"process_cpu_profile","target_instance_id":"order-1",'
            '"evidence_purpose":"FALSIFY","reason":"区分用户态热点与系统争抢",'
            '"expected_observation":"热点集中在少数业务函数",'
            '"falsification_criterion":"热点分散且系统等待占主导"}'
        )}}]}}]},
    )

    result = propose_next_probe(**_kwargs())

    assert result["probe_id"] == "process_cpu_profile"
    sent = chat.call_args.args[0]
    enum = sent["tools"][0]["function"]["parameters"]["properties"]["probe_id"]["enum"]
    assert enum == ["process_cpu_profile"]


@patch("server.app.diagnosis.next_probe_planner.is_feature_enabled", return_value=True)
@patch("server.app.diagnosis.next_probe_planner.get_ai_settings")
@patch("server.app.diagnosis.next_probe_planner.chat_completions")
def test_invalid_model_probe_is_rejected(chat, settings, _enabled):
    settings.return_value = Mock(provider="deepseek", model="deepseek-chat")
    chat.return_value = Mock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"tool_calls": [{"function": {"arguments": (
            '{"probe_id":"shell_exec","target_instance_id":"order-1",'
            '"evidence_purpose":"VERIFY","reason":"run command",'
            '"expected_observation":"x","falsification_criterion":"y"}'
        )}}]}}]},
    )

    assert propose_next_probe(**_kwargs()) is None


@patch("server.app.diagnosis.next_probe_planner.is_feature_enabled", return_value=False)
def test_disabled_feature_does_not_call_model(_enabled):
    assert propose_next_probe(**_kwargs()) is None
