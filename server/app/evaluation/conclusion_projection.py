"""Fail-closed projection of diagnosis conclusions for evaluator scoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ConclusionProjectionError(ValueError):
    """Stable, non-sensitive scoring-shape error."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ConclusionProjection:
    assessment: dict[str, Any]
    root_location: dict[str, Any]
    domain_cause: dict[str, Any]


def _mapping(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConclusionProjectionError(code)
    return value


def _field(mapping: dict[str, Any], name: str, code: str) -> Any:
    if name not in mapping:
        raise ConclusionProjectionError(code)
    return mapping[name]


def _same_value(left: Any, right: Any) -> bool:
    return left == right


def project_conclusion(conclusion: Any) -> ConclusionProjection:
    """Normalize current and legacy conclusion shapes without guessing values.

    The current contract is ``current_conclusion`` containing the four scoring
    fields.  Older artifacts may put those fields at the conclusion root or
    under ``cluster_assessment``.  If both forms are present, their scoring
    values must agree; otherwise the artifact is rejected deterministically.
    """
    root = _mapping(conclusion, "CONCLUSION_INVALID")
    nested = root.get("current_conclusion")
    if nested is not None:
        current = _mapping(nested, "CONCLUSION_CURRENT_INVALID")
        legacy_assessment = root.get("cluster_assessment")
        legacy_root = root.get("root_location")
        legacy_domain = root.get("domain_cause")
        if legacy_assessment is not None:
            legacy_assessment = _mapping(legacy_assessment, "CONCLUSION_ASSESSMENT_INVALID")
        if legacy_root is not None:
            legacy_root = _mapping(legacy_root, "CONCLUSION_LOCATION_INVALID")
        if legacy_domain is not None:
            legacy_domain = _mapping(legacy_domain, "CONCLUSION_DOMAIN_INVALID")
        assessment = _mapping(
            current.get("cluster_assessment", current),
            "CONCLUSION_ASSESSMENT_INVALID",
        )
        root_location = _mapping(
            current.get("root_location", assessment.get("root_location")),
            "CONCLUSION_LOCATION_INVALID",
        )
        domain_cause = _mapping(
            current.get("domain_cause", assessment.get("domain_cause")),
            "CONCLUSION_DOMAIN_INVALID",
        )
        for name, value, legacy in (
            ("classification", assessment.get("classification"),
             legacy_assessment.get("classification") if legacy_assessment else None),
            ("root_location", root_location, legacy_root),
            ("domain_cause", domain_cause, legacy_domain),
        ):
            if legacy is not None and not _same_value(value, legacy):
                raise ConclusionProjectionError("CONCLUSION_CONFLICT")
    else:
        assessment = root.get("cluster_assessment")
        assessment = _mapping(assessment, "CONCLUSION_ASSESSMENT_MISSING")
        root_location = root.get("root_location", assessment.get("root_location"))
        domain_cause = root.get("domain_cause", assessment.get("domain_cause"))
        root_location = _mapping(root_location, "CONCLUSION_LOCATION_MISSING")
        domain_cause = _mapping(domain_cause, "CONCLUSION_DOMAIN_MISSING")

    _field(root_location, "type", "CONCLUSION_LOCATION_TYPE_MISSING")
    _field(root_location, "target_ref", "CONCLUSION_TARGET_MISSING")
    _field(domain_cause, "type", "CONCLUSION_DOMAIN_TYPE_MISSING")
    _field(assessment, "classification", "CONCLUSION_CLASSIFICATION_MISSING")
    if root_location["target_ref"] is not None and (
        not isinstance(root_location["target_ref"], str)
        or not root_location["target_ref"].strip()
    ):
        raise ConclusionProjectionError("CONCLUSION_TARGET_INVALID")
    return ConclusionProjection(
        assessment=assessment,
        root_location=root_location,
        domain_cause=domain_cause,
    )
