from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from scripts import otel_ad_cpu_experiment as experiment


def _phase(
    name: str,
    *,
    cpu_mean: float,
    failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    enabled = name == "incident"
    return {
        "phase": name,
        "requests": 10,
        "errors": 0,
        "request_rate_per_second": 10.0,
        "latency_ms_p95": 2.0,
        "resource_observation": {"cpu_percent_mean": cpu_mean},
        "toggle": {"enabled": enabled},
        "fixture_failure": failure,
    }


def _prepare_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(experiment, "run_command", lambda *args: "pinned-commit")
    monkeypatch.setattr(experiment, "host_pid", lambda _container: 123)
    monkeypatch.setattr(experiment, "docker_host_port", lambda *args: 9555)
    monkeypatch.setattr(experiment, "compile_proto", lambda *args: (object(), object()))
    monkeypatch.setattr(
        experiment,
        "set_otel_feature_flag",
        lambda _case, *, otel_root, enabled: {"enabled": enabled},
    )


def test_run_phase_classifies_unexpected_container_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stub:
        def __init__(self, _channel: Any) -> None:
            pass

        def GetAds(self, _request: Any, *, timeout: float) -> None:
            assert timeout == 5

    class Request:
        def __init__(self, **values: Any) -> None:
            self.__dict__.update(values)

    channel = type("Channel", (), {"close": lambda self: None})()
    monkeypatch.setattr(experiment.grpc, "insecure_channel", lambda _target: channel)
    monkeypatch.setattr(experiment, "set_otel_feature_flag", lambda *args, **kwargs: {})
    monkeypatch.setattr(experiment, "host_pid", lambda _container: 456)
    monkeypatch.setattr(experiment.time, "sleep", lambda _seconds: None)

    phase = experiment.run_phase(
        case_id="T1-CPU-001",
        name="incident",
        enabled=True,
        otel_root=Path("otel"),
        target="127.0.0.1:9555",
        container="ad",
        duration=3,
        workers=1,
        demo_pb2=type("Pb2", (), {"AdRequest": Request}),
        demo_pb2_grpc=type("Grpc", (), {"AdServiceStub": Stub}),
        expected_pid=123,
    )

    assert phase["fixture_failure"] == {
        "reason": "unexpected_container_restart",
        "expected_pid": 123,
        "observed_pid": 456,
    }


def test_run_phase_classifies_resource_sampling_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stub:
        def __init__(self, _channel: Any) -> None:
            pass

        def GetAds(self, _request: Any, *, timeout: float) -> None:
            assert timeout == 5

    class Request:
        def __init__(self, **values: Any) -> None:
            self.__dict__.update(values)

    channel = type("Channel", (), {"close": lambda self: None})()
    monkeypatch.setattr(experiment.grpc, "insecure_channel", lambda _target: channel)
    monkeypatch.setattr(experiment, "set_otel_feature_flag", lambda *args, **kwargs: {})
    monkeypatch.setattr(experiment, "host_pid", lambda _container: 123)
    monkeypatch.setattr(
        experiment,
        "docker_stats",
        lambda _container: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, ["docker", "stats"])
        ),
    )
    monkeypatch.setattr(experiment.time, "sleep", lambda _seconds: None)

    phase = experiment.run_phase(
        case_id="T1-CPU-001",
        name="incident",
        enabled=True,
        otel_root=Path("otel"),
        target="127.0.0.1:9555",
        container="ad",
        duration=3,
        workers=1,
        demo_pb2=type("Pb2", (), {"AdRequest": Request}),
        demo_pb2_grpc=type("Grpc", (), {"AdServiceStub": Stub}),
        expected_pid=123,
    )

    assert phase["fixture_failure"]["reason"] == "resource_sampling_failed"
    assert "CalledProcessError" in phase["fixture_failure"]["error"]


