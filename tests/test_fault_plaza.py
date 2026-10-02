from __future__ import annotations

import pytest

from server.app.drop_insight import fault_plaza


@pytest.fixture(autouse=True)
def configured_lab_agent(monkeypatch):
    monkeypatch.setenv("MINI_DROP_FAULT_LAB_AGENT_ID", "test-lab-agent")


def test_missing_lab_agent_rejects_before_injection(monkeypatch):
    monkeypatch.delenv("MINI_DROP_FAULT_LAB_AGENT_ID")
    calls = []
    monkeypatch.setattr(fault_plaza, "_request_json", lambda *args, **kwargs: calls.append(args) or {})
    with pytest.raises(fault_plaza.FaultPlazaError, match="AGENT_ID"):
        fault_plaza.start_fault_scenario("java-gc-pressure", 30)
    assert calls == []


@pytest.mark.parametrize("scenario_id", ["java-gc-pressure", "go-cpu-hotspot", "cpu-hotspot", "cpp-file-io"])
def test_fault_request_carries_operator_agent_without_snapshot_pid(monkeypatch, scenario_id):
    monkeypatch.setattr(fault_plaza, "_request_json", lambda *args, **kwargs: {"pid": 123})
    request = fault_plaza.start_fault_scenario(scenario_id, 30)["diagnosis_request"]
    assert request["target"] == {"agent_id": "test-lab-agent"}


def _disable_all_labs(monkeypatch) -> None:
    for environment in fault_plaza._LAB_ENVIRONMENTS.values():
        monkeypatch.delenv(environment, raising=False)


def test_fault_plaza_is_explicitly_disabled_without_server_url(monkeypatch) -> None:
    _disable_all_labs(monkeypatch)

    result = fault_plaza.get_fault_plaza()

    assert result["status"] == "DISABLED"
    assert result["production_safe"] is False
    assert len(result["scenarios"]) == 21
    assert all(item["safety"]["allow_listed"] for item in result["scenarios"])
    assert all(item["safety"]["auto_stop"] for item in result["scenarios"])
    assert all(
        item["duration_options_seconds"] == [30, 60, 120]
        for item in result["scenarios"]
    )
    assert all(item["available"] is False for item in result["scenarios"])


def test_fault_plaza_marks_only_published_skill_routes_as_ab_supported(monkeypatch) -> None:
    _disable_all_labs(monkeypatch)

    scenarios = {
        item["scenario_id"]: item for item in fault_plaza.get_fault_plaza()["scenarios"]
    }

    assert scenarios["cpu-hotspot"]["supports_skill_ab"] is True
    assert scenarios["cpu-hotspot"]["skill_ab_unavailable_reason"] == ""
    assert scenarios["queue-backlog"]["supports_skill_ab"] is True
    assert scenarios["go-cpu-hotspot"]["supports_skill_ab"] is True
    assert scenarios["load-saturation"]["supports_skill_ab"] is False
    assert "尚未发布" in scenarios["load-saturation"]["skill_ab_unavailable_reason"]


def test_fault_plaza_exposes_multilanguage_routes_and_multiround_contract(monkeypatch) -> None:
    _disable_all_labs(monkeypatch)

    scenarios = fault_plaza.get_fault_plaza()["scenarios"]

    assert {item["target_runtime"] for item in scenarios} == {
        "Python",
        "Go",
        "Java",
        "C++",
    }
    assert len({item["scenario_id"] for item in scenarios}) == 21
    assert {
        "go-memory-growth",
        "go-file-io",
        "java-offheap-growth",
        "java-file-io",
        "cpp-file-io",
        "cpp-downstream-latency",
    } <= {item["scenario_id"] for item in scenarios}
    assert {item["family"] for item in scenarios} >= {
        "CPU",
        "内存",
        "I/O",
        "网络",
        "Java 运行时",
        "锁竞争",
    }
    assert all(2 <= item["minimum_diagnosis_rounds"] <= 4 for item in scenarios)
    assert all(len(item["investigation_stages"]) >= 3 for item in scenarios)
    assert all("诊断" in item["diagnosis_query"] for item in scenarios)
    assert all("acceptance_level" not in item and "latest_acceptance" not in item for item in scenarios)
    expected_process = {
        "python": "python-hotspot",
        "go": "go-hotspot",
        # The Agent reads /proc/<pid>/comm; the JVM reports its real process
        # name as "java", not the Docker service label "java-hotspot".
        "java": "进程名为 java",
        "cpp": "cpp-hotspot",
    }
    assert all(
        expected_process[item["lab_key"]] in item["diagnosis_query"]
        for item in scenarios
    )


