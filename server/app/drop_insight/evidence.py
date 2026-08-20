from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, BaseModel, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceSource(StrictModel):
    tool_name: str
    task_id: str
    task_attempt_id: str
    artifact_id: str
    artifact_sha256: str = ""
    analysis_job_id: str = ""
    analyzer_type: str = ""
    analyzer_version: str
    analyzer_output_schema_version: str = ""
    observation_json_pointer: str = "/"


class EvidenceScope(StrictModel):
    agent_id: str
    service: str | None = None
    host_id: str | None = None
    instance_id: str | None = None
    container_id: str | None = None
    pid: int | None = Field(default=None, ge=1)


class EvidenceTimeRange(StrictModel):
    start: datetime
    end: datetime
    timezone: str = "Asia/Shanghai"


class EvidenceQuality(StrictModel):
    level: Literal["HIGH", "MEDIUM", "LOW"]
    sample_count: int = Field(ge=0)
    sample_count_known: bool = True
    degraded: bool
    target_match: bool
    time_overlap: bool
    schema_valid: bool = True
    analyzer_validated: bool = True
    minimum_samples: int | None = Field(default=None, ge=1)


class EvidenceEnvelope(StrictModel):
    evidence_id: str
    diagnosis_id: str
    evidence_type: str
    source: EvidenceSource
    scope: EvidenceScope
    time_range: EvidenceTimeRange
    observation: dict[str, Any]
    quality: EvidenceQuality
    limitations: list[str] = Field(default_factory=list)


def classify_evidence(envelope: EvidenceEnvelope) -> dict[str, Any]:
    reasons = []
    if not envelope.quality.target_match:
        reasons.append("目标不匹配")
    if not envelope.quality.time_overlap:
        reasons.append("时间窗口不重叠")
    if not envelope.quality.schema_valid:
        reasons.append("产物不属于已注册的诊断证据契约")
    if not envelope.quality.analyzer_validated:
        reasons.append("产物未经过 Analyzer Job 验证")
    if envelope.quality.sample_count_known and envelope.quality.sample_count == 0:
        reasons.append("空样本")
    if not envelope.source.task_attempt_id:
        reasons.append("缺少 TaskAttempt 引用")
    if not envelope.source.artifact_id:
        reasons.append("缺少 Artifact 引用")
    if envelope.source.artifact_id:
        digest = envelope.source.artifact_sha256
        if len(digest) != 64 or any(
            ch not in "0123456789abcdefABCDEF" for ch in digest
        ):
            reasons.append("缺少有效 Artifact SHA-256")
        if not envelope.source.analysis_job_id:
            reasons.append("缺少 AnalysisJob 引用")
        if not envelope.source.analyzer_output_schema_version:
            reasons.append("缺少 Analyzer 输出 Schema 版本")
        if not envelope.source.observation_json_pointer.startswith("/"):
            reasons.append("Observation JSON Pointer 非法")
    if reasons:
        return {"decision": "REJECT", "can_support_conclusion": False, "reasons": reasons}

    limitations = list(envelope.limitations)
    if envelope.quality.degraded:
        limitations.append("采集已降级")
    if not envelope.quality.sample_count_known:
        limitations.append("样本数量未知")
    min_samples = envelope.quality.minimum_samples or (
        5
        if envelope.source.tool_name == "sys_metrics"
        or envelope.evidence_type.startswith("SYS_METRICS")
        else 100
    )
    if (
        envelope.quality.sample_count_known
        and envelope.quality.sample_count < min_samples
    ):
        limitations.append(f"样本数不足 {min_samples}")
    if envelope.quality.level == "LOW":
        limitations.append("证据质量为 LOW")
    can_support = not limitations
    return {
        "decision": "ACCEPT_SUPPORT" if can_support else "ACCEPT_LIMITED",
        "can_support_conclusion": can_support,
        "reasons": limitations,
    }


def calibrate_confidence(
    supporting: list[EvidenceEnvelope],
    counter: list[EvidenceEnvelope],
    coverage_ratio: float,
) -> float:
    accepted_support = [item for item in supporting if classify_evidence(item)["can_support_conclusion"]]
    accepted_counter = [item for item in counter if classify_evidence(item)["can_support_conclusion"]]
    if not accepted_support:
        return 0.0

    quality_weight = {"HIGH": 1.0, "MEDIUM": 0.7, "LOW": 0.3}
    support_score = sum(quality_weight[item.quality.level] for item in accepted_support)
    independent_sources = len({item.source.tool_name for item in accepted_support})
    counter_score = sum(quality_weight[item.quality.level] for item in accepted_counter)
    raw = (
        0.25
        + min(0.35, support_score * 0.15)
        + min(0.2, independent_sources * 0.1)
        + max(0.0, min(1.0, coverage_ratio)) * 0.2
        - min(0.5, counter_score * 0.2)
    )
    return round(max(0.0, min(0.99, raw)), 2)
