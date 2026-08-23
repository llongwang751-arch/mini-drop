from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from server.app.agent_runtime.port import AgentTurnInput, CaseContextSnapshot


class MockSidecarHandler(BaseHTTPRequestHandler):
    received: list[tuple[str, str, dict, str | None]] = []
    turn_status = 200
    turn_response: dict = {}

    def log_message(self, *args):
        pass

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def _respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self._body()
        self.__class__.received.append((
            self.path,
            "POST",
            body,
            self.headers.get("X-Internal-Token"),
        ))
        if self.path.endswith("/resume"):
            context = body["context"]
            self._respond(200, {"ok": True, "data": {
                "diagnosis_id": context["diagnosis_id"],
                "runtime_session_id": "pi-session-1",
                "runtime_generation": context["runtime_generation"],
                "runtime_type": "pi",
                "runtime_version": "pi-0.83.0",
            }})
        elif self.path.endswith("/turn"):
            response = self.__class__.turn_response or {"ok": True, "data": {
                "turn_id": body["turn"]["turn_id"],
                "accepted": True,
                "mode": "pi",
                "detail": "accepted",
            }}
            self._respond(self.__class__.turn_status, response)
        elif self.path.endswith("/seal") or self.path.endswith("/cancel"):
            self._respond(200, {"ok": True, "data": {"sealed": True}})
        else:
            self._respond(404, {"ok": False, "error": "not_found"})

    def do_GET(self):
        self.__class__.received.append((
            self.path,
            "GET",
            {},
            self.headers.get("X-Internal-Token"),
        ))
        if "/turns/accepted/" in self.path:
            if self.path.endswith("/missing"):
                self._respond(404, {"ok": False, "error": "not_found"})
            else:
                self._respond(200, {"ok": True, "data": {
                    "turn_id": "turn-authoritative",
                    "accepted": True,
                    "mode": "pi",
                    "detail": "recovered",
                }})
        elif self.path.endswith("/state"):
            self._respond(200, {"ok": True, "data": {
                "diagnosis_id": "diag-runtime",
                "runtime_session_id": "pi-session-1",
                "runtime_generation": 3,
                "status": "READY",
            }})
        else:
            self._respond(404, {"ok": False, "error": "not_found"})


