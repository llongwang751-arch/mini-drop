from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from server.app.agent_runtime.pi_adapter import (
    PiAcceptanceUnknown,
    PiAgentRuntimeAdapter,
    PiDefinitiveRejection,
)
from server.app.agent_runtime.port import AgentTurnInput, CaseContextSnapshot


ROOT = Path(__file__).resolve().parents[1]
SIDECAR_DIR = ROOT / "agent_runtime" / "pi-sidecar"
SIDECAR_ENTRY = SIDECAR_DIR / "src" / "server.mjs"


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_health(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail(f"Pi Sidecar exited before health check (code {process.returncode})")
        try:
            with urllib.request.urlopen(
                f"{base_url}/internal/runtime/v1/health", timeout=0.5
            ) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.05)
    pytest.fail("Pi Sidecar did not become healthy within 15 seconds")


def _start_sidecar(
    node: str,
    *,
    port: int,
    token: str,
    state_path: Path,
    internal_base: str = "http://127.0.0.1:1",
) -> tuple[subprocess.Popen[str], str]:
    base_url = f"http://127.0.0.1:{port}"
    environment = os.environ.copy()
    for name in (
        "DEEPSEEK_API_KEY",
        "MINI_DROP_AI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
    ):
        environment.pop(name, None)
    environment.update({
        "MINI_DROP_PI_SIDECAR_PORT": str(port),
        "MINI_DROP_PI_INTERNAL_TOKEN": token,
        "MINI_DROP_PI_INTERNAL_BASE": internal_base,
        "MINI_DROP_PI_STATE_PATH": str(state_path),
    })
    process = subprocess.Popen(
        [node, str(SIDECAR_ENTRY)],
        cwd=SIDECAR_DIR,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    _wait_for_health(base_url, process)
    return process, base_url


def _stop_sidecar(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _wait_until(predicate, message: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail(message)


class _CallbackHandler(BaseHTTPRequestHandler):
    server: "_CallbackServer"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length)
        with self.server.requests_lock:
            self.server.requests.append({
                "path": self.path,
                "raw_body": raw_body,
                "body": json.loads(raw_body),
                "token": self.headers.get("X-Internal-Token"),
            })
        status = 200 if self.server.acknowledge.is_set() else 503
        body = json.dumps({"ok": status == 200}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


class _CallbackServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _CallbackHandler)
        self.acknowledge = threading.Event()
        self.requests: list[dict] = []
        self.requests_lock = threading.Lock()
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=5)

    def snapshot(self) -> list[dict]:
        with self.requests_lock:
            return list(self.requests)


@pytest.fixture()
def actual_pi_sidecar(monkeypatch, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    if not SIDECAR_ENTRY.exists() or not (SIDECAR_DIR / "node_modules").exists():
        pytest.skip("Pi Sidecar dependencies are not installed")

    token = "process-contract-token"
    process, base_url = _start_sidecar(
        node,
        port=_available_port(),
        token=token,
        state_path=tmp_path / "sidecar-state.sqlite3",
    )
    try:
        monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi_shadow")
        monkeypatch.setenv("MINI_DROP_PI_RUNTIME_URL", base_url)
        monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", token)
        yield base_url
    finally:
        _stop_sidecar(process)


def _callback_outbox_rows(state_path: Path) -> list[tuple]:
    try:
        with sqlite3.connect(state_path, timeout=0.2) as database:
            return database.execute(
                "SELECT callback_id, callback_kind, body_json, delivery_status, "
                "attempt_count FROM callback_outbox ORDER BY ordinal"
            ).fetchall()
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        return []


def _snapshot(generation: int = 3) -> CaseContextSnapshot:
    return CaseContextSnapshot(
        diagnosis_id="diag-process",
        case_id="RW-PI-PROCESS-1",
        runtime_generation=generation,
        case_goal="validate the process transport contract",
        context_snapshot_id=f"snapshot-{generation}",
    )


def _turn(
    *,
    turn_id: str = "turn-process-a",
    command_id: str = "command-process-a",
    generation: int = 3,
    message: str = "accept this shadow turn without model execution",
) -> AgentTurnInput:
    return AgentTurnInput(
        diagnosis_id="diag-process",
        turn_id=turn_id,
        runtime_generation=generation,
        message=message,
        references=[{"evidence_id": "evidence-process"}],
        requested_mode="COLLABORATE",
        client_command_id=command_id,
    )


def test_accepted_command_reconciliation_survives_sidecar_restart(
    monkeypatch, tmp_path
):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    if not SIDECAR_ENTRY.exists() or not (SIDECAR_DIR / "node_modules").exists():
        pytest.skip("Pi Sidecar dependencies are not installed")

    token = "restart-contract-token"
    state_path = tmp_path / "restart-state.sqlite3"
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi_shadow")
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", token)

    first_process, first_url = _start_sidecar(
        node,
        port=_available_port(),
        token=token,
        state_path=state_path,
    )
    monkeypatch.setenv("MINI_DROP_PI_RUNTIME_URL", first_url)
    first_adapter = PiAgentRuntimeAdapter(first_url, timeout=2)
    first_adapter.start_or_resume(_snapshot())
    accepted = first_adapter.submit_turn(_turn())
    _stop_sidecar(first_process)

    second_process, second_url = _start_sidecar(
        node,
        port=_available_port(),
        token=token,
        state_path=state_path,
    )
    try:
        monkeypatch.setenv("MINI_DROP_PI_RUNTIME_URL", second_url)
        second_adapter = PiAgentRuntimeAdapter(second_url, timeout=2)
        second_adapter.start_or_resume(_snapshot())
        assert second_adapter.get_accepted_turn(
            "diag-process", "command-process-a"
        ) == accepted
        with pytest.raises(
            PiAcceptanceUnknown,
            match="sidecar replaced the authoritative turn identity",
        ):
            second_adapter.submit_turn(_turn(turn_id="retry-local-turn"))
        assert second_adapter.get_accepted_turn(
            "diag-process", "command-process-a"
        ) == accepted
        with pytest.raises(PiDefinitiveRejection, match="different request"):
            second_adapter.submit_turn(_turn(
                turn_id="changed-local-turn",
                message="changed immutable message",
            ))
        assert second_adapter.start_or_resume(_snapshot(4)).runtime_generation == 4
        assert second_adapter.get_accepted_turn(
            "diag-process", "command-process-a"
        ) is None
    finally:
        _stop_sidecar(second_process)

    third_process, third_url = _start_sidecar(
        node,
        port=_available_port(),
        token=token,
        state_path=state_path,
    )
    try:
        monkeypatch.setenv("MINI_DROP_PI_RUNTIME_URL", third_url)
        third_adapter = PiAgentRuntimeAdapter(third_url, timeout=2)
        assert third_adapter.start_or_resume(_snapshot(4)).runtime_generation == 4
        assert third_adapter.get_accepted_turn(
            "diag-process", "command-process-a"
        ) is None
        with pytest.raises(PiDefinitiveRejection, match="stale runtime generation"):
            third_adapter.start_or_resume(_snapshot())
    finally:
        _stop_sidecar(third_process)


def test_pending_callback_delivery_survives_sidecar_restart(
    monkeypatch, tmp_path
):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    if not SIDECAR_ENTRY.exists() or not (SIDECAR_DIR / "node_modules").exists():
        pytest.skip("Pi Sidecar dependencies are not installed")

    token = "callback-restart-token"
    state_path = tmp_path / "callback-restart-state.sqlite3"
    callback_server = _CallbackServer()
    callback_server.start()
    monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi")
    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", token)

    first_process, first_url = _start_sidecar(
        node,
        port=_available_port(),
        token=token,
        state_path=state_path,
        internal_base=callback_server.base_url,
    )
    try:
        monkeypatch.setenv("MINI_DROP_PI_RUNTIME_URL", first_url)
        first_adapter = PiAgentRuntimeAdapter(first_url, timeout=2)
        first_adapter.start_or_resume(_snapshot())
        accepted = first_adapter.submit_turn(_turn())
        assert accepted.turn_id == "turn-process-a"
        _wait_until(
            lambda: any(
                row[1] == "TERMINAL" and row[3] == "PENDING"
                for row in _callback_outbox_rows(state_path)
            ),
            "Sidecar did not persist pending terminal callback work",
        )
        rows_before_restart = _callback_outbox_rows(state_path)
        assert len(rows_before_restart) == 1
        assert rows_before_restart[0][1] == "TERMINAL"
        callback_id = rows_before_restart[0][0]
        callback_body = rows_before_restart[0][2]
        _wait_until(
            lambda: len(callback_server.snapshot()) >= 1,
            "Sidecar did not attempt callback delivery before restart",
        )
    finally:
        _stop_sidecar(first_process)

    attempts_before_restart = callback_server.snapshot()
    callback_server.acknowledge.set()
    second_process, second_url = _start_sidecar(
        node,
        port=_available_port(),
        token=token,
        state_path=state_path,
        internal_base=callback_server.base_url,
    )
    try:
        monkeypatch.setenv("MINI_DROP_PI_RUNTIME_URL", second_url)
        second_adapter = PiAgentRuntimeAdapter(second_url, timeout=2)
        binding = second_adapter.start_or_resume(_snapshot())
        assert binding.runtime_generation == 3
        _wait_until(
            lambda: any(
                row[0] == callback_id and row[3] == "DELIVERED"
                for row in _callback_outbox_rows(state_path)
            ),
            "Sidecar did not deliver persisted callback after restart",
        )
        requests = callback_server.snapshot()
        assert len(requests) > len(attempts_before_restart)
        assert {request["raw_body"] for request in requests} == {
            callback_body.encode("utf-8")
        }
        assert {request["token"] for request in requests} == {token}
        assert all(request["path"].endswith("/terminal") for request in requests)
        assert second_adapter.get_state("diag-process").active_turn_id is None
        with sqlite3.connect(state_path) as database:
            turn = database.execute(
                "SELECT lifecycle_status, terminal_status, final_message_json "
                "FROM runtime_turn_state WHERE diagnosis_id = ? AND turn_id = ?",
                ("diag-process", "turn-process-a"),
            ).fetchone()
        assert turn[0:2] == ("DELIVERED", "FAILED")
        assert json.loads(turn[2]) == json.loads(callback_body)["final_message"]
        rows_after_restart = _callback_outbox_rows(state_path)
        assert len(rows_after_restart) == 1
        assert rows_after_restart[0][0] == callback_id
        assert rows_after_restart[0][2] == callback_body
        assert rows_after_restart[0][3] == "DELIVERED"
        recovered = second_adapter.get_accepted_turn(
            "diag-process", "command-process-a"
        )
        assert recovered == accepted
    finally:
        _stop_sidecar(second_process)
        callback_server.close()


def test_python_adapter_contract_against_actual_node_sidecar(
    actual_pi_sidecar, monkeypatch
):
    adapter = PiAgentRuntimeAdapter(actual_pi_sidecar, timeout=2)

    binding = adapter.start_or_resume(_snapshot())
    accepted = adapter.submit_turn(_turn())
    recovered = adapter.get_accepted_turn("diag-process", "command-process-a")
    state = adapter.get_state("diag-process")

    assert binding.diagnosis_id == "diag-process"
    assert binding.runtime_generation == 3
    assert accepted.turn_id == "turn-process-a"
    assert accepted.mode == "pi_shadow"
    assert recovered == accepted
    assert state.status == "READY"
    assert state.active_turn_id is None
    assert state.last_error is None

    adapter.seal_turn("diag-process", "turn-process-a")
    adapter.submit_turn(_turn(
        turn_id="turn-process-b",
        command_id="command-process-b",
    ))
    adapter.cancel_turn("diag-process", "turn-process-b", "contract cleanup")

    with pytest.raises(PiDefinitiveRejection, match="stale runtime generation"):
        adapter.submit_turn(_turn(
            turn_id="turn-stale",
            command_id="command-stale",
            generation=2,
        ))

    rotated = adapter.start_or_resume(_snapshot(4))
    assert rotated.runtime_generation == 4
    assert adapter.get_accepted_turn("diag-process", "command-process-a") is None

    monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", "wrong-process-token")
    with pytest.raises(PiDefinitiveRejection, match="HTTP 401"):
        adapter.get_state("diag-process")
