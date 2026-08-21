"""Small JSON logging helpers for the server runtime."""

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
_URL_CREDENTIAL_RE = re.compile(r"(\b[a-z][a-z0-9+.-]*://)([^/@\s]+):([^/@\s]+)@", re.IGNORECASE)
_TEXT_SECRET_RE = re.compile(
    r"(?P<label>(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"password|secret|client[_-]?secret|private[_-]?key|signature|"
    r"sig|token))(?P<sep>\s*[:=]\s*)(?P<value>[^\s,;&]+)",
    re.IGNORECASE,
)
_BASIC_AUTH_RE = re.compile(r"(\bBasic\s+)[A-Za-z0-9+/=]+", re.IGNORECASE)
_PEM_BLOCK_RE = re.compile(
    r"-----BEGIN [^-]+-----.*?-----END [^-]+-----",
    re.IGNORECASE | re.DOTALL,
)


def _redact(value: Any, *, key: str = "") -> Any:
    if _SECRET_KEY_RE.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        value = _URL_CREDENTIAL_RE.sub(r"\1[REDACTED]@", value)
        value = _BASIC_AUTH_RE.sub(r"\1[REDACTED]", value)
        value = _PEM_BLOCK_RE.sub("[REDACTED]", value)
        return _TEXT_SECRET_RE.sub(
            lambda match: f"{match.group('label')}{match.group('sep')}[REDACTED]",
            value,
        )
    return value


def log_event(level: str, event: str, **fields: Any) -> None:
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": level,
        "event": event,
        **_redact(fields),
    }
    stream = sys.stderr if level in {"error", "warning"} else sys.stdout
    print(json.dumps(record, ensure_ascii=False, default=str), file=stream)
