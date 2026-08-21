"""Small JSON logger used by the Agent runtime."""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any


_SECRET_KEY_RE = re.compile(
    r"(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"password|secret|evaluation[_-]?oracle|ground[_-]?truth|oracle[_-]?answer)",
    re.IGNORECASE,
)
_URL_CREDENTIAL_RE = re.compile(r"(https?://)([^/@\s]+):([^/@\s]+)@", re.IGNORECASE)


def _redact(value: Any, *, key: str = "") -> Any:
    if _SECRET_KEY_RE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _URL_CREDENTIAL_RE.sub(r"\1[REDACTED]@", value)
    return value


def log_event(level: str, event: str, **fields: Any) -> None:
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "event": event,
        **_redact(fields),
    }
    print(json.dumps(record, ensure_ascii=False, default=str), file=sys.stderr if level == "error" else sys.stdout)
