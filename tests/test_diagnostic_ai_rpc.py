import json

import grpc
import pytest
from pydantic import ValidationError

from server.app.diagnostic_ai_rpc import DiagnosticAIService, dispatch
from server.app.generated import diagnostic_ai_pb2
from server.app.logging_utils import current_trace_id


class AbortedRPC(RuntimeError):
    pass


class FakeContext:
    def __init__(self, metadata=()):
        self._metadata = metadata
        self.code = None

    def invocation_metadata(self):
        return self._metadata

    def abort(self, code, details):
        self.code = code
        raise AbortedRPC(details)


def test_private_diagnostic_rpc_rejects_invalid_token(monkeypatch) -> None:
    monkeypatch.setenv("MINI_DROP_GRPC_AUTH_ENABLED", "1")
    monkeypatch.setenv("MINI_DROP_GRPC_TOKEN", "expected-token")
    context = FakeContext((("x-mini-drop-grpc-token", "wrong-token"),))

    with pytest.raises(AbortedRPC, match="invalid internal gRPC token"):
        DiagnosticAIService().Invoke(diagnostic_ai_pb2.DiagnosticAIRequest(), context)

    assert context.code == grpc.StatusCode.UNAUTHENTICATED


def test_private_diagnostic_rpc_binds_and_resets_trace_context(monkeypatch) -> None:
    observed = []

    def fake_dispatch(*_args):
        observed.append(current_trace_id())
        from server.app.diagnostic_ai_rpc import _ok

        return _ok({})

    monkeypatch.setenv("MINI_DROP_GRPC_AUTH_ENABLED", "0")
    monkeypatch.setattr("server.app.diagnostic_ai_rpc.dispatch", fake_dispatch)
    context = FakeContext(
        (("traceparent", "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"),)
    )

    response = DiagnosticAIService().Invoke(
        diagnostic_ai_pb2.DiagnosticAIRequest(method="GET", path="/diagnostic-tools"),
        context,
    )

    assert response.status_code == 200
    assert observed == ["4bf92f3577b34da6a3ce929d0e0e4736"]
    assert current_trace_id() == ""


def test_agent_runtime_status_internal_route_is_secret_free(monkeypatch) -> None:
    status = {
        "framework": "langchain-create-agent/langgraph",
        "version": "diagnosis-agent-v3",
        "requested_backend": "postgres",
        "actual_backend": "memory",
        "status": "DEGRADED",
        "healthy": False,
        "degraded": True,
        "fallback_reason": "PostgreSQL checkpoint initialization failed",
        "checkpoint_setup_status": "FAILED",
        "checkpoint_schema_ready": False,
        "persistence_guarantee": "process-local only",
        "survives_process_restart": False,
    }
    monkeypatch.setattr(
        "server.app.diagnostic_ai_rpc.get_agent_runtime_status",
        lambda: status,
    )

    result = dispatch("GET", "/agent-runtime/status", "", "", "tester")

    assert result.status == 200
    assert result.body["data"] == status
    assert "password" not in str(result.body).lower()


def test_frozen_lats_replay_catalog_route_exposes_truth_boundary() -> None:
    result = dispatch("GET", "/showcases/lats-replays", "", "", "tester")

    assert result.status == 200
    scenario = result.body["data"]["scenarios"][0]
    assert scenario["selection_policy"] == "UCT"
    assert scenario["truth_boundary"]["live_collection"] is False
    assert scenario["truth_boundary"]["creates_evidence_rows"] is False


def test_frozen_lats_replay_start_route_accepts_only_client_run_id(monkeypatch) -> None:
    observed = []

    def fake_start(scenario_id, client_run_id, *, principal):
        observed.append((scenario_id, client_run_id, principal))
        return {
            "diagnosis_id": "insight_replay_123",
            "created": True,
            "execution_mode": "FULL_LATS",
            "snapshot": {"snapshot_id": "snapshot-1"},
        }

    monkeypatch.setattr(
        "server.app.diagnostic_ai_rpc.start_frozen_replay_run", fake_start
    )
    result = dispatch(
        "POST",
        "/showcases/lats-replays/python-hotspot-tree-v1/runs",
        "",
        json.dumps({"client_run_id": "browser-run-0001"}),
        "interviewer",
    )

    assert result.status == 201
    assert result.body["data"]["execution_mode"] == "FULL_LATS"
    assert observed == [
        ("python-hotspot-tree-v1", "browser-run-0001", "interviewer")
    ]

    with pytest.raises(ValidationError):
        dispatch(
            "POST",
            "/showcases/lats-replays/python-hotspot-tree-v1/runs",
            "",
            json.dumps(
                {
                    "client_run_id": "browser-run-0002",
                    "scenario_url": "https://untrusted.invalid",
                }
            ),
            "interviewer",
        )


def test_unknown_frozen_lats_replay_id_is_not_a_dynamic_fixture_path() -> None:
    result = dispatch(
        "POST",
        "/showcases/lats-replays/not-allow-listed/runs",
        "",
        json.dumps({"client_run_id": "browser-run-0003"}),
        "interviewer",
    )
    assert result.status == 404


def test_experiment_and_operator_memory_routes_preserve_server_authority(monkeypatch) -> None:
    observed = []

    class Stored:
        @staticmethod
        def to_dict():
            return {"experiment_id": "exp-1", "status": "RUNNING"}

    monkeypatch.setattr(
        "server.app.diagnostic_ai_rpc.create_experiment",
        lambda request, *, created_by: (
            observed.append((request.name, created_by)) or Stored()
        ),
    )
    monkeypatch.setattr(
        "server.app.diagnostic_ai_rpc.put_operator_preference",
        lambda principal, **kwargs: (
            observed.append((principal, kwargs["memory_key"], kwargs["value"]))
            or type(
                "Preference",
                (),
                {"to_dict": staticmethod(lambda: {
                    "principal_id": principal,
                    "memory_key": kwargs["memory_key"],
                    "value": kwargs["value"],
                    "source": "EXPLICIT_USER",
                })},
            )()
        ),
    )

    created = dispatch(
        "POST",
        "/diagnostic-experiments",
        "",
        json.dumps({"name": "Skill randomized experiment"}),
        "operator-a",
    )
    preference = dispatch(
        "PUT",
        "/operator-memories",
        "",
        json.dumps({
            "project_scope": "*",
            "memory_key": "response_language",
            "value": "zh-CN",
        }),
        "operator-a",
    )

    assert created.status == 201
    assert created.body["data"]["experiment_id"] == "exp-1"
    assert preference.body["data"]["source"] == "EXPLICIT_USER"
    assert observed == [
        ("Skill randomized experiment", "operator-a"),
        ("operator-a", "response_language", "zh-CN"),
    ]