def test_fault_plaza_reports_availability_per_runtime(monkeypatch) -> None:
    _disable_all_labs(monkeypatch)
    monkeypatch.setenv("MINI_DROP_FAULT_LAB_URL", "http://python-lab:8081")
    monkeypatch.setenv("MINI_DROP_FAULT_LAB_JAVA_URL", "http://java-lab:8082")

    def fake_request(method, path, payload=None, *, lab_key="python"):
        assert method == "GET"
        assert path == "/snapshot"
        return {"runtime": lab_key, "gc_fault_active": lab_key == "java"}

    monkeypatch.setattr(fault_plaza, "_request_json", fake_request)
    result = fault_plaza.get_fault_plaza()
    scenarios = {item["scenario_id"]: item for item in result["scenarios"]}

    assert result["status"] == "READY"
    assert result["labs"]["python"]["status"] == "READY"
    assert result["labs"]["java"]["status"] == "READY"
    assert result["labs"]["go"]["status"] == "DISABLED"
    assert scenarios["java-gc-pressure"]["available"] is True
    assert scenarios["java-gc-pressure"]["active"] is True
    assert scenarios["go-cpu-hotspot"]["available"] is False


def test_start_fault_uses_only_allow_listed_endpoint_and_bounded_duration(monkeypatch) -> None:
    calls = []

    def fake_request(method, path, payload=None, *, lab_key="python"):
        calls.append((method, path, payload, lab_key))
        return {"fault_active": True}

    monkeypatch.setattr(fault_plaza, "_request_json", fake_request)

    result = fault_plaza.start_fault_scenario("cpu-hotspot", 999)

    assert calls == [
        ("POST", "/faults/cpu/start", {"duration_seconds": 300}, "python")
    ]
    assert result["status"] == "RUNNING"
    assert result["scenario"]["active"] is True
    assert result["diagnosis_request"]["skill_policy"] == "AUTO"
    assert result["diagnosis_request"]["budget"] == {
        "min_diagnosis_rounds": 3,
        "max_diagnosis_rounds": 4,
    }
    assert result["minimum_diagnosis_rounds"] == 3


def test_multilanguage_start_uses_runtime_specific_lab(monkeypatch) -> None:
    calls = []

    def fake_request(method, path, payload=None, *, lab_key="python"):
        calls.append((method, path, payload, lab_key))
        return {"network_fault_active": True}

    monkeypatch.setattr(fault_plaza, "_request_json", fake_request)
    result = fault_plaza.start_fault_scenario("go-network-latency", 5)

    assert calls == [
        (
            "POST",
            "/faults/network/start",
            {"delay_ms": 240, "duration_seconds": 15},
            "go",
        )
    ]
    assert result["scenario"]["active"] is True
    assert result["scenario"]["target_runtime"] == "Go"


