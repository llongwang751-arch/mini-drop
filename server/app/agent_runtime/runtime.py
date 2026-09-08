"""Runtime identity and framework decision shared by all diagnosis agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass


AGENT_FRAMEWORK = "langchain-create-agent/langgraph"
AGENT_VERSION = "diagnosis-agent-v4-lats"
SCOPE_AGENT_VERSION = "scope-agent-v1"


@dataclass(frozen=True)
class RuntimeDescriptor:
    framework: str = AGENT_FRAMEWORK
    durable_graph: bool = True
    shell_access: bool = False
    authority_model: str = "opaque-binding+policy+budget+evidence-gate"
    memory_model: str = (
        "business-state+thread-checkpoint+published-skill+explicit-operator-preference"
    )

    def as_dict(self) -> dict[str, object]:
        return asdict(self)