def test_execute_experiment_records_three_phase_cpu_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_execute(monkeypatch)
    cpu = {"baseline": 2.0, "incident": 8.0, "recovery": 3.0}
    monkeypatch.setattr(
        experiment,
        "run_phase",
        lambda **kwargs: _phase(kwargs["name"], cpu_mean=cpu[kwargs["name"]]),
    )
    output = tmp_path / "cpu.json"

    result = experiment.execute_experiment(
        case_id="T1-CPU-001",
        otel_root=tmp_path,
        duration=3,
        workers=1,
        output=output,
    )

    assert [item["phase"] for item in result["phases"]] == [
        "baseline",
        "incident",
        "recovery",
    ]
    assert result["verification"] == {
        "incident_cpu_increase_ratio": 4.0,
        "incident_latency_p95_ratio": 1.0,
        "recovery_cpu_below_incident": True,
        "real_requests_observed": True,
        "passed": True,
    }
    assert result["cleanup"]["errors"] == []
    assert json.loads(output.read_text(encoding="utf-8"))["verification"]["passed"] is True


def test_execute_experiment_persists_partial_manifest_after_phase_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_execute(monkeypatch)
    toggles: list[bool] = []
    monkeypatch.setattr(
        experiment,
        "set_otel_feature_flag",
        lambda _case, *, otel_root, enabled: toggles.append(enabled) or {"enabled": enabled},
    )
    monkeypatch.setattr(
        experiment,
        "run_phase",
        lambda **kwargs: _phase(
            kwargs["name"],
            cpu_mean=2.0,
            failure=(
                {"reason": "unexpected_container_restart"}
                if kwargs["name"] == "incident"
                else None
            ),
        ),
    )
    output = tmp_path / "partial.json"

    result = experiment.execute_experiment(
        case_id="T1-CPU-001",
        otel_root=tmp_path,
        duration=3,
        workers=1,
        output=output,
    )

    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert [item["phase"] for item in persisted["phases"]] == ["baseline", "incident"]
    assert persisted["fixture_failure"]["reason"] == "experiment_exception"
    assert persisted["verification"]["passed"] is False
    assert result["cleanup"]["flag_reset"] == {"enabled": False}
    assert toggles[-1] is False
    assert not (tmp_path / "partial.json.tmp").exists()


def test_execute_experiment_records_explicit_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_execute(monkeypatch)
    compose = {"project": "mini-drop-cpu", "sha256": "compose-sha"}
    container = {"host_pid": 123, "container_id": "ad-container"}
    monkeypatch.setattr(experiment, "compose_config_provenance", lambda **kwargs: compose)
    monkeypatch.setattr(experiment, "docker_container_provenance", lambda _name: container)
    cpu = {"baseline": 2.0, "incident": 8.0, "recovery": 3.0}
    monkeypatch.setattr(
        experiment,
        "run_phase",
        lambda **kwargs: _phase(kwargs["name"], cpu_mean=cpu[kwargs["name"]]),
    )

    result = experiment.execute_experiment(
        case_id="T1-CPU-001",
        otel_root=tmp_path,
        duration=3,
        workers=1,
        output=tmp_path / "cpu.json",
        project_name="mini-drop-cpu",
        compose_files=[tmp_path / "compose.yaml"],
    )

    assert result["provenance"] == {
        "compose": compose,
        "containers": {"initial": container, "recovery": None, "cleanup": None},
    }


def test_execute_experiment_rejects_initial_provenance_pid_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_execute(monkeypatch)
    monkeypatch.setattr(experiment, "compose_config_provenance", lambda **kwargs: {})
    monkeypatch.setattr(
        experiment,
        "docker_container_provenance",
        lambda _name: {"host_pid": 999, "container_id": "wrong"},
    )
    monkeypatch.setattr(
        experiment,
        "run_phase",
        lambda **kwargs: pytest.fail("PID mismatch must fail before workload execution"),
    )

    result = experiment.execute_experiment(
        case_id="T1-CPU-001",
        otel_root=tmp_path,
        duration=3,
        workers=1,
        output=tmp_path / "cpu.json",
        project_name="mini-drop-cpu",
        compose_files=[tmp_path / "compose.yaml"],
    )

    assert result["fixture_failure"]["reason"] == "experiment_exception"
    assert "provenance PID does not match" in result["fixture_failure"]["error"]
    assert result["provenance"]["containers"]["initial"]["host_pid"] == 999


def test_execute_experiment_rejects_duration_above_fault_bound(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="between 3 and 120"):
        experiment.execute_experiment(
            case_id="T1-CPU-001",
            otel_root=tmp_path,
            duration=121,
            workers=1,
            output=tmp_path / "cpu.json",
        )
