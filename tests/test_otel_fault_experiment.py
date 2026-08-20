from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import otel_grpc_fault_experiment as experiment


def test_memory_bytes_parses_docker_units() -> None:
    assert experiment.memory_bytes("12.5MiB / 100MiB") == 12.5 * 1024 * 1024
    assert experiment.memory_bytes("1.5GiB / 2GiB") == 1.5 * 1024**3
    assert experiment.memory_bytes("900kB / 1GB") == 900_000


def test_memory_bytes_rejects_unknown_format() -> None:
    assert experiment.memory_bytes("") == 0
    assert experiment.memory_bytes("unknown") == 0


def test_memory_request_factory_posts_expected_http_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, Any] = {}

    @contextmanager
    def fake_urlopen(request: Any, timeout: float):
        observed["url"] = request.full_url
        observed["method"] = request.get_method()
        observed["headers"] = dict(request.header_items())
        observed["payload"] = json.loads(request.data.decode("utf-8"))
        observed["timeout"] = timeout
        yield SimpleNamespace(status=200, read=lambda: b"")

    monkeypatch.setattr(experiment, "urlopen", fake_urlopen)
    workload = experiment.request_factory("T1-MEM-001", object(), object())
    client = workload.create_client("127.0.0.1:6060")

    assert workload.transport == "http"
    assert workload.invoke(client) == 200
    assert observed == {
        "url": "http://127.0.0.1:6060/send_order_confirmation",
        "method": "POST",
        "headers": {"Content-type": "application/json"},
        "payload": experiment.EMAIL_PAYLOAD,
        "timeout": 5,
    }


def test_payment_request_factory_keeps_grpc_charge_path(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = SimpleNamespace(close=lambda: None)
    observed: dict[str, Any] = {}

    class Stub:
        def __init__(self, received_channel: Any) -> None:
            observed["channel"] = received_channel

        def Charge(self, request: Any, *, timeout: float) -> str:
            observed["request"] = request
            observed["timeout"] = timeout
            return "charged"

    class Message:
        def __init__(self, **values: Any) -> None:
            self.__dict__.update(values)

    pb2 = SimpleNamespace(ChargeRequest=Message, Money=Message, CreditCardInfo=Message)
    pb2_grpc = SimpleNamespace(PaymentServiceStub=Stub)
    monkeypatch.setattr(experiment.grpc, "insecure_channel", lambda _target: channel)

    workload = experiment.request_factory("T1-DOWNSTREAM-001", pb2, pb2_grpc)
    client = workload.create_client("127.0.0.1:50051")

    assert workload.transport == "grpc"
    assert workload.invoke(client) == "charged"
    assert observed["channel"] is channel
    assert observed["timeout"] == 5
    assert observed["request"].amount.units == 42


def test_request_factory_rejects_unsupported_case() -> None:
    with pytest.raises(ValueError, match="unsupported OTel fault case"):
        experiment.request_factory("T1-UNKNOWN-001", object(), object())


def _workload() -> experiment.Workload:
    return experiment.Workload(
        transport="http",
        create_client=lambda target: target,
        invoke=lambda _client: 200,
        close_client=lambda _client: None,
        request_errors=(OSError,),
    )


def _phase(name: str, *, memory_max: int, failure: dict[str, Any] | None = None) -> dict[str, Any]:
    enabled = name == "incident"
    return {
        "phase": name,
        "requests": 1,
        "errors": 0,
        "error_rate": 0,
        "resource_observation": {"memory_bytes_max": memory_max},
        "toggle": {"enabled": enabled},
        "fixture_failure": failure,
    }


def _prepare_memory_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        experiment,
        "compile_proto",
        lambda *args: pytest.fail("memory case must not compile gRPC proto"),
    )
    monkeypatch.setattr(experiment, "request_factory", lambda *args: _workload())
    monkeypatch.setattr(experiment, "docker_host_port", lambda *args: 6060)
    monkeypatch.setattr(experiment, "run_command", lambda *args: "pinned-commit")
    monkeypatch.setattr(
        experiment,
        "set_otel_feature_flag",
        lambda _case, *, otel_root, enabled: {"enabled": enabled},
    )


def test_run_phase_stops_at_memory_abort_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(experiment, "set_otel_feature_flag", lambda *args, **kwargs: {})
    monkeypatch.setattr(experiment, "host_pid", lambda _container: 123)
    monkeypatch.setattr(
        experiment,
        "docker_stats",
        lambda _container: {"cpu_percent": 1.0, "memory_usage": "81MiB / 100MiB"},
    )
    monkeypatch.setattr(experiment.time, "sleep", lambda _seconds: None)

    phase = experiment.run_phase(
        case_id="T1-MEM-001",
        name="incident",
        enabled=True,
        otel_root=Path("otel"),
        target="127.0.0.1:6060",
        container="email",
        duration=3,
        workers=1,
        workload=_workload(),
        expected_pid=123,
        memory_abort_bytes=experiment.MEMORY_ABORT_BYTES,
    )

    assert phase["fixture_failure"]["reason"] == "memory_abort_threshold_reached"
    assert phase["resource_observation"]["memory_bytes_max"] == 81 * 1024 * 1024


