from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from server.app.agent_runtime.pi_adapter import (
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


@pytest.fixture()
def actual_pi_sidecar(monkeypatch):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    if not SIDECAR_ENTRY.exists() or not (SIDECAR_DIR / "node_modules").exists():
        pytest.skip("Pi Sidecar dependencies are not installed")

    port = _available_port()
    token = "process-contract-token"
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
        "MINI_DROP_PI_INTERNAL_BASE": "http://127.0.0.1:1",
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
    try:
        _wait_for_health(base_url, process)
        monkeypatch.setenv("MINI_DROP_AGENT_RUNTIME", "pi_shadow")
        monkeypatch.setenv("MINI_DROP_PI_RUNTIME_URL", base_url)
        monkeypatch.setenv("MINI_DROP_PI_INTERNAL_TOKEN", token)
        yield base_url
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


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
) -> AgentTurnInput:
    return AgentTurnInput(
        diagnosis_id="diag-process",
        turn_id=turn_id,
        runtime_generation=generation,
        message="accept this shadow turn without model execution",
        references=[{"evidence_id": "evidence-process"}],
        requested_mode="COLLABORATE",
        client_command_id=command_id,
    )


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
