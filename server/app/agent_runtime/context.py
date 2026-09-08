"""Trusted context assembly and deterministic JSON normalization."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel


def trusted_json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def trusted_context_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=trusted_json_default,
    )


def normalize_trusted_context(value: Any) -> Any:
    """Return a JSON-native copy safe for prompts and checkpoints."""

    return json.loads(trusted_context_json(value))


def bounded_tail(values: list[Any] | tuple[Any, ...], limit: int) -> tuple[Any, ...]:
    """Keep the newest bounded records before token-level summarization."""

    return tuple(normalize_trusted_context(list(values)[-max(0, limit):]))
