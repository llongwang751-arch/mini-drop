"""Short-term checkpoint and context-window policy."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentMemoryPolicy:
    checkpoint_backend: str
    max_messages: int
    max_tokens: int
    keep_tokens: int

    @classmethod
    def from_env(cls) -> "AgentMemoryPolicy":
        max_messages = min(
            max(int(os.getenv("MINI_DROP_AGENT_MEMORY_MAX_MESSAGES", "24")), 8),
            80,
        )
        max_tokens = min(
            max(int(os.getenv("MINI_DROP_AGENT_MEMORY_MAX_TOKENS", "12000")), 4000),
            60000,
        )
        keep_tokens = min(
            max(
                int(
                    os.getenv(
                        "MINI_DROP_AGENT_MEMORY_KEEP_TOKENS",
                        str(max_tokens // 2),
                    )
                ),
                2000,
            ),
            max_tokens - 1000,
        )
        return cls(
            checkpoint_backend=os.getenv(
                "MINI_DROP_AGENT_CHECKPOINT_BACKEND", "memory"
            ).strip().lower(),
            max_messages=max_messages,
            max_tokens=max_tokens,
            keep_tokens=keep_tokens,
        )
