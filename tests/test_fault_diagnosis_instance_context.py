import pytest

from scripts.verify_interview_demo import _run_diagnosis


@pytest.mark.parametrize("target,expected", [
    ({"agent_id": "operator-lab", "pid": 77}, {"agent_id": "operator-lab"}),
    ({}, {}),
    (None, {}),
])
def test_acceptance_preserves_operator_instance_without_injecting_oracle_pid(target, expected):
    captured = []

    class StopAfterCreate:
        def request(self, method, path, payload):
            assert method == "POST" and path == "/api/v2/diagnoses"
            captured.append(payload)
            raise RuntimeError("capture only")

    with pytest.raises(RuntimeError, match="capture only"):
        _run_diagnosis(StopAfterCreate(), {"query": "诊断进程名为 java 的延迟", "target": target},
                       policy="AUTO", demo_agent_id="oracle-host", demo_pid=999,
                       timeout_seconds=240, poll_seconds=2, minimum_rounds=3)
    assert captured[0]["target"] == expected
    assert captured[0]["budget"]["min_diagnosis_rounds"] == 3
