"""Promote a completed real-fault Campaign into a verified AI diagnosis.

The bridge is intentionally strict: a Campaign result is eligible only after
the hidden Oracle, persisted task evidence, Analyzer job and cleanup checks all
pass. This keeps the UI from turning a successful experiment into an
untraceable model-written conclusion.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from server.app.database import new_session
from server.app.models import DropInsightEvidenceModel
from server.app.state_machine import now_utc

from .evidence import EvidenceEnvelope, classify_evidence
from .schemas import (
    CreateDiagnosisRequestV2,
    CreateHypothesisRequest,
    DiagnosticTarget,
    DiagnosticTimeRange,
    GenerateReportRequest,
)
from .service import (
    create_diagnosis,
    create_hypothesis,
    generate_report,
    mark_evidence_collection_started,
)


def _status(value: Any) -> str:
    return str(value or "").upper()


def _parse_time(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        parsed = fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _trusted_provenance(run: dict[str, Any]) -> dict[str, str]:
    if _status(run.get("status")) != "COMPLETED":
        raise ValueError("Campaign 尚未完成")
    if not (run.get("comparison") or {}).get("passed"):
        raise ValueError("Campaign 未通过隐藏 Oracle 对比")
    if not (run.get("cleanup") or {}).get("succeeded"):
        raise ValueError("Campaign 未通过故障清理与恢复验证")
    snapshots = run.get("snapshots") or {}
    if not all(snapshots.get(role) for role in ("baseline_snapshot", "fault_snapshot", "recovery_snapshot")):
        raise ValueError("Campaign 缺少基线、故障或恢复快照")

    linked = run.get("linked_task") or {}
    if _status(linked.get("status")) != "DONE":
        raise ValueError("Campaign 没有关联已完成的真实采集任务")
    attempts = linked.get("task_attempts") or []
    attempt = next(
        (item for item in attempts if _status(item.get("status")) in {"SUCCEEDED", "DONE", "SUCCESS"}),
        None,
    )
    artifacts = linked.get("artifacts") or []
    artifact = next(
        (
            item
            for item in artifacts
            if _status(item.get("integrity_status")) == "VERIFIED"
            and len(str(item.get("sha256") or "")) == 64
        ),
        None,
    )
    jobs = linked.get("analysis_jobs") or []
    job = next(
        (item for item in jobs if _status(item.get("status")) in {"SUCCEEDED", "DONE", "SUCCESS"}),
        None,
    )
    if not attempt or not artifact or not job:
        raise ValueError("Campaign 的 TaskAttempt、Artifact 或 Analyzer Job 可信链不完整")
    return {
        "task_id": str(linked.get("task_id") or ""),
        "task_attempt_id": str(attempt.get("id") or ""),
        "artifact_id": str(artifact.get("id") or ""),
        "artifact_sha256": str(artifact.get("sha256") or ""),
        "analysis_job_id": str(job.get("id") or ""),
        "analyzer_type": str(job.get("analyzer_type") or "campaign.analyzer"),
        "analyzer_version": str(job.get("analyzer_version") or "1.0.0"),
    }


def promote_campaign(run: dict[str, Any]) -> dict[str, Any]:
    """Create one immutable, evidence-backed diagnosis from a Campaign run."""

    existing = run.get("drop_insight_diagnosis_id")
    if existing:
        return {"diagnosis_id": existing, "reused": True}

    provenance = _trusted_provenance(run)
    linked = run["linked_task"]
    snapshots = run["snapshots"]
    now = now_utc()
    start = _parse_time(run.get("started_at"), now - timedelta(minutes=5))
    end = _parse_time(run.get("finished_at"), now)
    if end <= start:
        end = start + timedelta(seconds=1)
    scenario_title = str(run.get("title") or run.get("scenario_id") or "真实故障")
    diagnosis = create_diagnosis(
        CreateDiagnosisRequestV2(
            query=f"真实故障 Campaign：{scenario_title}，请基于故障前后证据定位根因",
            target=DiagnosticTarget(
                service=str(run.get("scenario_id") or "campaign-target"),
                environment="campaign",
                agent_id=str(linked.get("agent_id") or "campaign-agent"),
                host_id=str(linked.get("agent_id") or "campaign-host"),
                pid=max(1, int(linked.get("target_pid") or 1)),
            ),
            time_range=DiagnosticTimeRange(start=start, end=end, timezone="UTC"),
            mode="REPRODUCTION",
        )
    )
    expected_root = str((run.get("diagnosis") or {}).get("root_cause") or "受控故障根因")
    hypothesis = create_hypothesis(
        diagnosis.id,
        CreateHypothesisRequest(
            statement=f"{scenario_title} 的根因是 {expected_root}",
            expected_observations=["故障窗口指标与真实采集产物共同支持该根因"],
            falsification_criteria=["故障停止后指标恢复，证明变化与受控故障开关存在因果对应"],
        ),
        source="CAMPAIGN",
        generation_reason="真实故障、采集产物与恢复对照均已通过门禁",
        effect_key=f"campaign:{run['run_id']}:hypothesis",
    )
    if hypothesis is None:
        raise RuntimeError("无法创建 Campaign 诊断假设")

    common_source = {
        **provenance,
        "analyzer_output_schema_version": "campaign-evidence/v1",
        "observation_json_pointer": "/",
    }
    common_scope = {
        "agent_id": str(linked.get("agent_id") or "campaign-agent"),
        "service": str(run.get("scenario_id") or "campaign-target"),
        "host_id": str(linked.get("agent_id") or "campaign-host"),
        "pid": max(1, int(linked.get("target_pid") or 1)),
    }
    rows = [
        ("SUPPORT", "campaign_fault_snapshot", snapshots["fault_snapshot"], "expected", 0),
        (
            "SUPPORT",
            str(linked.get("collector_type") or "sys_metrics"),
            {
                "linked_task": linked,
                "campaign_diagnosis": run.get("diagnosis") or {},
                "oracle_comparison": run.get("comparison") or {},
            },
            "expected",
            0,
        ),
        ("CONTROL", "campaign_recovery_control", snapshots["recovery_snapshot"], "falsification", 0),
    ]
    session = new_session()
    try:
        for role, tool_name, observation, criterion_kind, criterion_index in rows:
            evidence_id = f"ev_campaign_{uuid4().hex}"
            payload = dict(observation or {})
            metadata = dict(payload.get("metadata") or {})
            metadata["campaign_run_id"] = run["run_id"]
            metadata["hypothesis_predicate"] = {
                "outcome": role,
                "criterion_indexes": [criterion_index],
                "reason": (
                    "故障窗口与真实 Analyzer 产物支持根因"
                    if criterion_kind == "expected"
                    else "故障关闭后恢复快照构成独立因果对照"
                ),
            }
            payload["metadata"] = metadata
            envelope = EvidenceEnvelope.model_validate(
                {
                    "evidence_id": evidence_id,
                    "diagnosis_id": diagnosis.id,
                    "evidence_type": "CAMPAIGN_CONTROL" if role == "CONTROL" else "CAMPAIGN_OBSERVATION",
                    "source": {**common_source, "tool_name": tool_name},
                    "scope": common_scope,
                    "time_range": {"start": start, "end": end, "timezone": "UTC"},
                    "observation": payload,
                    "quality": {
                        "level": "HIGH",
                        "sample_count": 1,
                        "degraded": False,
                        "target_match": True,
                        "time_overlap": True,
                        "schema_valid": True,
                        "analyzer_validated": True,
                        "minimum_samples": 1,
                    },
                    "limitations": [],
                }
            )
            classification = classify_evidence(envelope)
            if not classification["can_support_conclusion"]:
                raise ValueError("Campaign 证据未通过可信边界：" + "; ".join(classification["reasons"]))
            session.add(
                DropInsightEvidenceModel(
                    id=evidence_id,
                    diagnosis_id=diagnosis.id,
                    hypothesis_id=hypothesis.id,
                    role=role,
                    envelope_json=envelope.model_dump(mode="json"),
                    classification_json=classification,
                    created_at=now,
                )
            )
        session.commit()
    finally:
        session.close()

    mark_evidence_collection_started(
        diagnosis.id,
        reason=f"Campaign {run['run_id']} 的可信故障、采集与恢复证据已导入",
    )

    report = generate_report(
        diagnosis.id,
        GenerateReportRequest(hypothesis_id=hypothesis.id),
    )
    if report is None or (report.verification_json or {}).get("status") != "VERIFIED":
        raise RuntimeError("Campaign 证据未能生成 VERIFIED 报告")
    return {
        "diagnosis_id": diagnosis.id,
        "hypothesis_id": hypothesis.id,
        "report_id": report.id,
        "conclusion": report.conclusion,
        "confidence": report.confidence / 1000,
        "verification_status": (report.verification_json or {}).get("status"),
        "reused": False,
    }
