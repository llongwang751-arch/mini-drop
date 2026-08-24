from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from typing import Any

TASK_ATTEMPT_AUTHORITY_BYTES = 32
TASK_ATTEMPT_AUTHORITY_LENGTH = TASK_ATTEMPT_AUTHORITY_BYTES * 2
TASK_ATTEMPT_AUTHORITY_SHA256_LENGTH = 64
_AUTHORITY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_VERIFIER_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class TaskDispatch:
    task: Any
    task_attempt_id: str
    task_attempt_authority: str

    def __getattr__(self, name: str) -> Any:
        return getattr(self.task, name)


@dataclass(frozen=True)
class AuthorizedTaskAttempt:
    task: Any
    task_attempt: Any


def generate_task_attempt_authority() -> str:
    return secrets.token_hex(TASK_ATTEMPT_AUTHORITY_BYTES)


def task_attempt_authority_sha256(authority: str) -> str:
    if not isinstance(authority, str) or _AUTHORITY_PATTERN.fullmatch(authority) is None:
        raise ValueError("invalid task attempt authority")
    return hashlib.sha256(authority.encode("ascii")).hexdigest()


def verify_task_attempt_authority(authority: str, verifier: str | None) -> bool:
    if not isinstance(verifier, str) or _VERIFIER_PATTERN.fullmatch(verifier) is None:
        return False
    try:
        candidate = task_attempt_authority_sha256(authority)
    except ValueError:
        return False
    return hmac.compare_digest(candidate, verifier)
