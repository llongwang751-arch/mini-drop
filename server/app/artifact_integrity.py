"""Artifact identity, manifest generation, and content verification helpers.

Artifact metadata is not evidence unless it can be bound to immutable bytes.
This module keeps the policy independent from SQLAlchemy and the analyzer so
both production and in-memory repositories use the same manifest contract.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Iterable

from server.app import storage

MANIFEST_VERSION = "mini-drop.artifact.v1"
SHA256_HEX_LENGTH = 64


class ArtifactIntegrityError(RuntimeError):
    """Raised when persisted artifact bytes do not match their manifest."""


def hash_chunks(chunks: Iterable[bytes]) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for chunk in chunks:
        if not chunk:
            continue
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def hash_file(path: str | os.PathLike[str], chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    def chunks() -> Iterable[bytes]:
        with Path(path).open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    return
                yield chunk

    return hash_chunks(chunks())


def normalize_sha256(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text[7:]
    if len(text) != SHA256_HEX_LENGTH:
        return None
    if any(character not in "0123456789abcdef" for character in text):
        return None
    return text


def prepare_artifact(task_id: str, artifact: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with a canonical manifest and any locally-computable hash."""

    prepared = dict(artifact)
    local_path = str(prepared.get("local_path") or "")
    digest = normalize_sha256(prepared.get("sha256"))
    size_bytes = max(0, int(prepared.get("size_bytes", 0) or 0))
    status = "DECLARED" if digest else "LEGACY_UNVERIFIED"
    reason = "Agent/producer supplied SHA-256" if digest else "legacy artifact has no SHA-256"

    if local_path and Path(local_path).is_file():
        computed, computed_size = hash_file(local_path)
        if digest and digest != computed:
            raise ArtifactIntegrityError(
                f"artifact SHA-256 mismatch before persistence: {prepared.get('filename') or local_path}"
            )
        digest = computed
        size_bytes = computed_size
        status = "VERIFIED"
        reason = "server verified local artifact bytes"

    manifest = dict(prepared.get("manifest") or {})
    manifest.update({
        "manifest_version": MANIFEST_VERSION,
        "task_id": task_id,
        "artifact_type": str(prepared.get("artifact_type") or "raw"),
        "bucket": str(prepared.get("bucket") or "mini-drop"),
        "object_key": str(prepared.get("object_key") or prepared.get("cos_key") or ""),
        "filename": str(prepared.get("filename") or ""),
        "content_type": str(prepared.get("content_type") or "application/octet-stream"),
        "size_bytes": size_bytes,
        "sha256": digest,
    })
    prepared["size_bytes"] = size_bytes
    prepared["sha256"] = digest
    prepared["manifest"] = manifest
    prepared["integrity_status"] = status
    prepared["integrity_reason"] = reason
    return prepared


def verify_artifact_bytes(artifact: dict[str, Any]) -> tuple[str, str]:
    """Verify one artifact against its declared digest.

    Legacy rows without a digest remain readable but are explicitly identified
    as unverified. New Agent uploads always carry a digest.
    """

    expected = normalize_sha256(artifact.get("sha256"))
    if not expected:
        return "LEGACY_UNVERIFIED", "artifact has no SHA-256"

    local_path = str(artifact.get("local_path") or "")
    if local_path and Path(local_path).is_file():
        actual, actual_size = hash_file(local_path)
        source = local_path
    else:
        object_key = str(artifact.get("object_key") or "")
        if not object_key:
            raise ArtifactIntegrityError("artifact has SHA-256 but no readable object reference")
        bucket = str(artifact.get("bucket") or "mini-drop")
        actual, actual_size = hash_chunks(storage.stream_object(bucket, object_key))
        source = f"{bucket}/{object_key}"

    declared_size = max(0, int(artifact.get("size_bytes", 0) or 0))
    if actual != expected:
        raise ArtifactIntegrityError(f"artifact SHA-256 mismatch: {source}")
    if declared_size and actual_size != declared_size:
        raise ArtifactIntegrityError(
            f"artifact size mismatch: {source}; expected={declared_size}, actual={actual_size}"
        )
    return "VERIFIED", "Analyzer verified artifact SHA-256 and size"

