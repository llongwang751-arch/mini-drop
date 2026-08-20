"""Translate persisted artifacts into conservative AI evidence quality."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from server.app.artifact_contracts import COLLECTOR_CONTRACTS


@dataclass(frozen=True)
class ArtifactEvidenceAssessment:
    schema_valid: bool
    analyzer_validated: bool
    sample_count: int
    sample_count_known: bool
    minimum_samples: int
    limitations: tuple[str, ...]


MINIMUM_SAMPLES = {
    "perf_cpu": 100,
    "ebpf_io": 10,
    "pyspy": 50,
    "continuous_perf": 1,
    "java_async": 50,
    "go_pprof": 50,
    "memory_smaps": 3,
    "sys_metrics": 5,
}

COLLECTOR_ALIASES = {"perf": "perf_cpu"}
ARTIFACT_ALIASES = {"flamegraph": "flamegraph_json"}


def assess_artifact_evidence(
    collector_type: str,
    artifact_type: str,
    metadata: dict[str, Any],
    *,
    analyzer_validated: bool,
) -> ArtifactEvidenceAssessment:
    collector_type = COLLECTOR_ALIASES.get(collector_type, collector_type)
    artifact_type = ARTIFACT_ALIASES.get(artifact_type, artifact_type)
    contract = COLLECTOR_CONTRACTS.get(collector_type)
    schema_valid = bool(
        contract is not None and artifact_type in contract.analysis_types
    )
    sample_count, known = extract_sample_count(metadata, collector_type)
    minimum = MINIMUM_SAMPLES.get(collector_type, 100)
    limitations: list[str] = []
    if not schema_valid:
        limitations.append("该产物是原始文件或不属于已注册的诊断证据类型")
    if not analyzer_validated:
        limitations.append("产物未经过成功的版本化 Analyzer Job 验证")
    if not known:
        limitations.append("采集器未提供可核验的样本数量")
    elif sample_count < minimum:
        limitations.append(f"样本数不足 {minimum}")
    return ArtifactEvidenceAssessment(
        schema_valid=schema_valid,
        analyzer_validated=analyzer_validated,
        sample_count=sample_count,
        sample_count_known=known,
        minimum_samples=minimum,
        limitations=tuple(limitations),
    )


def extract_sample_count(
    metadata: dict[str, Any],
    collector_type: str,
) -> tuple[int, bool]:
    for key in ("sample_count", "total_samples", "event_count", "samples"):
        value = metadata.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return max(0, value), True
    if collector_type == "continuous_perf":
        windows = metadata.get("windows")
        if isinstance(windows, list):
            successful = sum(1 for row in windows if isinstance(row, dict) and row.get("ok"))
            return successful, True
    return 0, False
