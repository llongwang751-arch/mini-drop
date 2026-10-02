"""Strict aggregation for cross-environment Skill admission campaigns."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from typing import Any


EXPECTED_COLLECTORS = {
    "perf_cpu",
    "java_async",
    "go_pprof",
    "pyspy",
    "ebpf_io",
    "memory_smaps",
    "sys_metrics",
    "continuous_perf",
}
REQUIRED_SKILL_CASE_KINDS = {
    "POSITIVE",
    "MISLEADING_NEGATIVE",
    "ENVIRONMENT_DRIFT",
    "PAIRED_SKILL_NO_SKILL",
}


def _canonical_sha256(value: dict) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_collector_reports(reports: list[dict]) -> dict:
    if len(reports) < 2:
        raise ValueError("at least two live collector-matrix reports are required")
    environments: set[str] = set()
    report_hashes: list[str] = []
    for report in reports:
        if report.get("schema") != "mini-drop.collector-e2e-matrix.v1":
            raise ValueError("unsupported collector matrix schema")
        if report.get("evidence_level") != "LIVE_FULL_STACK":
            raise ValueError("collector matrix must contain LIVE_FULL_STACK evidence")
        supplied_hash = str(report.get("report_sha256") or "").lower()
        unsigned = dict(report)
        unsigned.pop("report_sha256", None)
        if supplied_hash != _canonical_sha256(unsigned):
            raise ValueError("collector matrix report digest does not match its content")
        environment = str(report.get("environment_fingerprint") or "").strip()
        if not environment or environment in environments:
            raise ValueError("collector matrix environments must be distinct and named")
        environments.add(environment)
        report_hashes.append(supplied_hash)

        rows = report.get("results") or []
        by_collector = {str(row.get("collector")): row for row in rows}
        if set(by_collector) != EXPECTED_COLLECTORS:
            missing = sorted(EXPECTED_COLLECTORS - set(by_collector))
            extra = sorted(set(by_collector) - EXPECTED_COLLECTORS)
            raise ValueError(f"collector matrix coverage mismatch: missing={missing}, extra={extra}")
        for collector, row in by_collector.items():
            if not row.get("passed"):
                raise ValueError(f"collector {collector} did not pass in {environment}")
            if str(row.get("status") or "").upper() != "DONE":
                raise ValueError(f"collector {collector} lacks terminal DONE evidence")
            if str(row.get("analysis_status") or "").upper() not in {
                "SUCCESS",
                "SUCCEEDED",
                "DONE",
            }:
                raise ValueError(f"collector {collector} lacks successful analysis evidence")
            if int(row.get("verified_artifact_count") or 0) < 1:
                raise ValueError(f"collector {collector} lacks a verified artifact")
    return {
        "environment_fingerprints": sorted(environments),
        "collector_kinds": sorted(EXPECTED_COLLECTORS),
        "collector_cells": len(environments) * len(EXPECTED_COLLECTORS),
        "collector_report_sha256": sorted(report_hashes),
    }


def build_campaign_admission(
    *,
    campaign_id: str,
    collector_reports: list[dict],
    skill_cases: list[dict],
    report_ref: str | None = None,
) -> dict[str, Any]:
    coverage = validate_collector_reports(collector_reports)
    environments = set(coverage["environment_fingerprints"])
    if not skill_cases:
        raise ValueError("campaign requires Skill positive, negative, drift and paired cases")

    kinds_by_environment: dict[str, set[str]] = defaultdict(set)
    counts: Counter[str] = Counter()
    case_ids: set[str] = set()
    passed_cases = 0
    false_activations = 0
    policy_violations = 0
    for case in skill_cases:
        case_id = str(case.get("case_id") or "").strip()
        environment = str(case.get("environment_fingerprint") or "").strip()
        kind = str(case.get("case_kind") or "").upper()
        if not case_id:
            raise ValueError("every Skill campaign case requires case_id")
        if case_id in case_ids:
            raise ValueError(f"duplicate Skill campaign case_id={case_id}")
        case_ids.add(case_id)
        if environment not in environments:
            raise ValueError(f"Skill case {case_id} references an unverified environment")
        if kind not in REQUIRED_SKILL_CASE_KINDS:
            raise ValueError(f"Skill case {case_id} has unsupported case_kind={kind}")
        kinds_by_environment[environment].add(kind)
        counts[kind] += 1
        passed_cases += bool(case.get("passed"))
        if kind == "MISLEADING_NEGATIVE":
            false_activations += bool(case.get("false_activation"))
        policy_violations += int(case.get("policy_violation_count") or 0)

    for environment in environments:
        missing = REQUIRED_SKILL_CASE_KINDS - kinds_by_environment[environment]
        if missing:
            raise ValueError(
                f"Skill campaign environment {environment} lacks cases: {sorted(missing)}"
            )
    negative_cases = counts["MISLEADING_NEGATIVE"]
    false_activation_rate = false_activations / negative_cases
    provenance = {
        "campaign_id": campaign_id,
        "collector_report_sha256": coverage["collector_report_sha256"],
        "skill_cases": skill_cases,
    }
    return {
        "campaign_id": campaign_id,
        "benchmark_kind": "CROSS_ENVIRONMENT_SKILL_CAMPAIGN",
        "report_sha256": _canonical_sha256(provenance),
        "environment_fingerprints": coverage["environment_fingerprints"],
        "collector_kinds": coverage["collector_kinds"],
        "positive_cases": counts["POSITIVE"],
        "misleading_negative_cases": negative_cases,
        "environment_drift_cases": counts["ENVIRONMENT_DRIFT"],
        "paired_skill_no_skill_cases": counts["PAIRED_SKILL_NO_SKILL"],
        "passed_cases": passed_cases,
        "total_cases": len(skill_cases),
        "false_activation_rate": round(false_activation_rate, 6),
        "policy_violation_count": policy_violations,
        "report_ref": report_ref,
    }
