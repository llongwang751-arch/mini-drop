"""Bounded S3 object metadata persisted in protected backup manifests.

Transport, authentication and encryption headers never become restore PUT
headers. Encryption is supplied by the destination bucket's policy.
"""

import re
from collections.abc import Mapping

OBJECT_HEADERS = frozenset(
    {
        "cache-control",
        "content-encoding",
        "content-disposition",
        "content-language",
    }
)
_USER_HEADER = re.compile(r"x-amz-meta-[a-z0-9!#$%&'*+.^_`|~-]+\Z")
_MAX_BYTES = 8192
_MAX_FIELDS = 128


def validate_metadata(value):
    """Validate metadata from an untrusted backup before making any PUT."""
    if value is None:
        return {}  # Backups created before metadata preservation.
    if not isinstance(value, Mapping) or len(value) > _MAX_FIELDS:
        raise ValueError("Invalid or oversized object metadata")
    result = {}
    size = 0
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError("Object metadata must contain string headers")  # noqa: TRY004 - corrupt backup data has one validation error type
        normalized = key.lower()
        if (
            normalized in result
            or normalized not in OBJECT_HEADERS
            and not _USER_HEADER.fullmatch(normalized)
        ):
            raise ValueError("Invalid or duplicate object metadata header")
        if any(ord(char) < 32 or ord(char) == 127 for char in item):
            raise ValueError("Invalid object metadata value")
        size += len(normalized.encode("utf-8")) + len(item.encode("utf-8"))
        if size > _MAX_BYTES:
            raise ValueError("Object metadata exceeds the backup budget")
        result[normalized] = item
    return result


def capture_metadata(headers):
    """Extract only content metadata; do not retain SSE/auth/response headers."""
    selected = {}
    seen = set()
    for key, value in headers.items():
        if not isinstance(key, str):
            raise ValueError("Invalid S3 response header")  # noqa: TRY004 - uniform metadata validation failure
        normalized = key.lower()
        if normalized in OBJECT_HEADERS or normalized.startswith("x-amz-meta-"):
            if normalized in seen:
                raise ValueError("Duplicate object metadata response header")
            seen.add(normalized)
            selected[normalized] = value
    return validate_metadata(selected)


def verify_metadata(expected, headers):
    if validate_metadata(expected) != capture_metadata(headers):
        raise ValueError("Isolated S3 restore metadata mismatch")