@pytest.fixture()
def sidecar(monkeypatch):
    MockSidecarHandler.received = []
    MockSidecarHandler.turn_status = 200
    MockSidecarHandler.turn_response = {}
    server = HTTPServer(("127.0.0.1", 0), MockSidecarHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("MINI_DROP_PI_RUNTIME_URL", url)
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", "test-token")
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi")
    yield url
    server.shutdown()
    thread.join(timeout=2)


def _snapshot() -> CaseContextSnapshot:
    return CaseContextSnapshot(
        diagnosis_id="diag-runtime",
        case_id="RW-RUNTIME-1",
        runtime_generation=3,
        case_goal="inspect current evidence",
        context_snapshot_id="snapshot-3",
    )


def _turn() -> AgentTurnInput:
    return AgentTurnInput(
        diagnosis_id="diag-runtime",
        turn_id="turn-authoritative",
        runtime_generation=3,
        message="continue investigation",
        references=[{"evidence_id": "evidence-1"}],
        requested_mode="COLLABORATE",
        client_command_id="command-1",
    )


def test_adapter_uses_diagnosis_protocol_and_preserves_turn_identity(sidecar):
    from server.app.agent_runtime.pi_adapter import PiAgentRuntimeAdapter

    adapter = PiAgentRuntimeAdapter(sidecar)
    binding = adapter.start_or_resume(_snapshot())
    accepted = adapter.submit_turn(_turn())

    assert binding.diagnosis_id == "diag-runtime"
    assert binding.runtime_generation == 3
    assert accepted.turn_id == "turn-authoritative"
    turn_request = next(item for item in MockSidecarHandler.received if item[0].endswith("/turn"))
    assert turn_request[0] == "/internal/runtime/v1/diagnoses/diag-runtime/turn"
    assert turn_request[2]["turn"]["turn_id"] == "turn-authoritative"
    assert turn_request[2]["turn"]["runtime_generation"] == 3
    assert turn_request[3] == "test-token"


def test_adapter_rejects_sidecar_turn_identity_substitution(sidecar):
    from server.app.agent_runtime.pi_adapter import PiAgentRuntimeAdapter, PiProtocolError

    MockSidecarHandler.turn_response = {"ok": True, "data": {
        "turn_id": "sidecar-invented",
        "accepted": True,
        "mode": "pi",
        "detail": "wrong identity",
    }}

    with pytest.raises(PiProtocolError, match="turn identity"):
        PiAgentRuntimeAdapter(sidecar).submit_turn(_turn())


def test_adapter_recovers_accepted_command_and_supports_turn_fencing(sidecar):
    from server.app.agent_runtime.pi_adapter import PiAgentRuntimeAdapter

    adapter = PiAgentRuntimeAdapter(sidecar)
    recovered = adapter.get_accepted_turn("diag-runtime", "command-1")
    missing = adapter.get_accepted_turn("diag-runtime", "missing")
    state = adapter.get_state("diag-runtime")
    adapter.seal_turn("diag-runtime", "turn-authoritative")
    adapter.cancel_turn("diag-runtime", "turn-other", "user stopped")

    assert recovered is not None
    assert recovered.turn_id == "turn-authoritative"
    assert missing is None
    assert state.runtime_generation == 3
    paths = [item[0] for item in MockSidecarHandler.received]
    assert "/internal/runtime/v1/diagnoses/diag-runtime/turns/turn-authoritative/seal" in paths
    assert "/internal/runtime/v1/diagnoses/diag-runtime/turns/turn-other/cancel" in paths


def test_submit_distinguishes_definitive_rejection(sidecar):
    from server.app.agent_runtime.pi_adapter import (
        PiAgentRuntimeAdapter,
        PiDefinitiveRejection,
    )

    MockSidecarHandler.turn_status = 409
    MockSidecarHandler.turn_response = {"ok": False, "error": "stale generation"}

    with pytest.raises(PiDefinitiveRejection, match="HTTP 409"):
        PiAgentRuntimeAdapter(sidecar).submit_turn(_turn())


def test_submit_marks_transport_failure_as_acceptance_unknown(monkeypatch):
    from server.app.agent_runtime.pi_adapter import (
        PiAcceptanceUnknown,
        PiAgentRuntimeAdapter,
    )

    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi")

    with pytest.raises(PiAcceptanceUnknown, match="acceptance unknown"):
        PiAgentRuntimeAdapter(f"http://127.0.0.1:{port}", timeout=0.1).submit_turn(_turn())


def test_adapter_requires_url_and_dispatcher_fails_closed(monkeypatch):
    from server.app.agent_runtime.dispatcher import get_runtime, reset_runtime
    from server.app.agent_runtime.pi_adapter import PiAgentRuntimeAdapter

    monkeypatch.setenv("MINI_DROP_PI_RUNTIME_URL", "")
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi")
    reset_runtime()
    with pytest.raises(RuntimeError, match="MINI_DROP_PI_RUNTIME_URL"):
        PiAgentRuntimeAdapter()
    with pytest.raises(RuntimeError, match="MINI_DROP_PI_RUNTIME_URL"):
        get_runtime()
    reset_runtime()


def test_dispatcher_defaults_to_deterministic_and_rejects_invalid_mode(monkeypatch):
    from server.app.agent_runtime.deterministic import DeterministicAgentRuntime
    from server.app.agent_runtime.dispatcher import get_runtime, reset_runtime

    monkeypatch.delenv("MINI_DROP_AGENT_RUNTIME", raising=False)
    reset_runtime()
    assert isinstance(get_runtime(), DeterministicAgentRuntime)

    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "typo")
    reset_runtime()
    with pytest.raises(RuntimeError, match="invalid MINI_DROP_AGENT_RUNTIME"):
        get_runtime()
    reset_runtime()