@pytest.mark.parametrize(
    ("scenario_id", "expected_path", "expected_payload", "lab_key", "active_field"),
    [
        (
            "go-memory-growth",
            "/faults/memory/start",
            {"megabytes": 96, "duration_seconds": 30},
            "go",
            "memory_fault_active",
        ),
        (
            "java-offheap-growth",
            "/faults/offheap/start",
            {"megabytes": 96, "duration_seconds": 30},
            "java",
            "offheap_fault_active",
        ),
        (
            "cpp-downstream-latency",
            "/faults/downstream/start",
            {"delay_ms": 260, "duration_seconds": 30},
            "cpp",
            "downstream_fault_active",
        ),
    ],
)
def test_new_faults_keep_server_owned_parameters_and_runtime_route(
    monkeypatch,
    scenario_id,
    expected_path,
    expected_payload,
    lab_key,
    active_field,
) -> None:
    calls = []

    def fake_request(method, path, payload=None, *, lab_key="python"):
        calls.append((method, path, payload, lab_key))
        return {active_field: True}

    monkeypatch.setattr(fault_plaza, "_request_json", fake_request)

    result = fault_plaza.start_fault_scenario(scenario_id, 30)

    assert calls == [("POST", expected_path, expected_payload, lab_key)]
    assert result["scenario"]["active"] is True
    assert result["minimum_diagnosis_rounds"] >= 3


def test_unknown_fault_scenario_never_becomes_a_dynamic_url(monkeypatch) -> None:
    monkeypatch.setattr(
        fault_plaza,
        "_request_json",
        lambda *_args, **_kwargs: pytest.fail("request should not be issued"),
    )

    with pytest.raises(ValueError, match="fault scenario not found"):
        fault_plaza.start_fault_scenario("../../admin", 60)


def test_memory_fault_keeps_server_owned_safe_defaults(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        fault_plaza,
        "_request_json",
        lambda method, path, payload=None, **_kwargs: calls.append(
            (method, path, payload)
        )
        or {"memory_fault_active": True},
    )

    result = fault_plaza.start_fault_scenario("memory-pressure", 45)

    assert calls[0][2] == {"megabytes": 96, "duration_seconds": 45}
    assert result["scenario"]["active"] is True


@pytest.mark.parametrize("operation", ["list", "start", "stop", "rpc_list"])
def test_default_fault_controls_never_read_or_return_retired_strict_scores(
    tmp_path, monkeypatch, operation,
) -> None:
    from pathlib import Path

    strict_index = tmp_path / "retired-strict-index.json"
    monkeypatch.setenv("MINI_DROP_FAULT_ACCEPTANCE_INDEX", str(strict_index))
    _disable_all_labs(monkeypatch)
    monkeypatch.setattr(fault_plaza, "_request_json", lambda *_args, **_kwargs: {"fault_active": True})
    actual_stat, actual_read_text = Path.stat, Path.read_text

    def guarded_stat(path, *args, **kwargs):
        if path == strict_index:
            pytest.fail("default fault controls must not access the retired strict index")
        return actual_stat(path, *args, **kwargs)

    def guarded_read_text(path, *args, **kwargs):
        if path == strict_index:
            pytest.fail("default fault controls must not load the retired strict score")
        return actual_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", guarded_stat)
    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    if operation == "list":
        response = fault_plaza.get_fault_plaza()
        scenarios = response["scenarios"]
    elif operation == "rpc_list":
        from server.app.diagnostic_ai_rpc import dispatch

        result = dispatch("GET", "/showcases/fault-plaza", "", "", "test-operator")
        assert result.status == 200
        response = result.body["data"]
        scenarios = response["scenarios"]
    else:
        control = fault_plaza.start_fault_scenario if operation == "start" else fault_plaza.stop_fault_scenario
        response = control("cpu-hotspot", 30) if operation == "start" else control("cpu-hotspot")
        scenarios = [response["scenario"]]
    if operation in {"list", "rpc_list"}:
        assert len(scenarios) == 21
        assert {item["target_runtime"] for item in scenarios} == {"Python", "Go", "Java", "C++"}
    retired_grades = {"latest_acceptance", "acceptance_level", "root_cause_accepted", "passed", "fix_verified"}
    assert not retired_grades.intersection(response)
    assert all(not retired_grades.intersection(item) for item in scenarios)