def test_run_phase_classifies_unexpected_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(experiment, "set_otel_feature_flag", lambda *args, **kwargs: {})
    monkeypatch.setattr(experiment, "host_pid", lambda _container: 456)
    monkeypatch.setattr(experiment.time, "sleep", lambda _seconds: None)

    phase = experiment.run_phase(
        case_id="T1-MEM-001",
        name="incident",
        enabled=True,
        otel_root=Path("otel"),
        target="127.0.0.1:6060",
        container="email",
        duration=3,
        workers=1,
        workload=_workload(),
        expected_pid=123,
    )

    assert phase["fixture_failure"] == {
        "reason": "unexpected_container_restart",
        "expected_pid": 123,
        "observed_pid": 456,
    }


def test_execute_experiment_restarts_memory_fixture_after_incident_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    toggles: list[bool] = []
    restarts: list[str] = []
    _prepare_memory_execute(monkeypatch)
    monkeypatch.setattr(experiment, "host_pid", lambda _container: 123)
    monkeypatch.setattr(
        experiment,
        "set_otel_feature_flag",
        lambda _case, *, otel_root, enabled: toggles.append(enabled) or {"enabled": enabled},
    )
    monkeypatch.setattr(
        experiment,
        "restart_and_wait_healthy",
        lambda container: restarts.append(container) or {
            "before_pid": 123,
            "after_pid": 456,
            "status": "healthy",
        },
    )
    monkeypatch.setattr(
        experiment,
        "run_phase",
        lambda **kwargs: _phase(
            kwargs["name"],
            memory_max=1,
            failure=(
                {"reason": "memory_abort_threshold_reached"}
                if kwargs["name"] == "incident"
                else None
            ),
        ),
    )
    output = tmp_path / "result.json"

    result = experiment.execute_experiment(
        case_id="T1-MEM-001",
        otel_root=tmp_path,
        duration=3,
        workers=1,
        output=output,
    )

    assert [phase["phase"] for phase in result["phases"]] == ["baseline", "incident"]
    assert result["verification"]["passed"] is False
    assert result["cleanup"]["errors"] == []
    assert toggles[-1] is False
    assert restarts == ["email"]
    assert json.loads(output.read_text(encoding="utf-8"))["fixture_failure"]


def test_execute_experiment_restarts_memory_fixture_for_recovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_memory_execute(monkeypatch)
    monkeypatch.setattr(experiment, "host_pid", lambda _container: 123)
    monkeypatch.setattr(
        experiment,
        "restart_and_wait_healthy",
        lambda _container: {"before_pid": 123, "after_pid": 456, "status": "healthy"},
    )
    maxima = {"baseline": 10, "incident": 12 * 1024 * 1024, "recovery": 5}
    monkeypatch.setattr(
        experiment,
        "run_phase",
        lambda **kwargs: _phase(kwargs["name"], memory_max=maxima[kwargs["name"]]),
    )

    result = experiment.execute_experiment(
        case_id="T1-MEM-001",
        otel_root=tmp_path,
        duration=3,
        workers=1,
        output=tmp_path / "result.json",
    )

    assert result["recovery_intervention"]["after_pid"] == 456
    assert result["verification"]["passed"] is True
    assert result["cleanup"]["memory_restart"] is None


def test_execute_experiment_records_explicit_compose_and_container_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_memory_execute(monkeypatch)
    compose = {"project": "mini-drop-mem", "sha256": "compose-sha"}
    containers = iter((
        {"host_pid": 123, "container_id": "initial"},
        {"host_pid": 456, "container_id": "recovery"},
    ))
    monkeypatch.setattr(experiment, "compose_config_provenance", lambda **kwargs: compose)
    monkeypatch.setattr(experiment, "docker_container_provenance", lambda _container: next(containers))
    monkeypatch.setattr(experiment, "host_pid", lambda _container: 123)
    monkeypatch.setattr(
        experiment,
        "restart_and_wait_healthy",
        lambda _container: {"before_pid": 123, "after_pid": 456, "status": "healthy"},
    )
    maxima = {"baseline": 10, "incident": 12 * 1024 * 1024, "recovery": 5}
    monkeypatch.setattr(
        experiment,
        "run_phase",
        lambda **kwargs: _phase(kwargs["name"], memory_max=maxima[kwargs["name"]]),
    )
    compose_file = tmp_path / "compose.yaml"

    result = experiment.execute_experiment(
        case_id="T1-MEM-001",
        otel_root=tmp_path,
        duration=3,
        workers=1,
        output=tmp_path / "result.json",
        project_name="mini-drop-mem",
        compose_files=[compose_file],
    )

    assert result["provenance"] == {
        "compose": compose,
        "containers": {
            "initial": {"host_pid": 123, "container_id": "initial"},
            "recovery": {"host_pid": 456, "container_id": "recovery"},
            "cleanup": None,
        },
    }
    assert [item["phase"] for item in result["flag_transitions"]] == [
        "baseline",
        "incident",
        "recovery_intervention",
        "recovery",
        "cleanup",
    ]


