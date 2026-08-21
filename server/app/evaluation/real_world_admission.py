"""Fail-closed admission contract for formally scored upstream replays."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
from typing import Any


ADMISSION_SCHEMA_VERSION = "real-world-admission-v1"
FULL_UPSTREAM_REPLAY = "FULL_UPSTREAM_REPLAY"
REQUIRED_EVIDENCE_ROLES = ("baseline", "incident", "verification")
_SHA = re.compile(r"^[0-9a-f]{40,64}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def canonical_hash(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(rendered).hexdigest()


def _required_string(mapping: dict[str, Any], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"formal admission missing {field}")
    return value


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"formal admission missing {field}")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"formal admission invalid {field}") from exc


def validate_formal_admission(
    run: dict[str, Any],
    *,
    case_id: str,
    minimum_repetitions: int,
    evidence_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Validate and return a hash-bound admission record or fail closed."""
    admission = run.get("admission")
    if not isinstance(admission, dict):
        raise ValueError(f"formal admission missing for {case_id}")
    if admission.get("schema_version") != ADMISSION_SCHEMA_VERSION:
        raise ValueError(f"formal admission schema invalid for {case_id}")
    if run.get("execution_fidelity") != FULL_UPSTREAM_REPLAY:
        raise ValueError(f"formal admission requires FULL_UPSTREAM_REPLAY for {case_id}")
    if admission.get("execution_fidelity") != FULL_UPSTREAM_REPLAY:
        raise ValueError(f"formal admission fidelity mismatch for {case_id}")
    if admission.get("case_id") != case_id or admission.get("run_id") != run.get("run_id"):
        raise ValueError(f"formal admission ownership mismatch for {case_id}")
    if run.get("status") != "COMPLETED" or admission.get("terminal_status") != "COMPLETED":
        raise ValueError(f"formal admission requires completed run for {case_id}")
    if admission.get("comparator_passed") is not True:
        raise ValueError(f"formal admission comparator failed for {case_id}")

    base_sha = _required_string(admission, "base_sha").lower()
    fix_sha = _required_string(admission, "fix_sha").lower()
    if not _SHA.fullmatch(base_sha) or not _SHA.fullmatch(fix_sha) or base_sha == fix_sha:
        raise ValueError(f"formal admission commit identity invalid for {case_id}")
    for field in (
        "repository_url",
        "source_integrity_hash",
        "dependency_lock_hash",
        "harness_integrity_hash",
    ):
        value = _required_string(admission, field)
        if field.endswith("hash") and not _SHA256.fullmatch(value):
            raise ValueError(f"formal admission invalid {field} for {case_id}")
    environment = admission.get("environment")
    if not isinstance(environment, dict):
        raise ValueError(f"formal admission missing environment for {case_id}")
    for field in ("os", "architecture", "runtime", "container_image_digest"):
        _required_string(environment, field)
    if not _SHA256.fullmatch(environment["container_image_digest"]):
        raise ValueError(f"formal admission invalid container image for {case_id}")

    evidence = list(evidence_by_id.values())
    roles = [item.get("role") for item in evidence]
    if len(evidence) != len(REQUIRED_EVIDENCE_ROLES) or sorted(roles) != sorted(REQUIRED_EVIDENCE_ROLES):
        raise ValueError(f"formal admission evidence roles invalid for {case_id}")
    ordered = sorted(evidence, key=lambda item: _timestamp(item.get("recorded_at"), "evidence recorded_at"))
    if [item["role"] for item in ordered] != list(REQUIRED_EVIDENCE_ROLES):
        raise ValueError(f"formal admission evidence phase order invalid for {case_id}")
    evidence_hashes = []
    for item in evidence:
        submitted_hash = item.get("integrity_hash")
        if not isinstance(submitted_hash, str) or not _SHA256.fullmatch(
            submitted_hash
        ):
            raise ValueError(
                f"formal admission evidence integrity hash invalid for {case_id}"
            )
        meaningful_evidence = {
            key: value
            for key, value in item.items()
            if key != "integrity_hash"
        }
        recomputed_hash = canonical_hash(meaningful_evidence)
        if submitted_hash != recomputed_hash:
            raise ValueError(
                f"formal admission evidence integrity mismatch for {case_id}"
            )
        evidence_hashes.append(recomputed_hash)
    evidence_hashes.sort()

    repetitions = admission.get("repetitions")
    if not isinstance(repetitions, list) or len(repetitions) < max(1, int(minimum_repetitions)):
        raise ValueError(f"formal admission repetitions insufficient for {case_id}")
    repetition_ids: set[str] = set()
    for repetition in repetitions:
        if not isinstance(repetition, dict):
            raise ValueError(f"formal admission repetition invalid for {case_id}")
        repetition_id = _required_string(repetition, "repetition_id")
        if repetition_id in repetition_ids:
            raise ValueError(f"formal admission duplicate repetition for {case_id}")
        repetition_ids.add(repetition_id)
        if repetition.get("terminal_status") != "COMPLETED":
            raise ValueError(f"formal admission repetition incomplete for {case_id}")
        if not all(
            repetition.get(field) is True
            for field in ("baseline_stable", "incident_reproduced", "verification_recovered", "comparator_passed")
        ):
            raise ValueError(f"formal admission unstable repetition for {case_id}")
        if repetition.get("evidence_roles") != list(REQUIRED_EVIDENCE_ROLES):
            raise ValueError(f"formal admission repetition roles invalid for {case_id}")
        if sorted(repetition.get("evidence_hashes", [])) != evidence_hashes:
            raise ValueError(f"formal admission repetition evidence mismatch for {case_id}")

    expected_hash = admission.get("admission_hash")
    meaningful = {key: value for key, value in admission.items() if key != "admission_hash"}
    if expected_hash != canonical_hash(meaningful):
        raise ValueError(f"formal admission integrity mismatch for {case_id}")
    return admission
