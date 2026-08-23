from __future__ import annotations

import os
from enum import Enum


class AgentRuntimeMode(str, Enum):
    DETERMINISTIC = "deterministic"
    PI_SHADOW = "pi_shadow"
    PI = "pi"


def runtime_mode() -> AgentRuntimeMode:
    raw = os.getenv("MINI_DROP_AGENT_RUNTIME", "deterministic").strip().lower()
    try:
        return AgentRuntimeMode(raw)
    except ValueError as exc:
        raise RuntimeError(
            "invalid MINI_DROP_AGENT_RUNTIME; expected deterministic, pi_shadow, or pi"
        ) from exc


def pi_runtime_url() -> str:
    return os.getenv("MINI_DROP_PI_RUNTIME_URL", "").strip()


def pi_runtime_version() -> str:
    return os.getenv("MINI_DROP_PI_RUNTIME_VERSION", "0.83.0").strip()


def pi_internal_token() -> str:
    return os.getenv("MINI_DROP_PI_INTERNAL_TOKEN", "").strip()
