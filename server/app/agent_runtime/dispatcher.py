from __future__ import annotations

from server.app.agent_runtime.config import AgentRuntimeMode, runtime_mode
from server.app.agent_runtime.deterministic import DeterministicAgentRuntime
from server.app.agent_runtime.pi_adapter import PiAgentRuntimeAdapter

_runtime: DeterministicAgentRuntime | PiAgentRuntimeAdapter | None = None


def get_runtime() -> DeterministicAgentRuntime | PiAgentRuntimeAdapter:
    global _runtime
    if _runtime is not None:
        return _runtime
    mode = runtime_mode()
    if mode in {AgentRuntimeMode.PI, AgentRuntimeMode.PI_SHADOW}:
        _runtime = PiAgentRuntimeAdapter()
    else:
        _runtime = DeterministicAgentRuntime()
    return _runtime


def reset_runtime() -> None:
    global _runtime
    _runtime = None


def active_runtime_info() -> dict[str, object]:
    mode = runtime_mode().value
    try:
        runtime = get_runtime()
    except RuntimeError as exc:
        return {
            "runtime_type": "pi",
            "runtime_version": None,
            "mode": mode,
            "ready": False,
            "error": str(exc),
        }
    return {
        "runtime_type": runtime.runtime_type,
        "runtime_version": runtime.runtime_version,
        "mode": mode,
        "ready": True,
    }