def test_execute_experiment_records_cleanup_provenance_after_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_memory_execute(monkeypatch)
    monkeypatch.setattr(experiment, "compose_config_provenance", lambda **kwargs: {"sha256": "sha"})
    containers = iter((
        {"host_pid": 123, "container_id": "initial"},
        {"host_pid": 456, "container_id": "cleanup"},
    ))
    monkeypatch.setattr(experiment, "docker_container_provenance", lambda _container: next(containers))
    monkeypatch.setattr(experiment, "host_pid", lambda _container: 123)
    monkeypatch.setattr(
        experiment,
        "restart_and_wait_healthy",
        lambda _container: {"before_pid": 123, "after_pid": 456, "status": "healthy"},
    )
    monkeypatch.setattr(
        experiment,
        "run_phase",
        lambda **kwargs: _phase(
            kwargs["name"],
            memory_max=1,
            failure=({"reason": "memory_abort_threshold_reached"} if kwargs["name"] == "incident" else None),
        ),
    )

    result = experiment.execute_experiment(
        case_id="T1-MEM-001",
        otel_root=tmp_path,
        duration=3,
        workers=1,
        output=tmp_path / "result.json",
        project_name="mini-drop-mem",
        compose_files=[tmp_path / "compose.yaml"],
    )

    assert result["provenance"]["containers"]["cleanup"] == {
        "host_pid": 456,
        "container_id": "cleanup",
    }
    assert result["cleanup"]["errors"] == []
    assert result["fixture_failure"]["reason"] == "experiment_exception"


def test_execute_experiment_rejects_cleanup_provenance_pid_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _prepare_memory_execute(monkeypatch)
    monkeypatch.setattr(experiment, "compose_config_provenance", lambda **kwargs: {"sha256": "sha"})
    containers = iter((
        {"host_pid": 123, "container_id": "initial"},
        {"host_pid": 999, "container_id": "cleanup"},
    ))
    monkeypatch.setattr(experiment, "docker_container_provenance", lambda _container: next(containers))
    monkeypatch.setattr(experiment, "host_pid", lambda _container: 123)
    monkeypatch.setattr(
        experiment,
        "restart_and_wait_healthy",
        lambda _container: {"before_pid": 123, "after_pid": 456, "status": "healthy"},
    )
    monkeypatch.setattr(
        experiment,
        "run_phase",
        lambda **kwargs: _phase(
            kwargs["name"],
            memory_max=1,
            failure=({"reason": "memory_abort_threshold_reached"} if kwargs["name"] == "incident" else None),
        ),
    )

    result = experiment.execute_experiment(
        case_id="T1-MEM-001",
        otel_root=tmp_path,
        duration=3,
        workers=1,
        output=tmp_path / "result.json",
        project_name="mini-drop-mem",
        compose_files=[tmp_path / "compose.yaml"],
    )

    assert result["fixture_failure"]["reason"] == "cleanup_failed"
    assert "cleanup provenance PID does not match" in result["cleanup"]["errors"][0]
    assert result["provenance"]["containers"]["cleanup"]["host_pid"] == 999


def test_execute_experiment_does_not_require_docker_provenance_without_compose_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        experiment,
        "run_command",
        lambda *args: (_ for _ in ()).throw(RuntimeError("git unavailable")),
    )
    monkeypatch.setattr(
        experiment,
        "docker_container_provenance",
        lambda _container: pytest.fail("legacy invocation must not inspect provenance"),
    )
    monkeypatch.setattr(
        experiment,
        "set_otel_feature_flag",
        lambda *args, **kwargs: {"enabled": False},
    )

    result = experiment.execute_experiment(
        case_id="T1-MEM-001",
        otel_root=tmp_path,
        duration=3,
        workers=1,
        output=tmp_path / "result.json",
    )

    assert result["fixture_failure"]["reason"] == "experiment_exception"
    assert result["provenance"]["containers"]["cleanup"] is None
