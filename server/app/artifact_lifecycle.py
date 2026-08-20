"""Artifact lifecycle retention policy and object-storage reconciliation.

The database owns the authoritative artifact list (bucket / object_key /
sha256); object storage is the physical store. This module reconciles the two
and computes which objects are expired under the replication guide's retention
policy (raw 7-30d, intermediate 1-7d, result 30-90d, logs 7-30d).

The pure functions are unit-tested against a mocked storage layer; the CLI
entrypoint wires them to the database for periodic maintenance runs.
"""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from server.app import storage

# Guide §9.7 retention per artifact family.
LIFECYCLE_RETENTION_DAYS: dict[str, int] = {
    "raw": 30,
    "intermediate": 7,
    "result": 90,
    "log": 30,
    "manifest": 90,
}

DEFAULT_RETENTION_DAYS = 30

# artifact_type -> lifecycle family.
_RESULT_TYPES = {
    "flamegraph_json",
    "flamegraph_svg",
    "top_json",
    "suggestions_md",
    "ebpf_metrics",
    "continuous_summary",
    "continuous_flamegraph_json",
    "continuous_flamegraph_svg",
    "continuous_top_json",
    "java_flamegraph_html",
    "memory_json",
    "sys_metrics",
}
_RAW_TYPES = {"raw", "ebpf_raw", "pprof_raw", "continuous_window"}


def classify_family(artifact_type: str | None) -> str:
    artifact_type = artifact_type or ""
    lowered = artifact_type.lower()
    if lowered in _RAW_TYPES or lowered.endswith("_raw"):
        return "raw"
    if lowered in _RESULT_TYPES:
        return "result"
    if "log" in lowered:
        return "log"
    if "manifest" in lowered:
        return "manifest"
    return "intermediate"


def retention_days(family: str) -> int:
    return LIFECYCLE_RETENTION_DAYS.get(family, DEFAULT_RETENTION_DAYS)


@dataclass
class ArtifactReconciliation:
    total_artifacts: int = 0
    total_objects: int = 0
    missing_objects: list[dict] = field(default_factory=list)
    orphan_objects: list[dict] = field(default_factory=list)
    hash_mismatches: list[dict] = field(default_factory=list)
    expirable_objects: list[dict] = field(default_factory=list)
    checked_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


def reconcile_artifacts(
    artifacts: list[dict],
    bucket: str,
    *,
    verify_hashes: bool = False,
    now: datetime | None = None,
) -> ArtifactReconciliation:
    """Compare the artifact table against object storage.

    Args:
        artifacts: artifact rows with ``bucket``, ``object_key``,
            ``artifact_type``, ``size_bytes``, ``sha256`` and ``created_at``.
        bucket: the bucket to scan (falls back to the row's own bucket).
        verify_hashes: when True, read every object and recompute SHA-256 to
            flag silent corruption (expensive; off by default).
        now: clock override for deterministic tests.

    Returns:
        ArtifactReconciliation with missing / orphan / mismatched / expirable
        object keys.
    """
    now = now or datetime.now(timezone.utc)
    objects = storage.list_objects(bucket, prefix="tasks/")
    by_key = {item["object_key"]: item for item in objects}

    db_keys: set[str] = set()
    missing: list[dict] = []
    mismatches: list[dict] = []
    expirable: list[dict] = []

    for artifact in artifacts:
        key = artifact.get("object_key")
        if not key:
            continue
        db_keys.add(key)
        row_bucket = artifact.get("bucket") or bucket
        if key not in by_key:
            missing.append(artifact)
            continue
        if verify_hashes and artifact.get("sha256"):
            try:
                payload = storage.read_object_bytes(row_bucket, key)
                actual = hashlib.sha256(payload).hexdigest()
                if actual != artifact["sha256"]:
                    mismatches.append({**artifact, "actual_sha256": actual})
            except Exception:
                continue

        object_modified = by_key[key].get("last_modified")
        age_reference = object_modified or artifact.get("created_at")
        if age_reference is None:
            continue
        if _is_expired(age_reference, classify_family(artifact.get("artifact_type")), now):
            expirable.append({**artifact, "object_key": key, "size_bytes": by_key[key].get("size")})

    orphaned = [
        {"object_key": key, "size": item.get("size"), "last_modified": item.get("last_modified")}
        for key, item in by_key.items()
        if key not in db_keys
    ]

    return ArtifactReconciliation(
        total_artifacts=len(artifacts),
        total_objects=len(objects),
        missing_objects=missing,
        orphan_objects=orphaned,
        hash_mismatches=mismatches,
        expirable_objects=expirable,
        checked_at=now,
    )


def _is_expired(reference, family: str, now: datetime) -> bool:
    if isinstance(reference, datetime):
        modified = reference
    elif isinstance(reference, str):
        try:
            modified = datetime.fromisoformat(reference.replace("Z", "+00:00"))
        except ValueError:
            return False
    else:
        return False
    if modified.tzinfo is None:
        modified = modified.replace(tzinfo=timezone.utc)
    return modified < now - timedelta(days=retention_days(family))


def list_expired_artifacts(
    artifacts: list[dict], now: datetime | None = None
) -> list[dict]:
    """Return artifact rows whose retention window has elapsed (pure helper)."""
    now = now or datetime.now(timezone.utc)
    return [
        artifact
        for artifact in artifacts
        if artifact.get("object_key")
        and _is_expired(
            artifact.get("created_at"),
            classify_family(artifact.get("artifact_type")),
            now,
        )
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Artifact lifecycle reconciliation")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--verify-hashes", action="store_true")
    args = parser.parse_args()

    from server.app.database import init_db, new_session
    from server.app.models import ArtifactModel

    init_db()
    session = new_session()
    try:
        rows = session.query(ArtifactModel).all()
        artifacts = [
            {
                "bucket": row.bucket,
                "object_key": row.object_key,
                "artifact_type": row.artifact_type,
                "size_bytes": row.size_bytes,
                "sha256": row.sha256,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    finally:
        session.close()

    report = reconcile_artifacts(
        artifacts, args.bucket, verify_hashes=args.verify_hashes
    )
    print(
        f"artifacts={report.total_artifacts} objects={report.total_objects} "
        f"missing={len(report.missing_objects)} orphans={len(report.orphan_objects)} "
        f"hash_mismatches={len(report.hash_mismatches)} expirable={len(report.expirable_objects)}"
    )
    for item in report.missing_objects:
        print(f"missing: {item.get('object_key')}")
    for item in report.orphan_objects:
        print(f"orphan: {item['object_key']}")
    for item in report.expirable_objects:
        print(f"expirable: {item['object_key']}")


if __name__ == "__main__":
    main()
