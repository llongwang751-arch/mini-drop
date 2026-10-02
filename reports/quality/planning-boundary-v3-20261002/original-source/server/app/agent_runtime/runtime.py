"""Runtime identity and framework decision shared by all diagnosis agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass


AGENT_FRAMEWORK = "langchain-create-agent/langgraph"
AGENT_VERSION = "diagnosis-agent-v8-request-intent"
SCOPE_AGENT_VERSION = "scope-agent-v1"


def checkpoint_thread_id(diagnosis_id: str, *, agent_version: str = AGENT_VERSION) -> str:
    """Version the physical thread key; top-level LangGraph namespaces are empty."""
    if not diagnosis_id.strip() or not agent_version.strip():
        raise ValueError("checkpoint identity requires diagnosis ID and agent version")
    return f"{agent_version}:{diagnosis_id}"


@dataclass(frozen=True)
class RuntimeDescriptor:
    framework: str = AGENT_FRAMEWORK
    durable_graph: bool = True
    shell_access: bool = False
    authority_model: str = "opaque-binding+policy+budget+evidence-gate"
    memory_model: str = (
        "business-state+thread-checkpoint+published-skill+explicit-operator-preference+scoped-incident-reference"
    )

    def as_dict(self) -> dict[str, object]:
        return asdict(self)
