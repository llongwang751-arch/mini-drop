"""Canonical serialization for immutable diagnosis artifacts."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from typing import Any

from server.app.evaluation.schemas import FrozenDiagnosisArtifact


def canonicalize(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): canonicalize(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    return value


def canonical_artifact_json(artifact: FrozenDiagnosisArtifact | dict[str, Any]) -> str:
    validated = (
        artifact
        if isinstance(artifact, FrozenDiagnosisArtifact)
        else FrozenDiagnosisArtifact.model_validate(artifact)
    )
    return json.dumps(
        canonicalize(validated.model_dump(mode="python")),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def artifact_hash(canonical_json: str) -> str:
    return "sha256:" + hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
