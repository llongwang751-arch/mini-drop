from __future__ import annotations

import re
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select, update

from server.app.database import new_session
from server.app.models import (
    AgentModel,
    AnalysisJobModel,
    ArtifactModel,
    DropInsightEventModel,
    DropInsightEvidenceModel,
    DropInsightFeedbackModel,
    DropInsightHypothesisModel,
    DropInsightReportModel,
    DropInsightSessionModel,
    DropInsightToolCallModel,
    FixVerificationModel,
    TaskAttemptModel,
    TaskModel,
)
from server.app.state_machine import now_utc

from .evidence import EvidenceEnvelope, calibrate_confidence, classify_evidence
from .artifact_evidence import assess_artifact_evidence
from .claim_verifier import verify_report_claims
from .policy import PolicyContext, evaluate_tool_call
from server.app.artifact_contracts import CONTRACT_VERSION
from .schemas import (
    AddEvidenceRequest,
    ClarifyDiagnosisRequest,
    CreateDiagnosisRequestV2,
    CreateHypothesisRequest,
    CreateToolCallRequest,
    DecideToolCallRequest,
    GenerateReportRequest,
    ImportTaskEvidenceRequest,
    PreviewToolCallRequest,
    RunPlannerRequest,
    SubmitDiagnosisFeedbackRequest,
)
from server.app.schemas import CreateTaskRequest
from server.app.sql_repository import SqlRepository
from server.app.prometheus_metrics import record_evidence_decision
from server.app.diagnosis.source_mapper import map_hot_functions
from .adaptive_planner import propose_hypothesis_plan


def _scope_questions(target: dict | None, time_range: dict | None) -> list[dict]:
    """Return the concrete scope gaps that would block a real collection."""

    target = target or {}
    questions = []
    fields = (
        ("target.service", "service", "要诊断哪个服务？"),
        ("target.environment", "environment", "目标属于哪个环境？"),
        ("target.agent_id", "agent_id", "由哪个在线 Agent 采集？"),
        ("target.pid", "pid", "要诊断该 Agent 上的哪个进程 PID？"),
    )
    for question_id, key, prompt in fields:
        if not target.get(key):
            questions.append({"question_id": question_id, "prompt": prompt})
    if not (time_range or {}).get("start") or not (time_range or {}).get("end"):
        questions.append({"question_id": "time_range", "prompt": "故障发生在哪个时间范围？"})
    return questions


def create_diagnosis(payload: CreateDiagnosisRequestV2) -> DropInsightSessionModel:
    target_json = payload.target.model_dump(mode="json")
    time_range_json = payload.time_range.model_dump(mode="json") if payload.time_range else {}
    questions = _scope_questions(target_json, time_range_json)

    status = "NEEDS_CLARIFICATION" if questions else "UNDERSTANDING"
    timestamp = now_utc()
    diagnosis_id = f"insight_{uuid4().hex}"
    model = DropInsightSessionModel(
        id=diagnosis_id,
        query=payload.query,
        target_json=target_json,
        time_range_json=time_range_json,
        mode=payload.mode,
        budget_json=payload.budget.model_dump(mode="json"),
        status=status,
        version=1,
        clarification_questions_json=questions,
        created_at=timestamp,
        updated_at=timestamp,
    )
    event = DropInsightEventModel(
        id=f"event_{uuid4().hex}",
        diagnosis_id=diagnosis_id,
        sequence=1,
        event_type="diagnosis.created",
        actor="USER",
        payload_json={"status": status},
        occurred_at=timestamp,
    )
    session = new_session()
    try:
        session.add(model)
        session.flush()
        session.add(event)
        session.commit()
        session.refresh(model)
        return model
    finally:
        session.close()


def get_diagnosis(diagnosis_id: str) -> DropInsightSessionModel | None:
    session = new_session()
    try:
        return session.get(DropInsightSessionModel, diagnosis_id)
    finally:
        session.close()


def list_diagnoses() -> list[DropInsightSessionModel]:
    session = new_session()
    try:
        return (
            session.query(DropInsightSessionModel)
            .filter(DropInsightSessionModel.deleted_at.is_(None))
            .order_by(DropInsightSessionModel.created_at.desc())
            .all()
        )
    finally:
        session.close()


def delete_diagnosis(
    diagnosis_id: str,
    *,
    deleted_by: str = "web",
    reason: str = "用户在 AI 诊断会话历史中归档",
) -> DropInsightSessionModel | None:
    """软归档一个诊断会话：隐藏但保留证据与审计（同任务归档策略）。

    已删除的会话返回 None；重复删除幂等（按 ID 置删除时间戳即可）。
    """
    timestamp = now_utc()
    session = new_session()
    try:
        model = session.get(DropInsightSessionModel, diagnosis_id)
        if model is None:
            return None
        model.deleted_at = timestamp
        model.deleted_by = deleted_by
        model.delete_reason = reason
        model.updated_at = timestamp
        session.commit()
        session.refresh(model)
        return model
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def list_events(diagnosis_id: str) -> list[DropInsightEventModel]:
    session = new_session()
    try:
        return (
            session.query(DropInsightEventModel)
            .filter(DropInsightEventModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightEventModel.sequence.asc())
            .all()
        )
    finally:
        session.close()


def create_hypothesis(
    diagnosis_id: str,
    payload: CreateHypothesisRequest,
    *,
    source: str = "USER",
    round_index: int = 1,
    parent_hypothesis_id: str | None = None,
    generation_reason: str = "",
) -> DropInsightHypothesisModel | None:
    session = new_session()
    try:
        diagnosis = _lock_diagnosis(
            session, diagnosis_id, payload.expected_version
        )
        if diagnosis is None:
            return None
        timestamp = now_utc()
        model = DropInsightHypothesisModel(
            id=f"hyp_{uuid4().hex}",
            diagnosis_id=diagnosis_id,
            statement=payload.statement,
            expected_observations_json=payload.expected_observations,
            falsification_criteria_json=payload.falsification_criteria,
            status="OPEN",
            source=source,
            round_index=round_index,
            parent_hypothesis_id=parent_hypothesis_id,
            generation_reason=generation_reason,
            created_at=timestamp,
            updated_at=timestamp,
        )
        _append_event(
            session,
            diagnosis_id,
            "hypothesis.created",
            "SYSTEM",
            {
                "hypothesis_id": model.id,
                "source": source,
                "round_index": round_index,
                "parent_hypothesis_id": parent_hypothesis_id,
            },
            timestamp,
        )
        _cas_session_update(session, diagnosis, status="HYPOTHESIZING", timestamp=timestamp)
        session.add(model)
        session.commit()
        session.refresh(model)
        return model
    finally:
        session.close()


def list_hypotheses(diagnosis_id: str) -> list[DropInsightHypothesisModel]:
    session = new_session()
    try:
        return (
            session.query(DropInsightHypothesisModel)
            .filter(DropInsightHypothesisModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightHypothesisModel.created_at.asc())
            .all()
        )
    finally:
        session.close()


def add_evidence(
    diagnosis_id: str,
    payload: AddEvidenceRequest,
) -> DropInsightEvidenceModel | None:
    session = new_session()
    try:
        diagnosis = _lock_diagnosis(
            session, diagnosis_id, payload.expected_version
        )
        if diagnosis is None:
            return None
        if payload.hypothesis_id:
            hypothesis = session.get(DropInsightHypothesisModel, payload.hypothesis_id)
            if hypothesis is None or hypothesis.diagnosis_id != diagnosis_id:
                raise ValueError("hypothesis does not belong to diagnosis")
        # This endpoint is intentionally restricted to untrusted contextual
        # material.  A caller cannot manufacture Task/Artifact provenance or
        # self-assert HIGH quality.  Traceable production evidence must use
        # import_task_evidence().
        timestamp = now_utc()
        target = diagnosis.target_json or {}
        requested_range = diagnosis.time_range_json or {}
        range_end = _parse_datetime(requested_range.get("end")) or timestamp
        range_start = (
            _parse_datetime(requested_range.get("start"))
            or range_end - timedelta(microseconds=1)
        )
        envelope = EvidenceEnvelope(
            evidence_id=payload.evidence_id,
            diagnosis_id=diagnosis_id,
            evidence_type=f"UNVERIFIED_EXTERNAL:{payload.evidence_type}",
            source={
                "tool_name": payload.source_label,
                "task_id": "",
                "task_attempt_id": "",
                "artifact_id": "",
                "artifact_sha256": "",
                "analysis_job_id": "",
                "analyzer_type": "",
                "analyzer_version": "unverified-external",
                "analyzer_output_schema_version": "",
                "observation_json_pointer": "/",
            },
            scope={
                "agent_id": target.get("agent_id") or "unverified",
                "service": target.get("service"),
                "host_id": target.get("host_id"),
                "instance_id": target.get("instance_id"),
                "container_id": target.get("container_id"),
                "pid": target.get("pid"),
            },
            time_range={
                "start": range_start,
                "end": range_end,
                "timezone": requested_range.get("timezone", "Asia/Shanghai"),
            },
            observation=payload.observation,
            quality={
                "level": "LOW",
                "sample_count": 0,
                "sample_count_known": False,
                "degraded": True,
                "target_match": False,
                "time_overlap": False,
                "schema_valid": False,
                "analyzer_validated": False,
                "minimum_samples": None,
            },
            limitations=[
                "UNVERIFIED_EXTERNAL：该信息未绑定平台 Task、TaskAttempt、Artifact 和 Analyzer Job",
                *payload.limitations,
            ],
        )
        classification = classify_evidence(envelope)
        model = DropInsightEvidenceModel(
            id=payload.evidence_id,
            diagnosis_id=diagnosis_id,
            hypothesis_id=payload.hypothesis_id,
            role="UNVERIFIED_EXTERNAL",
            envelope_json=envelope.model_dump(mode="json"),
            classification_json=classification,
            created_at=timestamp,
        )
        _append_event(
            session,
            diagnosis_id,
            "evidence.added",
            "ANALYZER",
            {
                "evidence_id": model.id,
                "role": model.role,
                "decision": classification["decision"],
                "trusted_provenance": False,
            },
            timestamp,
        )
        _cas_session_update(
            session, diagnosis, status="COLLECTING_EVIDENCE", timestamp=timestamp
        )
        session.add(model)
        session.commit()
        session.refresh(model)
        return model
    finally:
        session.close()


def list_evidence(diagnosis_id: str) -> list[DropInsightEvidenceModel]:
    session = new_session()
    try:
        return (
            session.query(DropInsightEvidenceModel)
            .filter(DropInsightEvidenceModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightEvidenceModel.created_at.asc())
            .all()
        )
    finally:
        session.close()


def generate_report(
    diagnosis_id: str,
    payload: GenerateReportRequest,
) -> DropInsightReportModel | None:
    session = new_session()
    try:
        diagnosis = _lock_diagnosis(
            session, diagnosis_id, payload.expected_version
        )
        if diagnosis is None:
            return None
        hypothesis = session.get(DropInsightHypothesisModel, payload.hypothesis_id)
        if hypothesis is None or hypothesis.diagnosis_id != diagnosis_id:
            raise ValueError("hypothesis does not belong to diagnosis")

        rows = (
            session.query(DropInsightEvidenceModel)
            .filter(
                DropInsightEvidenceModel.diagnosis_id == diagnosis_id,
                DropInsightEvidenceModel.hypothesis_id == payload.hypothesis_id,
            )
            .all()
        )
        supporting = [
            EvidenceEnvelope.model_validate(row.envelope_json)
            for row in rows
            if row.role == "SUPPORT"
        ]
        counter = [
            EvidenceEnvelope.model_validate(row.envelope_json)
            for row in rows
            if row.role == "COUNTER"
        ]
        verification = verify_report_claims(
            [
                (row.role, EvidenceEnvelope.model_validate(row.envelope_json))
                for row in rows
            ],
            expected_observations=hypothesis.expected_observations_json or [],
            falsification_criteria=hypothesis.falsification_criteria_json or [],
        )
        coverage_ratio = verification["coverage_ratio"]
        confidence = calibrate_confidence(supporting, counter, coverage_ratio)
        support_refs = sorted({
            item["evidence_id"]
            for item in verification["claims"]
            if item["direction"] == "SUPPORT"
        })
        counter_refs = sorted({
            item["evidence_id"]
            for item in verification["claims"]
            if item["direction"] == "COUNTER"
        })
        limitations = sorted(
            {
                reason
                for item in supporting + counter
                for reason in classify_evidence(item)["reasons"]
            }
        )
        if not support_refs:
            limitations.append("No accepted supporting evidence; conclusion is not established.")
        elif not verification["has_independent_counter_or_control"]:
            limitations.append(
                "缺少独立反证或对照证据；报告可作为阶段性判断，但诊断不会进入最终完成态。"
            )

        if support_refs and verification["has_independent_counter_or_control"]:
            verification["status"] = "VERIFIED"
        elif support_refs:
            verification["status"] = "PARTIAL_WITHOUT_COUNTER"

        source_symbols = _extract_source_symbols(supporting + counter)
        verification["source_context"] = map_hot_functions(source_symbols)

        conclusion = _derive_report_conclusion(
            hypothesis.statement,
            support_refs=support_refs,
            counter_refs=counter_refs,
        )
        assumptions = ["结论仅适用于当前诊断目标与时间窗口"]
        next_actions = _derive_next_actions(
            support_refs=support_refs,
            counter_refs=counter_refs,
        )

        timestamp = now_utc()
        report = DropInsightReportModel(
            id=f"report_{uuid4().hex}",
            diagnosis_id=diagnosis_id,
            hypothesis_id=payload.hypothesis_id,
            conclusion=conclusion,
            confidence=round(confidence * 1000),
            evidence_refs_json=support_refs,
            counter_evidence_refs_json=counter_refs,
            assumptions_json=assumptions,
            limitations_json=limitations,
            next_actions_json=next_actions,
            claims_json=verification["claims"],
            verification_json=verification,
            created_at=timestamp,
        )
        if counter_refs and not support_refs:
            hypothesis.status = "COUNTER"
        elif confidence >= 0.6 and verification["status"] == "VERIFIED":
            hypothesis.status = "SUPPORTED"
        else:
            hypothesis.status = "INCONCLUSIVE"
        hypothesis.updated_at = timestamp
        next_status = (
            "COMPLETED"
            if verification["status"] == "VERIFIED"
            else "COLLECTING_EVIDENCE"
            if support_refs
            else "INSUFFICIENT_EVIDENCE"
        )
        _cas_session_update(session, diagnosis, status=next_status, timestamp=timestamp)
        _append_event(
            session,
            diagnosis_id,
            "report.generated",
            "SYSTEM",
            {
                "report_id": report.id,
                "confidence": confidence,
                "coverage_ratio": coverage_ratio,
                "evidence_refs": support_refs,
                "verification_status": verification["status"],
                "verified_claim_count": len(verification["claims"]),
                "generation_mode": "SERVER_EVIDENCE_RULES_V1",
            },
            timestamp,
        )
        session.add(report)
        session.commit()
        session.refresh(report)
        if hypothesis.status == "COUNTER":
            _replan_from_counter_evidence(diagnosis_id, hypothesis.id)
        elif next_status == "INSUFFICIENT_EVIDENCE":
            _replan_after_insufficient_evidence(diagnosis_id, hypothesis.id)
        elif next_status == "COMPLETED":
            _record_successful_route(diagnosis_id, report.id)
        return report
    finally:
        session.close()


def list_reports(diagnosis_id: str) -> list[DropInsightReportModel]:
    session = new_session()
    try:
        return (
            session.query(DropInsightReportModel)
            .filter(DropInsightReportModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightReportModel.created_at.desc())
            .all()
        )
    finally:
        session.close()


def _extract_source_symbols(evidence_rows: list) -> list[str]:
    """Extract a bounded set of sampled function names from trusted evidence.

    This deliberately ignores free-form prose. Only symbol-shaped values under
    known profiling keys are considered, preventing a model or user message
    from turning arbitrary text into a source-tree search.
    """

    symbol_keys = {
        "function", "function_name", "symbol", "frame", "top_function",
        "hot_function", "hot_functions", "top_functions",
    }
    symbols: list[str] = []

    def walk(value, key: str = "", depth: int = 0) -> None:
        if depth > 7 or len(symbols) >= 20:
            return
        if isinstance(value, dict):
            for child_key, child in value.items():
                walk(child, str(child_key).lower(), depth + 1)
        elif isinstance(value, list):
            for child in value[:100]:
                walk(child, key, depth + 1)
        elif key in symbol_keys and isinstance(value, str):
            candidate = value.strip()
            if 1 < len(candidate) <= 256 and candidate not in symbols:
                symbols.append(candidate)

    for row in evidence_rows:
        if hasattr(row, "envelope_json"):
            payload = row.envelope_json or {}
        elif hasattr(row, "model_dump"):
            payload = row.model_dump(mode="json")
        elif isinstance(row, dict):
            payload = row
        else:
            payload = {}
        walk(payload)
    return symbols


def list_feedback(diagnosis_id: str) -> list[DropInsightFeedbackModel]:
    session = new_session()
    try:
        return (
            session.query(DropInsightFeedbackModel)
            .filter(DropInsightFeedbackModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightFeedbackModel.created_at.desc())
            .all()
        )
    finally:
        session.close()


def submit_diagnosis_feedback(
    diagnosis_id: str,
    payload: SubmitDiagnosisFeedbackRequest,
    *,
    created_by: str,
) -> DropInsightFeedbackModel | None:
    """Persist human correction and start a new diagnostic round when needed."""
    session = new_session()
    try:
        diagnosis = _lock_diagnosis(session, diagnosis_id)
        if diagnosis is None:
            return None
        report = session.get(DropInsightReportModel, payload.report_id) if payload.report_id else (
            session.query(DropInsightReportModel)
            .filter(DropInsightReportModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightReportModel.created_at.desc())
            .first()
        )
        if report is not None and report.diagnosis_id != diagnosis_id:
            raise ValueError("report does not belong to diagnosis")
        hypothesis_id = payload.hypothesis_id or (report.hypothesis_id if report else None)
        parent = session.get(DropInsightHypothesisModel, hypothesis_id) if hypothesis_id else None
        if parent is not None and parent.diagnosis_id != diagnosis_id:
            raise ValueError("hypothesis does not belong to diagnosis")
        timestamp = now_utc()
        feedback = DropInsightFeedbackModel(
            id=f"feedback_{uuid4().hex}",
            diagnosis_id=diagnosis_id,
            report_id=report.id if report else None,
            hypothesis_id=parent.id if parent else None,
            feedback_label=payload.feedback_label,
            predicted_conclusion=report.conclusion if report else "",
            corrected_cause=(payload.corrected_cause or "").strip() or None,
            feedback_note=(payload.feedback_note or "").strip() or None,
            requested_replan=bool(
                payload.request_replan and payload.feedback_label in {"partial", "wrong"}
            ),
            created_by=created_by,
            created_at=timestamp,
        )
        if parent is not None:
            parent.status = (
                "SUPPORTED" if payload.feedback_label == "correct"
                else "COUNTER" if payload.feedback_label == "wrong"
                else "INCONCLUSIVE"
            )
            parent.updated_at = timestamp
        session.add(feedback)
        _append_event(
            session, diagnosis_id, "feedback.submitted", "USER",
            {
                "feedback_id": feedback.id,
                "label": payload.feedback_label,
                "report_id": feedback.report_id,
                "hypothesis_id": feedback.hypothesis_id,
                "request_replan": feedback.requested_replan,
            }, timestamp,
        )
        _cas_session_update(
            session,
            diagnosis,
            status="HYPOTHESIZING" if feedback.requested_replan else diagnosis.status,
            timestamp=timestamp,
        )
        session.commit()
        session.refresh(feedback)
    finally:
        session.close()

    if feedback.requested_replan:
        revision = _replan_from_feedback(diagnosis_id, feedback)
        session = new_session()
        try:
            persisted = session.get(DropInsightFeedbackModel, feedback.id)
            if persisted is not None:
                persisted.revision_hypothesis_id = revision.id if revision else None
                session.commit()
                session.refresh(persisted)
                feedback = persisted
        finally:
            session.close()
    return feedback


def _replan_from_feedback(
    diagnosis_id: str,
    feedback: DropInsightFeedbackModel,
) -> DropInsightHypothesisModel | None:
    diagnosis = get_diagnosis(diagnosis_id)
    if diagnosis is None:
        return None
    target = diagnosis.target_json or {}
    previous = list_hypotheses(diagnosis_id)
    parent = next((item for item in previous if item.id == feedback.hypothesis_id), None)
    round_index = max([item.round_index or 1 for item in previous] or [1]) + 1
    correction = feedback.corrected_cause or feedback.feedback_note or "用户认为上一轮结论不完整"
    baseline = {
        "statement": correction,
        "expected": ["新采集证据与用户纠正的原因在同一目标和时间窗内一致"],
        "falsification": ["补充证据与该纠正原因不一致或出现更强反证"],
        "tool_name": _feedback_tool(correction, parent),
    }
    proposal = propose_hypothesis_plan(
        query=diagnosis.query,
        target=target,
        category="HUMAN_CORRECTION",
        rule_plan=baseline,
        prior_hypotheses=[
            {"statement": item.statement, "status": item.status, "round": item.round_index}
            for item in previous
        ],
        user_correction=correction,
        allowed_tools=["collect_sys_metrics", "start_perf_profile", "start_ebpf_io_profile", "start_pyspy_profile"],
        route_priors=_successful_tool_route_priors(),
    )
    candidate = (proposal or {}).get("hypotheses", [{}])[0]
    statement = candidate.get("statement") or f"用户纠正后待验证：{correction}"
    expected = candidate.get("expected_observations") or baseline["expected"]
    falsification = candidate.get("falsification_criteria") or baseline["falsification"]
    source = "MODEL_REPLAN" if proposal else "USER_GUIDED_FALLBACK"
    reason = (proposal or {}).get("reasoning_summary") or "根据用户反馈开启新一轮取证，保留上一轮结果用于审计。"
    revision = create_hypothesis(
        diagnosis_id,
        CreateHypothesisRequest(
            statement=statement,
            expected_observations=expected,
            falsification_criteria=falsification,
        ),
        source=source,
        round_index=round_index,
        parent_hypothesis_id=parent.id if parent else None,
        generation_reason=reason,
    )
    if revision is None or not target.get("agent_id") or not target.get("pid"):
        return revision
    tool_name = (proposal or {}).get("tool_name") or baseline["tool_name"]
    request_tool_call(
        diagnosis_id,
        CreateToolCallRequest(
            hypothesis_id=revision.id,
            tool_name=tool_name,
            arguments=_planner_tool_arguments(tool_name, target),
        ),
        requested_by="system:adaptive-replanner",
    )
    return revision


def _feedback_tool(correction: str, parent: DropInsightHypothesisModel | None) -> str:
    text = correction.lower()
    if any(token in text for token in ("io", "磁盘", "写入", "读取")):
        return "start_ebpf_io_profile"
    if any(token in text for token in ("python", "gil", "协程")):
        return "start_pyspy_profile"
    if any(token in text for token in ("cpu", "热点", "函数", "调用栈")):
        return "start_perf_profile"
    if parent and any(token in parent.statement.lower() for token in ("cpu", "热点")):
        return "collect_sys_metrics"
    return "collect_sys_metrics"


def _planner_tool_arguments(tool_name: str, target: dict) -> dict:
    arguments = {
        "agent_id": target["agent_id"],
        "pid": target["pid"],
        "duration_seconds": 15,
    }
    if tool_name in {"start_perf_profile", "start_pyspy_profile"}:
        arguments["sample_rate"] = 99
    return arguments


def _replan_from_counter_evidence(
    diagnosis_id: str,
    parent_hypothesis_id: str,
) -> DropInsightHypothesisModel | None:
    """Open a bounded new round when trusted evidence falsifies the primary hypothesis."""
    diagnosis = get_diagnosis(diagnosis_id)
    if diagnosis is None:
        return None
    previous = list_hypotheses(diagnosis_id)
    parent = next((item for item in previous if item.id == parent_hypothesis_id), None)
    round_index = max([item.round_index or 1 for item in previous] or [1]) + 1
    if parent is None or round_index > 3:
        return None
    # Avoid opening the same round twice when orchestration is retried.
    if any(item.parent_hypothesis_id == parent.id for item in previous):
        return None
    target = diagnosis.target_json or {}
    statement = f"上一轮“{parent.statement}”已被反证，需验证同一时间窗内的替代资源或依赖原因"
    tool_name = "collect_sys_metrics" if any(
        token in parent.statement.lower() for token in ("cpu", "热点", "python", "io")
    ) else "start_perf_profile"
    baseline = {
        "statement": statement,
        "expected": ["补充证据能解释上一轮未覆盖的异常范围"],
        "falsification": ["补充指标平稳且不能解释故障现象"],
        "tool_name": tool_name,
    }
    proposal = propose_hypothesis_plan(
        query=diagnosis.query,
        target=target,
        category="COUNTER_EVIDENCE_REPLAN",
        rule_plan=baseline,
        prior_hypotheses=[item.to_dict() for item in previous],
        user_correction="可信反证已推翻上一轮主假设",
        allowed_tools=["collect_sys_metrics", "start_perf_profile"],
        route_priors=_successful_tool_route_priors(),
    )
    candidate = (proposal or {}).get("hypotheses", [{}])[0]
    revision = create_hypothesis(
        diagnosis_id,
        CreateHypothesisRequest(
            statement=candidate.get("statement") or statement,
            expected_observations=candidate.get("expected_observations") or baseline["expected"],
            falsification_criteria=candidate.get("falsification_criteria") or baseline["falsification"],
        ),
        source="MODEL_REPLAN" if proposal else "COUNTER_EVIDENCE_RULE",
        round_index=round_index,
        parent_hypothesis_id=parent.id,
        generation_reason=(proposal or {}).get("reasoning_summary")
        or "可信反证推翻上一轮主假设，自动进入下一轮互补取证。",
    )
    if revision is not None and target.get("agent_id") and target.get("pid"):
        selected_tool = (proposal or {}).get("tool_name") or tool_name
        request_tool_call(
            diagnosis_id,
            CreateToolCallRequest(
                hypothesis_id=revision.id,
                tool_name=selected_tool,
                arguments=_planner_tool_arguments(selected_tool, target),
            ),
            requested_by="system:counter-evidence-replanner",
        )
    return revision


def _successful_tool_route_priors(limit: int = 20) -> list[dict]:
    """从已验证报告提取成功工具路线，作为弱先验而非硬编码答案。"""
    session = new_session()
    try:
        rows = (
            session.query(DropInsightReportModel)
            .filter(DropInsightReportModel.verification_json.isnot(None))
            .order_by(DropInsightReportModel.created_at.desc())
            .limit(limit)
            .all()
        )
        priors = []
        for report in rows:
            if (report.verification_json or {}).get("status") != "VERIFIED":
                continue
            calls = (
                session.query(DropInsightToolCallModel)
                .filter(DropInsightToolCallModel.diagnosis_id == report.diagnosis_id)
                .order_by(DropInsightToolCallModel.created_at.asc())
                .all()
            )
            route = [item.tool_name for item in calls if item.status == "COMPLETED"]
            if route:
                priors.append({"route": route, "verified": True})
        return priors
    finally:
        session.close()


def _replan_after_insufficient_evidence(
    diagnosis_id: str,
    parent_hypothesis_id: str,
) -> DropInsightHypothesisModel | None:
    """证据不足时自动换证据域，而不是立即把诊断交还给用户。"""
    diagnosis = get_diagnosis(diagnosis_id)
    if diagnosis is None:
        return None
    target = diagnosis.target_json or {}
    if not target.get("agent_id") or not target.get("pid"):
        return None
    capability_by_tool = {
        "collect_sys_metrics": "sys_metrics",
        "start_perf_profile": "perf_cpu",
        "start_ebpf_io_profile": "ebpf_io",
        "start_pyspy_profile": "pyspy",
    }
    session = new_session()
    try:
        agent = session.get(AgentModel, target["agent_id"])
        if agent is None or agent.status != "ONLINE":
            return None
        capabilities = set(agent.capabilities or [])
    finally:
        session.close()
    previous = list_hypotheses(diagnosis_id)
    parent = next((item for item in previous if item.id == parent_hypothesis_id), None)
    round_index = max([item.round_index or 1 for item in previous] or [1]) + 1
    if parent is None or round_index > 3:
        return None
    if any(item.parent_hypothesis_id == parent.id for item in previous):
        return None
    attempted = {item.tool_name for item in list_tool_calls(diagnosis_id)}
    all_tools = [
        "collect_sys_metrics", "start_perf_profile", "start_ebpf_io_profile", "start_pyspy_profile",
    ]
    remaining = [
        item for item in all_tools
        if item not in attempted and capability_by_tool[item] in capabilities
    ]
    if not remaining:
        return None
    fallback_tool = remaining[0]
    baseline = {
        "statement": f"上一轮“{parent.statement}”证据不足，需切换证据域继续定位",
        "expected": ["新的独立采集结果能够支持或推翻至少一个候选假设"],
        "falsification": ["补充证据仍无区分力，或目标能力不支持该采集器"],
        "tool_name": fallback_tool,
    }
    proposal = propose_hypothesis_plan(
        query=diagnosis.query,
        target=target,
        category="INSUFFICIENT_EVIDENCE_REPLAN",
        rule_plan=baseline,
        prior_hypotheses=[item.to_dict() for item in previous],
        evidence_summary=[{"result": "insufficient_evidence", "attempted_tools": sorted(attempted)}],
        allowed_tools=remaining,
        route_priors=_successful_tool_route_priors(),
    )
    candidate = (proposal or {}).get("hypotheses", [{}])[0]
    reason = (proposal or {}).get("reasoning_summary") or (
        "上一证据域不足以建立结论，按剩余注册工具和历史成功路线切换取证方向。"
    )
    revision = create_hypothesis(
        diagnosis_id,
        CreateHypothesisRequest(
            statement=candidate.get("statement") or baseline["statement"],
            expected_observations=candidate.get("expected_observations") or baseline["expected"],
            falsification_criteria=candidate.get("falsification_criteria") or baseline["falsification"],
        ),
        source="MODEL_REPLAN" if proposal else "AUTONOMOUS_RULE_FALLBACK",
        round_index=round_index,
        parent_hypothesis_id=parent.id,
        generation_reason=reason,
    )
    selected_tool = (proposal or {}).get("tool_name") or fallback_tool
    call = request_tool_call(
        diagnosis_id,
        CreateToolCallRequest(
            hypothesis_id=revision.id,
            tool_name=selected_tool,
            arguments=_planner_tool_arguments(selected_tool, target),
        ),
        requested_by="system:insufficient-evidence-replanner",
    )
    session = new_session()
    try:
        timestamp = now_utc()
        _append_event(session, diagnosis_id, "planner.insufficient_replanned", "SYSTEM", {
            "round_index": round_index,
            "previous_hypothesis_id": parent.id,
            "hypothesis_id": revision.id,
            "tool_name": selected_tool,
            "planner_kind": "MODEL_ASSISTED" if proposal else "DETERMINISTIC_FALLBACK",
            "reason": reason,
            "requires_approval": call.policy_decision == "REQUIRE_APPROVAL",
        }, timestamp)
        persisted = _lock_diagnosis(session, diagnosis_id)
        if persisted is not None:
            _cas_session_update(session, persisted, status="HYPOTHESIZING", timestamp=timestamp)
        session.commit()
    finally:
        session.close()
    return revision


def _record_successful_route(diagnosis_id: str, report_id: str) -> None:
    session = new_session()
    try:
        calls = (
            session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightToolCallModel.created_at.asc())
            .all()
        )
        route = [item.tool_name for item in calls if item.status == "COMPLETED"]
        if route:
            _append_event(session, diagnosis_id, "diagnosis.route_learned", "SYSTEM", {
                "report_id": report_id,
                "tool_route": route,
                "learning_scope": "verified_route_prior",
                "note": "仅提升后续路线排序，不自动新增工具或绕过策略。",
            }, now_utc())
            session.commit()
    finally:
        session.close()


def preview_tool_call(
    diagnosis_id: str,
    payload: PreviewToolCallRequest,
) -> dict | None:
    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if diagnosis is None:
            return None
        target = diagnosis.target_json or {}
        allowed_agent_ids = {
            value
            for value in [target.get("agent_id")]
            if isinstance(value, str) and value
        }
        agent_id = payload.arguments.get("agent_id")
        agent = session.get(AgentModel, agent_id) if isinstance(agent_id, str) else None
        capabilities = frozenset(agent.capabilities or []) if agent is not None else frozenset()
        budget = diagnosis.budget_json or {}
        used_tool_calls = (
            session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.diagnosis_id == diagnosis_id)
            .count()
        )
        context = PolicyContext(
            allowed_agent_ids=frozenset(allowed_agent_ids),
            agent_capabilities=capabilities,
            max_risk_level=budget.get("max_risk_level", "R0"),
            used_tool_calls=used_tool_calls,
            max_tool_calls=budget.get("max_tool_calls", 12),
        )
        decision = evaluate_tool_call(payload.tool_name, payload.arguments, context)
        _append_event(
            session,
            diagnosis_id,
            "tool_call.policy_evaluated",
            "POLICY",
            {
                "tool_name": payload.tool_name,
                "decision": decision["decision"],
                "checks": decision["checks"],
            },
            now_utc(),
        )
        session.commit()
        return decision
    finally:
        session.close()


def request_tool_call(
    diagnosis_id: str,
    payload: CreateToolCallRequest,
    *,
    requested_by: str = "system:internal",
) -> DropInsightToolCallModel | None:
    session = new_session()
    try:
        diagnosis = _lock_diagnosis(
            session, diagnosis_id, payload.expected_version
        )
        if diagnosis is None:
            return None
        if payload.hypothesis_id:
            hypothesis = session.get(DropInsightHypothesisModel, payload.hypothesis_id)
            if hypothesis is None or hypothesis.diagnosis_id != diagnosis_id:
                raise ValueError("hypothesis does not belong to diagnosis")

        decision = _evaluate_persisted_tool_policy(session, diagnosis, payload.tool_name, payload.arguments)
        reservation = decision.pop("reservation", None)
        status_by_decision = {
            "DENY": "DENIED",
            "REQUIRE_APPROVAL": "PENDING_APPROVAL",
            "ALLOW": "APPROVED",
        }
        timestamp = now_utc()
        model = DropInsightToolCallModel(
            id=f"toolcall_{uuid4().hex}",
            diagnosis_id=diagnosis_id,
            hypothesis_id=payload.hypothesis_id,
            tool_name=payload.tool_name,
            arguments_json=payload.arguments,
            policy_decision=decision["decision"],
            policy_checks_json=decision["checks"],
            policy_reason=decision["reason"],
            status=status_by_decision[decision["decision"]],
            budget_reservation_json=reservation or {},
            budget_reservation_status="RESERVED" if reservation else "NONE",
            requested_by=requested_by,
            created_at=timestamp,
            decided_at=timestamp if decision["decision"] == "DENY" else None,
        )
        session.add(model)
        _append_event(
            session,
            diagnosis_id,
            "tool_call.requested",
            "PLANNER",
            {
                "tool_call_id": model.id,
                "tool_name": model.tool_name,
                "policy_decision": model.policy_decision,
                "status": model.status,
            },
            timestamp,
        )
        _cas_session_update(
            session,
            diagnosis,
            status="PLANNING" if diagnosis.status == "UNDERSTANDING" else diagnosis.status,
            timestamp=timestamp,
        )
        session.commit()
        session.refresh(model)
    finally:
        session.close()

    if model.status == "APPROVED":
        return _execute_approved_tool_call(model.id)
    return model


def decide_tool_call(
    diagnosis_id: str,
    tool_call_id: str,
    payload: DecideToolCallRequest,
    *,
    decided_by: str = "system:internal",
) -> DropInsightToolCallModel | None:
    session = new_session()
    try:
        model = session.get(DropInsightToolCallModel, tool_call_id)
        if model is None or model.diagnosis_id != diagnosis_id:
            return None
        if model.status != "PENDING_APPROVAL":
            raise ValueError(f"tool call is not awaiting approval: {model.status}")
        timestamp = now_utc()
        model.approved_by = decided_by
        model.approval_reason = payload.reason
        model.decided_at = timestamp
        model.status = "APPROVED" if payload.approved else "REJECTED"
        if not payload.approved:
            _release_budget_reservation(model, timestamp=timestamp, reason="approval_rejected")
        _append_event(
            session,
            diagnosis_id,
            "tool_call.approval_decided",
            "USER",
            {
                "tool_call_id": model.id,
                "approved": payload.approved,
                "decided_by": decided_by,
            },
            timestamp,
        )
        session.commit()
        session.refresh(model)
    finally:
        session.close()

    if payload.approved:
        return _execute_approved_tool_call(tool_call_id)
    return model


def update_tool_call_arguments(
    diagnosis_id: str,
    tool_call_id: str,
    *,
    arguments: dict,
    updated_by: str = "user",
) -> DropInsightToolCallModel | None:
    """修改待审批工具调用的参数；修改后重新做策略与预算校验。

    仅 ``PENDING_APPROVAL`` 状态可修改。若新参数被策略 DENY，则标记为
    DENIED 并释放预算预留；否则保持待审批，等待用户显式批准。
    """
    session = new_session()
    try:
        model = session.get(DropInsightToolCallModel, tool_call_id)
        if model is None or model.diagnosis_id != diagnosis_id:
            return None
        if model.status != "PENDING_APPROVAL":
            raise ValueError(
                f"只有待审批的工具调用可以修改参数（当前状态 {model.status}）"
            )
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if diagnosis is None:
            return None
        timestamp = now_utc()
        model.arguments_json = arguments
        decision = _evaluate_persisted_tool_policy(
            session, diagnosis, model.tool_name, arguments
        )
        reservation = decision.pop("reservation", None)
        model.policy_decision = decision["decision"]
        model.policy_checks_json = decision["checks"]
        model.policy_reason = decision["reason"]
        if decision["decision"] == "DENY":
            model.status = "DENIED"
            _release_budget_reservation(
                model, timestamp=timestamp, reason="arguments_rejected"
            )
        else:
            model.budget_reservation_json = reservation or {}
            model.budget_reservation_status = "RESERVED" if reservation else "NONE"
        _append_event(
            session,
            diagnosis_id,
            "tool_call.arguments_updated",
            updated_by,
            {
                "tool_call_id": model.id,
                "tool_name": model.tool_name,
                "policy_decision": model.policy_decision,
            },
            timestamp,
        )
        session.commit()
        session.refresh(model)
        return model
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def list_tool_calls(diagnosis_id: str) -> list[DropInsightToolCallModel]:
    session = new_session()
    try:
        return (
            session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightToolCallModel.created_at.asc())
            .all()
        )
    finally:
        session.close()


def get_budget_usage(diagnosis_id: str) -> dict | None:
    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if diagnosis is None:
            return None
        calls = (
            session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.diagnosis_id == diagnosis_id)
            .all()
        )
        task_ids = [item.task_id for item in calls if item.task_id]
        tasks = (
            session.query(TaskModel).filter(TaskModel.id.in_(task_ids)).all()
            if task_ids else []
        )
        artifact_bytes = 0
        if task_ids:
            artifact_bytes = sum(
                item.size_bytes or 0
                for item in session.query(ArtifactModel)
                .filter(ArtifactModel.task_id.in_(task_ids))
                .all()
            )
        limits = diagnosis.budget_json or {}
        used = {
            "tool_calls": len(calls),
            "task_duration_seconds": sum(item.duration_sec or 0 for item in tasks),
            "artifact_bytes": artifact_bytes,
            "hosts": len(
                {
                    (item.arguments_json or {}).get("agent_id")
                    for item in calls
                    if (item.arguments_json or {}).get("agent_id")
                }
            ),
        }
        reserved_calls = [
            item for item in calls
            if item.budget_reservation_status == "RESERVED"
        ]
        reserved = {
            "duration_seconds": sum(
                int((item.budget_reservation_json or {}).get("duration_seconds") or 0)
                for item in reserved_calls
            ),
            "artifact_bytes": sum(
                int((item.budget_reservation_json or {}).get("artifact_bytes") or 0)
                for item in reserved_calls
            ),
            "concurrent_tasks": len(reserved_calls),
            "hosts": len({
                (item.budget_reservation_json or {}).get("agent_id")
                for item in reserved_calls
                if (item.budget_reservation_json or {}).get("agent_id")
            }),
        }
        return {
            "limits": limits,
            "used": used,
            "reserved": reserved,
            "remaining": {
                "tool_calls": max(0, limits.get("max_tool_calls", 12) - used["tool_calls"]),
                "duration_seconds": max(
                    0,
                    limits.get("max_duration_seconds", 300) - used["task_duration_seconds"],
                ),
                "artifact_bytes": max(
                    0,
                    limits.get("max_artifact_bytes", 524_288_000) - used["artifact_bytes"],
                ),
                "hosts": max(0, limits.get("max_hosts", 5) - used["hosts"]),
            },
        }
    finally:
        session.close()


def advance_diagnosis(diagnosis_id: str) -> dict | None:
    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if diagnosis is None:
            return None
        calls = (
            session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightToolCallModel.created_at.asc())
            .all()
        )
        snapshots = [
            {
                "tool_call_id": item.id,
                "hypothesis_id": item.hypothesis_id,
                "status": item.status,
                "task_id": item.task_id,
            }
            for item in calls
        ]
    finally:
        session.close()

    actions = []
    for snapshot in snapshots:
        if not snapshot["task_id"]:
            continue
        session = new_session()
        try:
            task = session.get(TaskModel, snapshot["task_id"])
            tool_call = session.get(DropInsightToolCallModel, snapshot["tool_call_id"])
            if task is None or tool_call is None:
                continue
            task_status = task.status
            if task_status in {"PENDING", "RUNNING", "UPLOADING", "ANALYZING"}:
                next_status = "RUNNING" if task_status != "PENDING" else "TASK_CREATED"
                if tool_call.status != next_status:
                    tool_call.status = next_status
                    session.commit()
                actions.append({
                    "tool_call_id": tool_call.id,
                    "task_id": task.id,
                    "action": "WAIT",
                    "task_status": task_status,
                })
                continue
            if task_status in {"FAILED", "CANCELLED"}:
                tool_call.status = task_status
                tool_call.result_json = {
                    "task_status": task_status,
                    "reason": task.status_reason,
                }
                timestamp = now_utc()
                _release_budget_reservation(
                    tool_call,
                    timestamp=timestamp,
                    reason=f"task_{task_status.lower()}",
                )
                _append_event(
                    session,
                    diagnosis_id,
                    "tool_call.task_terminal",
                    "SYSTEM",
                    {
                        "tool_call_id": tool_call.id,
                        "task_id": task.id,
                        "task_status": task_status,
                    },
                    timestamp,
                )
                diagnosis = _lock_diagnosis(session, diagnosis_id)
                _cas_session_update(
                    session,
                    diagnosis,
                    status="INSUFFICIENT_EVIDENCE",
                    timestamp=timestamp,
                )
                session.commit()
                actions.append({
                    "tool_call_id": tool_call.id,
                    "task_id": task.id,
                    "action": task_status,
                })
                continue
            if task_status != "DONE":
                continue
            tool_call.status = "COMPLETED"
            tool_call.result_json = {"task_status": "DONE", "task_id": task.id}
            timestamp = now_utc()
            _settle_budget_reservation(session, tool_call, task, timestamp=timestamp)
            _append_event(
                session,
                diagnosis_id,
                "tool_call.task_terminal",
                "SYSTEM",
                {
                    "tool_call_id": tool_call.id,
                    "task_id": task.id,
                    "task_status": "DONE",
                },
                timestamp,
            )
            session.commit()
        finally:
            session.close()

        if snapshot["hypothesis_id"]:
            imported = import_task_evidence(
                diagnosis_id,
                ImportTaskEvidenceRequest(
                    task_id=snapshot["task_id"],
                    hypothesis_id=snapshot["hypothesis_id"],
                ),
            )
            session = new_session()
            try:
                existing_report = (
                    session.query(DropInsightReportModel)
                    .filter(
                        DropInsightReportModel.diagnosis_id == diagnosis_id,
                        DropInsightReportModel.hypothesis_id == snapshot["hypothesis_id"],
                    )
                    .first()
                )
                hypothesis = session.get(
                    DropInsightHypothesisModel,
                    snapshot["hypothesis_id"],
                )
            finally:
                session.close()
            if existing_report is None and hypothesis is not None:
                report = generate_report(
                    diagnosis_id,
                    GenerateReportRequest(hypothesis_id=hypothesis.id),
                )
            else:
                report = existing_report
            actions.append({
                "tool_call_id": snapshot["tool_call_id"],
                "task_id": snapshot["task_id"],
                "action": "EVIDENCE_IMPORTED",
                "evidence_refs": [item.id for item in imported or []],
                "report_id": report.id if report is not None else None,
            })

    if snapshots:
        # 方案 §5.2：证据到位后给备选假设与 OTHER/UNKNOWN 打分。
        _score_candidate_hypotheses(diagnosis_id)

    return {
        "diagnosis_id": diagnosis_id,
        "actions": actions,
        "budget": get_budget_usage(diagnosis_id),
    }


def _evaluate_persisted_tool_policy(session, diagnosis, tool_name: str, arguments: dict) -> dict:
    target = diagnosis.target_json or {}
    allowed_agent_ids = {
        value for value in [target.get("agent_id")]
        if isinstance(value, str) and value
    }
    agent_id = arguments.get("agent_id")
    agent = session.get(AgentModel, agent_id) if isinstance(agent_id, str) else None
    capabilities = frozenset(agent.capabilities or []) if agent is not None else frozenset()
    budget = diagnosis.budget_json or {}
    used_tool_calls = (
        session.query(DropInsightToolCallModel)
        .filter(DropInsightToolCallModel.diagnosis_id == diagnosis.id)
        .count()
    )
    decision = evaluate_tool_call(
        tool_name,
        arguments,
        PolicyContext(
            allowed_agent_ids=frozenset(allowed_agent_ids),
            agent_capabilities=capabilities,
            max_risk_level=budget.get("max_risk_level", "R0"),
            used_tool_calls=used_tool_calls,
            max_tool_calls=budget.get("max_tool_calls", 12),
        ),
    )
    if decision["decision"] == "DENY":
        return decision
    reservation = _evaluate_resource_budget(
        session, diagnosis, tool_name=tool_name, arguments=arguments
    )
    decision["checks"].extend(reservation["checks"])
    if not reservation["allowed"]:
        return {
            "decision": "DENY",
            "checks": decision["checks"],
            "reason": reservation["reason"],
        }
    decision["reservation"] = reservation["reservation"]
    return decision


_ESTIMATED_ARTIFACT_BYTES = {
    "get_agent_status": 0,
    "collect_sys_metrics": 2 * 1024 * 1024,
    "start_perf_profile": 64 * 1024 * 1024,
    "start_ebpf_io_profile": 16 * 1024 * 1024,
    "start_pyspy_profile": 16 * 1024 * 1024,
}


def _evaluate_resource_budget(
    session,
    diagnosis,
    *,
    tool_name: str,
    arguments: dict,
) -> dict:
    """Reserve bounded resources using persisted ToolCalls under session lock."""

    limits = diagnosis.budget_json or {}
    calls = (
        session.query(DropInsightToolCallModel)
        .filter(DropInsightToolCallModel.diagnosis_id == diagnosis.id)
        .all()
    )
    reserved_calls = [
        item for item in calls
        if item.budget_reservation_status == "RESERVED"
    ]
    settled_calls = [
        item for item in calls
        if item.budget_reservation_status == "SETTLED"
    ]
    requested = _resource_reservation(tool_name, arguments)
    duration = sum(
        int((item.budget_reservation_json or {}).get("duration_seconds") or 0)
        for item in reserved_calls
    ) + sum(
        int((item.budget_settlement_json or {}).get("duration_seconds") or 0)
        for item in settled_calls
    ) + requested["duration_seconds"]
    hosts = {
        payload.get("agent_id")
        for item in reserved_calls + settled_calls
        for payload in [
            item.budget_reservation_json or {}
            if item.budget_reservation_status == "RESERVED"
            else item.budget_settlement_json or {}
        ]
        if payload.get("agent_id")
    }
    if requested["agent_id"]:
        hosts.add(requested["agent_id"])
    concurrent = len(reserved_calls) + 1
    artifact_bytes = sum(
        int((item.budget_reservation_json or {}).get("artifact_bytes") or 0)
        for item in reserved_calls
    ) + sum(
        int((item.budget_settlement_json or {}).get("artifact_bytes") or 0)
        for item in settled_calls
    ) + requested["artifact_bytes"]

    checks = []
    values = (
        ("BUDGET_DURATION", duration, limits.get("max_duration_seconds", 300)),
        ("BUDGET_CONCURRENCY", concurrent, limits.get("max_concurrent_tasks", 3)),
        ("BUDGET_HOSTS", len(hosts), limits.get("max_hosts", 5)),
        ("BUDGET_ARTIFACT_BYTES", artifact_bytes, limits.get("max_artifact_bytes", 524_288_000)),
    )
    failed = []
    for name, reserved, limit in values:
        passed = reserved <= limit
        checks.append({
            "name": name,
            "result": "PASS" if passed else "FAIL",
            "reserved": reserved,
            "limit": limit,
        })
        if not passed:
            failed.append(f"{name} {reserved}>{limit}")
    return {
        "allowed": not failed,
        "checks": checks,
        "reason": "资源预算预留失败: " + ", ".join(failed) if failed else "资源预算已原子预留",
        "reservation": requested,
    }


def _resource_reservation(tool_name: str, arguments: dict) -> dict:
    return {
        "duration_seconds": int(arguments.get("duration_seconds") or 0),
        "artifact_bytes": _ESTIMATED_ARTIFACT_BYTES.get(tool_name, 0),
        "agent_id": arguments.get("agent_id"),
        "concurrent_tasks": 1,
    }


def _release_budget_reservation(model, *, timestamp, reason: str) -> None:
    if model.budget_reservation_status != "RESERVED":
        return
    model.budget_settlement_json = {
        "duration_seconds": 0,
        "artifact_bytes": 0,
        "released_at": timestamp.isoformat(),
        "reason": reason,
    }
    model.budget_reservation_status = "RELEASED"


def _settle_budget_reservation(session, model, task, *, timestamp) -> None:
    if model.budget_reservation_status != "RESERVED":
        return
    artifact_bytes = sum(
        int(item.size_bytes or 0)
        for item in session.query(ArtifactModel)
        .filter(ArtifactModel.task_id == task.id)
        .all()
    )
    model.budget_settlement_json = {
        "duration_seconds": int(task.duration_sec or 0),
        "artifact_bytes": artifact_bytes,
        "agent_id": task.agent_id,
        "settled_at": timestamp.isoformat(),
    }
    model.budget_reservation_status = "SETTLED"


def _execute_approved_tool_call(tool_call_id: str) -> DropInsightToolCallModel:
    session = new_session()
    try:
        model = (
            session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.id == tool_call_id)
            .with_for_update()
            .first()
        )
        if model is None:
            raise ValueError("tool call not found")
        if model.status not in {"APPROVED", "TASK_CREATED", "COMPLETED"}:
            raise ValueError(f"tool call is not executable: {model.status}")
        if model.status in {"TASK_CREATED", "COMPLETED"}:
            return model
        arguments = model.arguments_json or {}
        timestamp = now_utc()
        if model.tool_name == "get_agent_status":
            agent = session.get(AgentModel, arguments["agent_id"])
            model.result_json = agent.to_dict() if agent is not None else {"found": False}
            model.status = "COMPLETED"
            model.executed_at = timestamp
            model.budget_settlement_json = {
                "duration_seconds": 0,
                "artifact_bytes": 0,
                "agent_id": arguments.get("agent_id"),
                "settled_at": timestamp.isoformat(),
            }
            model.budget_reservation_status = "SETTLED"
            _append_event(
                session,
                model.diagnosis_id,
                "tool_call.completed",
                "SYSTEM",
                {"tool_call_id": model.id, "tool_name": model.tool_name},
                timestamp,
            )
            session.commit()
            session.refresh(model)
            return model

        collector_type = {
            "collect_sys_metrics": "sys_metrics",
            "start_perf_profile": "perf_cpu",
            "start_ebpf_io_profile": "ebpf_io",
            "start_pyspy_profile": "pyspy",
        }.get(model.tool_name)
        if collector_type is None:
            model.status = "FAILED"
            model.result_json = {"error": "tool has no executor"}
            model.executed_at = timestamp
            _release_budget_reservation(model, timestamp=timestamp, reason="executor_missing")
            session.commit()
            session.refresh(model)
            return model
        task = SqlRepository().create_task_in_session(
            session,
            CreateTaskRequest(
                name=f"Drop Insight: {model.tool_name}",
                agent_id=arguments["agent_id"],
                target_pid=arguments["pid"],
                collector_type=collector_type,
                sample_rate=arguments.get("sample_rate", 99),
                duration_sec=arguments["duration_seconds"],
                options={
                    "diagnosis_step_id": tool_call_id,
                    "drop_insight_diagnosis_id": model.diagnosis_id,
                    "drop_insight_tool_call_id": tool_call_id,
                },
            ),
        )
        model.task_id = task.id
        model.status = "TASK_CREATED"
        model.executed_at = now_utc()
        _append_event(
            session,
            model.diagnosis_id,
            "tool_call.task_created",
            "SYSTEM",
            {"tool_call_id": model.id, "task_id": task.id},
            model.executed_at,
        )
        session.commit()
        session.refresh(model)
        return model
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


_UNKNOWN_HYPOTHESIS = {
    "statement": "其他未知原因（OTHER/UNKNOWN）",
    # 字段需非空才能通过 schema 校验；占位文案表明该候选是兜底假设。
    "expected": ["存在尚未被既有候选假设充分解释的观测现象"],
    "falsification": ["所有观测现象均能被既有候选假设充分解释"],
}

# 每个领域的常见竞品假设（方案 §5.2：保留 1~3 个候选 + OTHER/UNKNOWN）。
# 主假设由 planner 决定；这里提供可区分的备选，避免假设成为答案边界。
_ALTERNATIVE_HYPOTHESES: dict[str, list[dict]] = {
    "CPU_HOTSPOT": [
        {
            "statement": "同宿主机其他工作负载与目标进程争抢 CPU 资源",
            "expected": ["同宿主机对照实例存在相似 CPU 热点"],
            "falsification": ["同宿主机其他实例 CPU 平稳且目标存在独立热点"],
        },
        {
            "statement": "内存压力或 I/O 等待间接导致 CPU 饱和",
            "expected": ["系统指标显示内存压力、换页或 I/O 等待升高"],
            "falsification": ["内存与 I/O 指标平稳且 CPU 热点集中在业务函数"],
        },
    ],
    "IO_LATENCY": [
        {
            "statement": "同宿主机 IO 争抢导致目标进程写盘变慢",
            "expected": ["同窗口其他容器存在高 IO 负载或队列深度上升"],
            "falsification": ["宿主机 IO 平稳且目标进程自身同步写盘占主导"],
        },
        {
            "statement": "目标进程同步写盘或落盘路径过重",
            "expected": ["IO 热点集中在目标进程的写系统调用"],
            "falsification": ["目标进程 IO 占比低但块设备延迟仍高"],
        },
    ],
    "NETWORK_DEGRADATION": [
        {
            "statement": "宿主机网络丢包或重传导致服务变慢",
            "expected": ["网络指标显示重传率、丢包或队列积压升高"],
            "falsification": ["网络指标平稳且本实例存在独立热点"],
        },
        {
            "statement": "下游依赖响应变慢传播到本实例",
            "expected": ["下游调用耗时上升或连接池排队"],
            "falsification": ["下游指标正常且本实例存在独立热点"],
        },
    ],
    "JVM_GC": [
        {
            "statement": "堆内存配置过小导致频繁 Full GC",
            "expected": ["GC 日志显示 Full GC 频率高且堆使用接近上限"],
            "falsification": ["GC 平稳且停顿集中在业务代码"],
        },
        {
            "statement": "内存泄漏导致堆持续增长",
            "expected": ["堆使用随时间单调上升且无法回落"],
            "falsification": ["堆使用稳定且 GC 正常"],
        },
    ],
    "PYTHON_RUNTIME": [
        {
            "statement": "GIL 竞争限制多线程并发能力",
            "expected": ["多线程 CPU 密集但并行度受限"],
            "falsification": ["单线程热点明确且 GIL 非瓶颈"],
        },
        {
            "statement": "等待或 I/O 阻塞被误判为用户态热点",
            "expected": ["系统调用或等待占比偏高"],
            "falsification": ["用户态函数独占热点"],
        },
    ],
    "MEMORY_PRESSURE": [
        {
            "statement": "无界缓存或对象未释放导致 RSS 增长",
            "expected": ["RSS 单调增长且存在无界缓存路径"],
            "falsification": ["RSS 稳定且内存压力来自宿主机"],
        },
        {
            "statement": "同宿主机内存争抢触发换页",
            "expected": ["同窗口宿主机内存压力或 swap 上升"],
            "falsification": ["宿主机内存平稳且目标自身 RSS 异常增长"],
        },
    ],
    "DOWNSTREAM_DEPENDENCY": [
        {
            "statement": "本实例资源正常但下游队列积压",
            "expected": ["下游队列深度或延迟上升"],
            "falsification": ["下游平稳且本实例存在独立热点"],
        },
        {
            "statement": "连接池耗尽导致调用排队等待",
            "expected": ["连接池使用率接近上限或等待时长上升"],
            "falsification": ["连接池充足且延迟来自其他环节"],
        },
    ],
    "QUEUE_CONGESTION": [
        {
            "statement": "消费者处理能力不足导致积压",
            "expected": ["消费者资源饱和且消费速率跟不上生产"],
            "falsification": ["消费者资源充足且队列持续积压"],
        },
        {
            "statement": "生产者突发写入导致瞬时积压",
            "expected": ["生产速率出现尖峰"],
            "falsification": ["生产速率平稳但消费速率下降"],
        },
    ],
    "DATABASE_LOCK": [
        {
            "statement": "慢查询持有锁时间过长",
            "expected": ["存在长事务或慢查询锁等待"],
            "falsification": ["锁等待快照为空且连接池正常"],
        },
        {
            "statement": "连接池耗尽导致数据库排队",
            "expected": ["连接池使用率接近上限"],
            "falsification": ["连接池充足且查询本身慢"],
        },
    ],
    "NOISY_NEIGHBOR": [
        {
            "statement": "目标实例自身存在独立热点",
            "expected": ["目标进程存在独占性的热点函数"],
            "falsification": ["目标资源平稳而宿主机其他实例异常"],
        },
        {
            "statement": "宿主机共享资源（带宽/IO）被其他租户占用",
            "expected": ["宿主机共享指标出现争抢"],
            "falsification": ["共享资源平稳且目标自身异常"],
        },
    ],
    "CONTAINER_RESOURCE_LIMIT": [
        {
            "statement": "CPU 配额限制导致节流",
            "expected": ["出现 CPU 节流或配额用尽"],
            "falsification": ["配额充足且无节流"],
        },
        {
            "statement": "内存限制触发频繁回收或 OOM 风险",
            "expected": ["内存接近限额或存在回收波动"],
            "falsification": ["内存余量充足且资源平稳"],
        },
    ],
}


def _candidate_hypotheses(category: str, plan: dict) -> list[dict]:
    """主假设 + 领域备选假设 + OTHER/UNKNOWN（方案 §5.2）。"""
    primary = {
        "statement": plan["statement"],
        "expected": plan.get("expected", []),
        "falsification": plan.get("falsification", []),
    }
    alternatives = _ALTERNATIVE_HYPOTHESES.get(category, [])
    return [primary, *alternatives, _UNKNOWN_HYPOTHESIS]


def _score_candidate_hypotheses(diagnosis_id: str) -> None:
    """基于已导入证据给「非主假设」的候选假设打分。

    主假设状态由报告生成决定（generate_report）；这里只更新备选假设与
    OTHER/UNKNOWN，让候选集合保留支持/反证/未知三种合法状态。
    """
    session = new_session()
    try:
        hypotheses = (
            session.query(DropInsightHypothesisModel)
            .filter(DropInsightHypothesisModel.diagnosis_id == diagnosis_id)
            .all()
        )
        if not hypotheses:
            return
        report_hypothesis_ids = {
            row.hypothesis_id
            for row in session.query(DropInsightReportModel)
            .filter(DropInsightReportModel.diagnosis_id == diagnosis_id)
            .all()
            if row.hypothesis_id
        }
        evidence_rows = (
            session.query(DropInsightEvidenceModel)
            .filter(DropInsightEvidenceModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightEvidenceModel.created_at.desc())
            .all()
        )
        metadata = None
        for row in evidence_rows:
            envelope = EvidenceEnvelope.model_validate(row.envelope_json)
            obs_meta = (envelope.observation or {}).get("metadata") or {}
            if isinstance(obs_meta, dict) and obs_meta.get("top_functions"):
                metadata = obs_meta
                break
        if metadata is None:
            return
        timestamp = now_utc()
        changed = False
        for hypothesis in hypotheses:
            if hypothesis.id in report_hypothesis_ids:
                continue
            if hypothesis.status in {"SUPPORTED", "COUNTER"}:
                continue
            predicate = _compute_hypothesis_predicate(hypothesis, metadata)
            if predicate and predicate["outcome"] == "SUPPORT":
                hypothesis.status = "SUPPORTED"
            elif predicate and predicate["outcome"] == "COUNTER":
                hypothesis.status = "COUNTER"
            else:
                hypothesis.status = "INCONCLUSIVE"
            hypothesis.updated_at = timestamp
            changed = True
        if changed:
            session.commit()
    finally:
        session.close()


def run_diagnosis_planner(
    diagnosis_id: str,
    payload: RunPlannerRequest,
    *,
    requested_by: str = "system:planner",
) -> dict | None:
    diagnosis = get_diagnosis(diagnosis_id)
    if diagnosis is None:
        return None
    target = diagnosis.target_json or {}
    if not target.get("agent_id") or not target.get("pid"):
        raise ValueError("planner requires target.agent_id and target.pid")

    # 初始规划是幂等操作。刷新或重复点击必须返回同一假设/工具调用，
    # 只有反证、证据不足或人工纠正入口才会显式创建下一轮。
    existing_calls = list_tool_calls(diagnosis_id)
    existing_hypotheses = list_hypotheses(diagnosis_id)
    if existing_calls and existing_hypotheses:
        primary = next(
            (item for item in existing_hypotheses if item.id == existing_calls[0].hypothesis_id),
            existing_hypotheses[0],
        )
        return {
            "planner_kind": "IDEMPOTENT_REPLAY",
            "planner_version": "rules-v2",
            "classification_confidence": 1.0,
            "category": "EXISTING_PLAN",
            "decision_source": primary.source,
            "reasoning_summary": "返回已持久化的诊断计划；重复请求不会创建新假设。",
            "hypothesis": primary.to_dict(),
            "tool_call": existing_calls[0].to_dict(),
        }

    query = (diagnosis.query or "").lower()
    triage_arguments = {
        "agent_id": target["agent_id"],
        "pid": target["pid"],
        "duration_seconds": 15,
    }
    if any(token in query for token in ("数据库锁", "锁等待", "deadlock", "mysql lock", "db lock")):
        plan = {
            "planner_version": "rules-v2",
            "category": "DATABASE_LOCK",
            "statement": "请求变慢可能与数据库锁等待或连接阻塞有关",
            "expected": ["系统初筛显示进程等待、上下文切换或负载异常，需继续采集数据库锁证据"],
            "falsification": ["系统资源平稳且数据库锁等待快照为空"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in query for token in ("丢包", "packet loss", "网络抖动", "重传", "timeout", "超时")):
        plan = {
            "planner_version": "rules-v2",
            "category": "NETWORK_DEGRADATION",
            "statement": "服务异常可能由网络丢包、重传或连接超时引起",
            "expected": ["系统初筛显示网络或等待指标异常，需继续采集连接与重传证据"],
            "falsification": ["同窗口网络指标和对照实例均正常"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in query for token in ("jvm", "gc", "full gc", "垃圾回收")):
        plan = {
            "planner_version": "rules-v2",
            "category": "JVM_GC",
            "statement": "Java 服务停顿可能与 GC 压力或堆内存波动有关",
            "expected": ["系统初筛显示 CPU、RSS 或停顿窗口异常，需继续采集 JVM 证据"],
            "falsification": ["GC、堆和系统资源在同窗口均处于基线范围"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in query for token in ("下游", "依赖服务", "rpc", "upstream", "downstream")):
        plan = {
            "planner_version": "rules-v2",
            "category": "DOWNSTREAM_DEPENDENCY",
            "statement": "入口服务变慢可能由下游依赖节点传播引起",
            "expected": ["本实例资源不足以解释延迟，需要同窗口下游和对照实例证据"],
            "falsification": ["下游节点正常且本实例存在独立热点"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in query for token in ("队列", "积压", "backlog", "consumer lag", "mq")):
        plan = {
            "planner_version": "rules-v2",
            "category": "QUEUE_CONGESTION",
            "statement": "吞吐下降可能由队列积压或消费者处理能力不足引起",
            "expected": ["系统初筛显示消费者资源饱和，需继续引用队列深度与消费速率"],
            "falsification": ["队列无积压且消费者资源正常"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in query for token in ("限流", "cpu limit", "memory limit", "容器限制", "throttl")):
        plan = {
            "planner_version": "rules-v2",
            "category": "CONTAINER_RESOURCE_LIMIT",
            "statement": "性能下降可能由容器 CPU 节流或内存限制触发",
            "expected": ["系统指标显示资源接近限额或出现节流相关异常"],
            "falsification": ["容器限额充足且无节流记录"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in query for token in ("噪声邻居", "同宿主机", "资源争抢", "noisy neighbor")):
        plan = {
            "planner_version": "rules-v2",
            "category": "NOISY_NEIGHBOR",
            "statement": "目标服务可能受到同宿主机其他工作负载的资源干扰",
            "expected": ["目标与同宿主机对照实例在同窗口出现共享资源竞争"],
            "falsification": ["同宿主机其他实例平稳且目标自身存在独立热点"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in query for token in ("io", "disk", "磁盘", "写入", "读取", "延迟")):
        plan = {
            "planner_version": "rules-v2",
            "category": "IO_LATENCY",
            "statement": "目标进程的性能下降可能由磁盘 IO 延迟或内核写入路径阻塞引起",
            "expected": ["eBPF IO 延迟分布出现长尾或高延迟桶"],
            "falsification": ["IO 延迟分布与基线一致且没有长尾"],
            "tool_name": "start_ebpf_io_profile",
            "arguments": {
                "agent_id": target["agent_id"],
                "pid": target["pid"],
                "duration_seconds": 15,
            },
        }
    elif any(token in query for token in ("python", "py-spy", "gil", "协程")):
        plan = {
            "planner_version": "rules-v2",
            "category": "PYTHON_RUNTIME",
            "statement": "目标 Python 进程可能存在用户态热点函数或 GIL 竞争",
            "expected": ["py-spy 样本集中在少数 Python 函数或线程"],
            "falsification": ["Python 栈样本均匀且无明显热点"],
            "tool_name": "start_pyspy_profile",
            "arguments": {
                "agent_id": target["agent_id"],
                "pid": target["pid"],
                "duration_seconds": 15,
                "sample_rate": 99,
            },
        }
    elif any(token in query for token in ("内存", "memory", "rss", "oom")):
        plan = {
            "planner_version": "rules-v2",
            "category": "MEMORY_PRESSURE",
            "statement": "目标进程可能存在内存压力或异常 RSS 增长",
            "expected": ["系统指标显示 RSS 或内存压力持续异常"],
            "falsification": ["RSS、换页和内存压力均处于正常范围"],
            "tool_name": "collect_sys_metrics",
            "arguments": {
                "agent_id": target["agent_id"],
                "pid": target["pid"],
                "duration_seconds": 15,
            },
        }
    elif any(
        token in query
        for token in ("cpu", "热点", "火焰图", "算力", "负载高", "load high")
    ):
        plan = {
            "planner_version": "rules-v2",
            "classification_confidence": 0.95,
            "category": "CPU_HOTSPOT",
            "statement": "目标进程可能存在 CPU 热点函数或系统调用开销",
            "expected": ["perf 样本集中在少数热点函数或内核调用链"],
            "falsification": ["CPU 样本均匀且没有显著热点"],
            "tool_name": "start_perf_profile",
            "arguments": {
                "agent_id": target["agent_id"],
                "pid": target["pid"],
                "duration_seconds": 15,
                "sample_rate": 99,
            },
        }
    else:
        questions = [
            {
                "question_id": "problem.domain",
                "prompt": "请补充异常属于 CPU、内存、磁盘 IO、网络、数据库还是语言运行时。",
            },
            {
                "question_id": "problem.symptom",
                "prompt": "请补充可观测症状，例如延迟、错误率、吞吐或资源曲线。",
            },
        ]
        session = new_session()
        try:
            persisted = _lock_diagnosis(session, diagnosis_id)
            if persisted is None:
                return None
            persisted.clarification_questions_json = questions
            timestamp = now_utc()
            _append_event(
                session,
                diagnosis_id,
                "planner.needs_clarification",
                "SYSTEM",
                {
                    "planner_kind": "DETERMINISTIC_RULES",
                    "planner_version": "rules-v2",
                    "category": "UNKNOWN",
                    "classification_confidence": 0.0,
                    "questions": questions,
                },
                timestamp,
            )
            _cas_session_update(
                session,
                persisted,
                status="NEEDS_CLARIFICATION",
                timestamp=timestamp,
            )
            session.commit()
        finally:
            session.close()
        return {
            "planner_kind": "DETERMINISTIC_RULES",
            "planner_version": "rules-v2",
            "classification_confidence": 0.0,
            "category": "UNKNOWN",
            "status": "NEEDS_CLARIFICATION",
            "clarification_questions": questions,
            "hypothesis": None,
            "tool_call": None,
        }

    # 规则负责范围/工具白名单，模型只在边界内提出和排序可证伪假设。
    # 模型不可用时保留确定性规则结果，且把来源显式展示给用户。
    proposal = propose_hypothesis_plan(
        query=diagnosis.query,
        target=target,
        category=plan["category"],
        rule_plan=plan,
        prior_hypotheses=[item.to_dict() for item in list_hypotheses(diagnosis_id)],
        allowed_tools=[plan["tool_name"]],
        route_priors=_successful_tool_route_priors(),
    ) if plan["category"] == "CPU_HOTSPOT" else None
    if proposal:
        plan["tool_name"] = proposal["tool_name"]
        plan["arguments"] = _planner_tool_arguments(plan["tool_name"], target)
        model_candidates = [
            {
                "statement": item["statement"],
                "expected": item["expected_observations"],
                "falsification": item["falsification_criteria"],
                "reason": item.get("rationale") or proposal.get("reasoning_summary", ""),
            }
            for item in proposal["hypotheses"]
        ]
        candidates = [*model_candidates, _UNKNOWN_HYPOTHESIS]
        source = "MODEL"
        generation_reason = proposal.get("reasoning_summary", "模型在策略边界内生成候选假设")
        plan["statement"] = model_candidates[0]["statement"]
        plan["expected"] = model_candidates[0]["expected"]
        plan["falsification"] = model_candidates[0]["falsification"]
    else:
        candidates = _candidate_hypotheses(plan["category"], plan)
        source = "DETERMINISTIC_RULE"
        generation_reason = "模型未启用或输出未通过约束校验，使用可复现规则兜底。"

    # 方案 §5.2：除主假设外，同时保留备选假设与 OTHER/UNKNOWN，
    # 避免假设成为答案边界。主假设仍驱动后续工具调用与报告生成。
    for candidate in candidates:
        if any(item.statement == candidate["statement"] for item in list_hypotheses(diagnosis_id)):
            continue
        create_hypothesis(
            diagnosis_id,
            CreateHypothesisRequest(
                statement=candidate["statement"],
                expected_observations=candidate["expected"],
                falsification_criteria=candidate["falsification"],
            ),
            source=source if candidate is not _UNKNOWN_HYPOTHESIS else "SYSTEM_FALLBACK",
            round_index=1,
            generation_reason=candidate.get("reason", generation_reason),
        )
    hypotheses = list_hypotheses(diagnosis_id)
    hypothesis = next(
        (item for item in hypotheses if item.statement == plan["statement"]),
        hypotheses[0] if hypotheses else None,
    )
    if hypothesis is None:
        hypothesis = create_hypothesis(
            diagnosis_id,
            CreateHypothesisRequest(
                statement=plan["statement"],
                expected_observations=plan.get("expected", []),
                falsification_criteria=plan.get("falsification", []),
            ),
            source=source,
            round_index=1,
            generation_reason=generation_reason,
        )

    existing_calls = list_tool_calls(diagnosis_id)
    tool_call = next(
        (
            item for item in existing_calls
            if item.hypothesis_id == hypothesis.id
            and item.tool_name == plan["tool_name"]
            and item.arguments_json == plan["arguments"]
        ),
        None,
    )
    if tool_call is None:
        tool_call = request_tool_call(
            diagnosis_id,
            CreateToolCallRequest(
                hypothesis_id=hypothesis.id,
                tool_name=plan["tool_name"],
                arguments=plan["arguments"],
            ),
            requested_by=requested_by,
        )
    return {
        "planner_kind": "MODEL_ASSISTED" if proposal else "DETERMINISTIC_RULES",
        "planner_version": plan["planner_version"],
        "classification_confidence": plan.get("classification_confidence", 0.9),
        "category": plan["category"],
        "decision_source": source,
        "reasoning_summary": generation_reason,
        "hypothesis": hypothesis.to_dict(),
        "tool_call": tool_call.to_dict(),
    }


def import_task_evidence(
    diagnosis_id: str,
    payload: ImportTaskEvidenceRequest,
) -> list[DropInsightEvidenceModel] | None:
    session = new_session()
    try:
        diagnosis = _lock_diagnosis(
            session, diagnosis_id, payload.expected_version
        )
        if diagnosis is None:
            return None
        hypothesis = session.get(DropInsightHypothesisModel, payload.hypothesis_id)
        if hypothesis is None or hypothesis.diagnosis_id != diagnosis_id:
            raise ValueError("hypothesis does not belong to diagnosis")
        task = session.get(TaskModel, payload.task_id)
        if task is None:
            raise ValueError("task not found")
        if task.status != "DONE":
            raise ValueError("only DONE tasks can be imported as evidence")

        target = diagnosis.target_json or {}
        allowed_agent_id = target.get("agent_id")
        if allowed_agent_id and task.agent_id != allowed_agent_id:
            raise ValueError("task agent is outside diagnosis target scope")
        target_pid = target.get("pid")
        if target_pid and task.target_pid != target_pid:
            raise ValueError("task PID does not match diagnosis target")

        attempt = (
            session.query(TaskAttemptModel)
            .filter(TaskAttemptModel.task_id == task.id)
            .order_by(TaskAttemptModel.attempt_no.desc())
            .first()
        )
        # TaskAttempt represents collection execution. SUCCEEDED is the
        # canonical terminal state; DONE remains accepted for legacy rows.
        if attempt is None or attempt.status not in {"SUCCEEDED", "DONE"}:
            raise ValueError(
                "该任务没有成功完成的执行批次（TaskAttempt）。"
                "请在任务面板重新创建采集任务，等待采集状态为 SUCCEEDED、分析状态为 SUCCEEDED 后再导入；"
                "历史任务无法补齐真实执行溯源。"
            )
        artifacts = (
            session.query(ArtifactModel)
            .filter(ArtifactModel.task_id == task.id)
            .order_by(ArtifactModel.id.asc())
            .all()
        )
        if not artifacts:
            raise ValueError("task has no artifacts")
        successful_jobs = (
            session.query(AnalysisJobModel)
            .filter(
                AnalysisJobModel.task_id == task.id,
                AnalysisJobModel.status == "SUCCEEDED",
            )
            .all()
        )
        validated_artifact_ids = {
            int(artifact_id)
            for job in successful_jobs
            for artifact_id in (job.output_artifact_ids_json or [])
        }
        analysis_jobs_by_artifact = {
            int(artifact_id): job
            for job in successful_jobs
            for artifact_id in (job.output_artifact_ids_json or [])
        }

        imported: list[DropInsightEvidenceModel] = []
        timestamp = now_utc()
        for artifact in artifacts:
            evidence_id = f"ev_task_{task.id}_{artifact.id}"
            existing = session.get(DropInsightEvidenceModel, evidence_id)
            if existing is not None:
                imported.append(existing)
                continue

            metadata = dict(artifact.meta_json or {})
            predicate = _compute_hypothesis_predicate(hypothesis, metadata)
            if predicate is not None:
                metadata["hypothesis_predicate"] = predicate
            analysis_job = analysis_jobs_by_artifact.get(artifact.id)
            assessment = assess_artifact_evidence(
                task.collector_type,
                artifact.artifact_type,
                metadata,
                analyzer_validated=(
                    artifact.id in validated_artifact_ids
                    and artifact.integrity_status == "VERIFIED"
                ),
            )
            sample_count = assessment.sample_count
            degraded = bool(
                metadata.get("degraded")
                or (task.request_params or {}).get("degraded")
                or "degraded" in (task.status_reason or "").lower()
            )
            quality_level = (
                "HIGH"
                if (
                    assessment.sample_count_known
                    and sample_count >= assessment.minimum_samples
                    and not degraded
                    and assessment.schema_valid
                    and assessment.analyzer_validated
                )
                else "MEDIUM"
                if assessment.sample_count_known and sample_count > 0
                else "LOW"
            )
            event_start = task.started_at or task.created_at
            event_end = task.finished_at or timestamp
            envelope = EvidenceEnvelope(
                evidence_id=evidence_id,
                diagnosis_id=diagnosis_id,
                evidence_type=f"{task.collector_type.upper()}_{artifact.artifact_type.upper()}",
                source={
                    "tool_name": task.collector_type,
                    "task_id": task.id,
                    "task_attempt_id": attempt.id,
                    "artifact_id": str(artifact.id),
                    "artifact_sha256": artifact.sha256 or "",
                    "analysis_job_id": analysis_job.id if analysis_job else "",
                    "analyzer_type": analysis_job.analyzer_type if analysis_job else "",
                    "analyzer_version": str(
                        analysis_job.analyzer_version
                        if analysis_job
                        else metadata.get("analyzer_version", "unknown")
                    ),
                    "analyzer_output_schema_version": CONTRACT_VERSION,
                    "observation_json_pointer": "/metadata",
                },
                scope={
                    "agent_id": task.agent_id,
                    "service": target.get("service"),
                    "host_id": target.get("host_id"),
                    "instance_id": target.get("instance_id"),
                    "container_id": target.get("container_id"),
                    "pid": task.target_pid,
                },
                time_range={
                    "start": event_start,
                    "end": event_end,
                    "timezone": (diagnosis.time_range_json or {}).get(
                        "timezone",
                        "Asia/Shanghai",
                    ),
                },
                observation={
                    "artifact_type": artifact.artifact_type,
                    "object_key": artifact.object_key,
                    "content_type": artifact.content_type,
                    "size_bytes": artifact.size_bytes,
                    "metadata": metadata,
                },
                quality={
                    "level": quality_level,
                    "sample_count": sample_count,
                    "sample_count_known": assessment.sample_count_known,
                    "degraded": degraded,
                    "target_match": True,
                    "time_overlap": _time_ranges_overlap(
                        event_start,
                        event_end,
                        diagnosis.time_range_json or {},
                    ),
                    "schema_valid": assessment.schema_valid,
                    "analyzer_validated": assessment.analyzer_validated,
                    "minimum_samples": assessment.minimum_samples,
                },
                limitations=list(assessment.limitations),
            )
            classification = classify_evidence(envelope)
            evidence_role = _derive_imported_evidence_role(
                hypothesis,
                artifact,
                assessment,
                predicate=predicate,
            )
            if classification["can_support_conclusion"]:
                if evidence_role == "COUNTER":
                    classification["decision"] = "ACCEPT_COUNTER"
                elif evidence_role == "NEUTRAL":
                    classification = {
                        "decision": "ACCEPT_NEUTRAL",
                        "can_support_conclusion": False,
                        "reasons": [
                            "Analyzer 未产出能够支持或证伪当前假设的结构化谓词"
                        ],
                    }
            record_evidence_decision(
                classification["decision"],
                task.collector_type,
                artifact.artifact_type,
            )
            model = DropInsightEvidenceModel(
                id=evidence_id,
                diagnosis_id=diagnosis_id,
                hypothesis_id=payload.hypothesis_id,
                role=evidence_role,
                envelope_json=envelope.model_dump(mode="json"),
                classification_json=classification,
                created_at=timestamp,
            )
            session.add(model)
            imported.append(model)

        _append_event(
            session,
            diagnosis_id,
            "task_evidence.imported",
            "SYSTEM",
            {
                "task_id": task.id,
                "task_attempt_id": attempt.id,
                "evidence_refs": [item.id for item in imported],
            },
            timestamp,
        )
        _cas_session_update(
            session, diagnosis, status="COLLECTING_EVIDENCE", timestamp=timestamp
        )
        session.commit()
        for item in imported:
            session.refresh(item)
        return imported
    finally:
        session.close()


def _append_event(
    session,
    diagnosis_id: str,
    event_type: str,
    actor: str,
    payload: dict,
    timestamp,
) -> None:
    # Serialize event writers on the aggregate root.  count()+1 races when
    # two requests append concurrently and can violate the unique
    # (diagnosis_id, sequence) constraint.
    session.execute(
        select(DropInsightSessionModel.id)
        .where(DropInsightSessionModel.id == diagnosis_id)
        .with_for_update()
    ).scalar_one()
    current = session.execute(
        select(func.max(DropInsightEventModel.sequence)).where(
            DropInsightEventModel.diagnosis_id == diagnosis_id
        )
    ).scalar_one()
    sequence = int(current or 0) + 1
    session.add(
        DropInsightEventModel(
            id=f"event_{uuid4().hex}",
            diagnosis_id=diagnosis_id,
            sequence=sequence,
            event_type=event_type,
            actor=actor,
            payload_json=payload,
            occurred_at=timestamp,
        )
    )


def _lock_diagnosis(session, diagnosis_id: str, expected_version: int | None = None):
    diagnosis = (
        session.query(DropInsightSessionModel)
        .filter(DropInsightSessionModel.id == diagnosis_id)
        .with_for_update()
        .first()
    )
    if diagnosis is not None and expected_version is not None:
        if diagnosis.version != expected_version:
            raise ValueError(
                f"diagnosis version conflict: expected={expected_version}, "
                f"actual={diagnosis.version}"
            )
    return diagnosis


_SESSION_TRANSITIONS = {
    "NEEDS_CLARIFICATION": {"UNDERSTANDING", "HYPOTHESIZING", "NEEDS_CLARIFICATION"},
    "UNDERSTANDING": {"PLANNING", "HYPOTHESIZING", "NEEDS_CLARIFICATION", "UNDERSTANDING"},
    "PLANNING": {"HYPOTHESIZING", "COLLECTING_EVIDENCE", "INSUFFICIENT_EVIDENCE", "PLANNING"},
    "HYPOTHESIZING": {"PLANNING", "COLLECTING_EVIDENCE", "INSUFFICIENT_EVIDENCE", "HYPOTHESIZING"},
    "COLLECTING_EVIDENCE": {"HYPOTHESIZING", "INSUFFICIENT_EVIDENCE", "COMPLETED", "COLLECTING_EVIDENCE"},
    "INSUFFICIENT_EVIDENCE": {"HYPOTHESIZING", "PLANNING", "COLLECTING_EVIDENCE", "INSUFFICIENT_EVIDENCE"},
    "COMPLETED": {"COMPLETED"},
}


def _cas_session_update(session, diagnosis, *, status: str, timestamp) -> None:
    allowed = _SESSION_TRANSITIONS.get(diagnosis.status, set())
    if status not in allowed:
        raise ValueError(
            f"illegal diagnosis status transition: {diagnosis.status}->{status}"
        )
    expected_version = diagnosis.version
    result = session.execute(
        update(DropInsightSessionModel)
        .where(
            DropInsightSessionModel.id == diagnosis.id,
            DropInsightSessionModel.version == expected_version,
        )
        .values(
            status=status,
            updated_at=timestamp,
            version=expected_version + 1,
        )
    )
    if result.rowcount != 1:
        raise ValueError(
            f"diagnosis version conflict: expected={expected_version}"
        )
    diagnosis.status = status
    diagnosis.updated_at = timestamp
    diagnosis.version = expected_version + 1


def _derive_report_conclusion(
    hypothesis_statement: str,
    *,
    support_refs: list[str],
    counter_refs: list[str],
) -> str:
    """Create the authoritative conclusion from accepted server evidence."""

    if not support_refs:
        if counter_refs:
            return (
                "INSUFFICIENT_EVIDENCE：现有可信证据未支持该假设，且存在反证；"
                f"暂不接受假设“{hypothesis_statement}”。"
            )
        return (
            "INSUFFICIENT_EVIDENCE：当前没有能够支持该假设的可信证据；"
            f"假设“{hypothesis_statement}”仍待验证。"
        )
    if counter_refs:
        return (
            "MIXED_EVIDENCE：可信证据部分支持该假设，同时存在反证；"
            f"假设“{hypothesis_statement}”需要继续证伪。"
        )
    return f"SUPPORTED：可信采集证据支持假设“{hypothesis_statement}”。"


def _derive_next_actions(
    *,
    support_refs: list[str],
    counter_refs: list[str],
) -> list[str]:
    if not support_refs:
        return ["补充同一目标、同一时间窗口且经过 Analyzer 验证的结构化证据"]
    if counter_refs:
        return ["针对冲突证据执行独立的证伪采集，并比较同窗口结果"]
    return ["在相同负载下执行修复前后复测，确认热点和副作用变化"]


def _compute_hypothesis_predicate(
    hypothesis: DropInsightHypothesisModel,
    metadata: dict,
) -> dict | None:
    """Deterministically evaluate analyzer output against the hypothesis plan.

    Produces a normalized predicate: a top function matching an expected
    observation -> SUPPORT; matching a falsification criterion -> COUNTER;
    otherwise None (the caller keeps the artifact NEUTRAL). This is what makes
    the counter-evidence gate reachable: without a COUNTER path, no imported
    artifact can ever satisfy ``has_independent_counter_or_control``.
    """
    top_functions = metadata.get("top_functions")
    if not isinstance(top_functions, list):
        return None
    named = [
        row
        for row in top_functions
        if isinstance(row, dict)
        and isinstance(row.get("name"), str)
        and row["name"].strip()
    ]
    if not named:
        return None
    expected = hypothesis.expected_observations_json or []
    falsification = hypothesis.falsification_criteria_json or []
    statement = str(hypothesis.statement or "").casefold()

    def _percent(row: dict) -> float:
        return _safe_percent(row.get("percent"))

    def _is_kernel(name: str) -> bool:
        value = name.casefold().strip()
        markers = (
            "[kernel", "vmlinux", "__x64_sys_", "do_syscall_", "entry_syscall_",
            "schedule", "finish_task_switch", "irq", "softirq", "kworker",
        )
        return any(marker in value for marker in markers)

    def _is_lock(name: str) -> bool:
        value = name.casefold()
        return any(marker in value for marker in (
            "pthread_mutex", "futex", "spin_lock", "spinlock", "mutex_lock",
            "rwsem", "sem_wait", "lock_slowpath",
        ))

    def _predicate(outcome: str, reason: str, indexes: list[int], **metrics):
        return {
            "outcome": outcome,
            "version": "hypothesis-predicate-v2",
            "reason": reason,
            "criterion_indexes": indexes,
            "metrics": metrics,
        }

    significant = [row for row in named if _percent(row) >= 20.0]
    user_rows = [row for row in named if not _is_kernel(str(row["name"]))]
    kernel_rows = [row for row in named if _is_kernel(str(row["name"]))]
    lock_rows = [row for row in named if _is_lock(str(row["name"]))]
    dominant_user = max(user_rows, key=_percent, default=None)
    dominant_kernel = max(kernel_rows, key=_percent, default=None)
    dominant_user_pct = _percent(dominant_user) if dominant_user else 0.0
    dominant_kernel_pct = _percent(dominant_kernel) if dominant_kernel else 0.0

    user_hypothesis = any(token in statement for token in (
        "用户态", "业务代码", "hot function", "user-space", "userspace",
    ))
    kernel_hypothesis = any(token in statement for token in (
        "内核态", "系统调用", "中断", "kernel", "syscall",
    ))
    lock_hypothesis = any(token in statement for token in (
        "锁竞争", "自旋", "lock contention", "spin",
    ))

    # Planner prose describes signal classes rather than concrete symbols.
    # Turn the Analyzer's TopN distribution into an explicit, auditable
    # predicate so high-quality data is not incorrectly left neutral.
    if user_hypothesis:
        if dominant_user and dominant_user_pct >= 60.0 and 1 <= len(significant) <= 3:
            return _predicate(
                "SUPPORT",
                f"dominant user-space hotspot {dominant_user['name']} accounts for "
                f"{dominant_user_pct:.1f}% with {len(significant)} significant hotspot(s)",
                [0, 1],
                dominant_function=dominant_user["name"],
                dominant_percent=dominant_user_pct,
                significant_hotspot_count=len(significant),
            )
        if dominant_kernel and dominant_kernel_pct >= 40.0 and dominant_user_pct < 40.0:
            return _predicate(
                "COUNTER",
                f"kernel hotspot {dominant_kernel['name']} dominates at {dominant_kernel_pct:.1f}%",
                [0],
                dominant_function=dominant_kernel["name"],
                dominant_percent=dominant_kernel_pct,
            )

    if kernel_hypothesis:
        if dominant_kernel and dominant_kernel_pct >= 40.0:
            return _predicate(
                "SUPPORT",
                f"kernel/syscall hotspot {dominant_kernel['name']} accounts for {dominant_kernel_pct:.1f}%",
                [0],
                dominant_function=dominant_kernel["name"],
                dominant_percent=dominant_kernel_pct,
            )
        if dominant_user and dominant_user_pct >= 60.0 and dominant_kernel_pct < 20.0:
            return _predicate(
                "COUNTER",
                f"user-space hotspot {dominant_user['name']} dominates while no kernel hotspot reaches 20%",
                [0],
                dominant_function=dominant_user["name"],
                dominant_percent=dominant_user_pct,
            )

    if lock_hypothesis:
        dominant_lock = max(lock_rows, key=_percent, default=None)
        if dominant_lock and _percent(dominant_lock) >= 5.0:
            return _predicate(
                "SUPPORT",
                f"lock-related hotspot {dominant_lock['name']} accounts for {_percent(dominant_lock):.1f}%",
                [0],
                dominant_function=dominant_lock["name"],
                dominant_percent=_percent(dominant_lock),
            )
        if dominant_user and dominant_user_pct >= 60.0 and not lock_rows:
            return _predicate(
                "COUNTER",
                "a strong non-lock user-space hotspot exists and no lock-related symbol was sampled",
                [0],
                dominant_function=dominant_user["name"],
                dominant_percent=dominant_user_pct,
            )

    def _matches(text_entries, name):
        lowered = name.casefold()
        for entry in text_entries:
            if not isinstance(entry, str):
                continue
            tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_.]*", entry.casefold())
            for token in tokens:
                if len(token) < 3:
                    continue
                if token in lowered or lowered in token:
                    return True
        return False

    for row in named:
        name = str(row["name"])
        if _matches(expected, name):
            return {
                "outcome": "SUPPORT",
                "version": "hypothesis-predicate-v2",
                "reason": f"top function {name} matches an expected observation",
                "criterion_indexes": [0],
            }
        if _matches(falsification, name):
            return {
                "outcome": "COUNTER",
                "version": "hypothesis-predicate-v2",
                "reason": f"top function {name} matches a falsification criterion",
                "criterion_indexes": [0],
            }
    return None


def _derive_imported_evidence_role(
    hypothesis: DropInsightHypothesisModel,
    artifact: ArtifactModel,
    assessment,
    predicate: dict | None = None,
) -> str:
    """Derive polarity from analyzer-produced predicates, never request data.

    Analyzer outputs may expose a normalized ``hypothesis_predicate``.  For
    perf TopN output we also accept the analyzer-produced top-functions list
    and compare it with the hypothesis text.  Other artifacts remain NEUTRAL
    instead of being optimistically labelled SUPPORT.
    """

    if not (assessment.schema_valid and assessment.analyzer_validated):
        return "NEUTRAL"
    metadata = artifact.meta_json or {}
    if predicate is None:
        predicate = metadata.get("hypothesis_predicate")
    if isinstance(predicate, dict):
        outcome = str(predicate.get("outcome") or "").upper()
        if outcome in {"SUPPORT", "COUNTER", "NEUTRAL"}:
            return outcome

    top_functions = metadata.get("top_functions")
    if isinstance(top_functions, list):
        statement = hypothesis.statement.casefold()
        valid_rows = [row for row in top_functions if isinstance(row, dict)]
        named = [
            row for row in valid_rows
            if isinstance(row.get("name"), str) and row["name"].strip()
        ]
        if any(row["name"].casefold() in statement for row in named):
            return "SUPPORT"
        if named and max(_safe_percent(row.get("percent")) for row in named) >= 30:
            # A strong hotspot exists, but it does not substantiate this
            # particular hypothesis.  It is useful context, not counterproof.
            return "NEUTRAL"
    return "NEUTRAL"


def _safe_percent(value) -> float:
    try:
        return max(0.0, min(100.0, float(value or 0)))
    except (TypeError, ValueError):
        return 0.0


def _parse_datetime(value):
    if value is None or not isinstance(value, str):
        return value
    try:
        from datetime import datetime

        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _sample_count(metadata: dict) -> int:
    for key in ("sample_count", "samples", "total_samples", "event_count"):
        value = metadata.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return max(0, value)
    return 0


def _time_ranges_overlap(start, end, requested: dict) -> bool:
    requested_start = requested.get("start")
    requested_end = requested.get("end")
    if not requested_start or not requested_end:
        return True
    try:
        from datetime import datetime, timezone

        if isinstance(requested_start, str):
            requested_start = datetime.fromisoformat(requested_start.replace("Z", "+00:00"))
        if isinstance(requested_end, str):
            requested_end = datetime.fromisoformat(requested_end.replace("Z", "+00:00"))
        start = _as_utc(start, timezone)
        end = _as_utc(end, timezone)
        requested_start = _as_utc(requested_start, timezone)
        requested_end = _as_utc(requested_end, timezone)
        return start < requested_end and end > requested_start
    except (TypeError, ValueError):
        return False


def _as_utc(value, timezone):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ── 修复前后 VERIFIED 验证闭环（guide #4.6）──────────────────

FIX_VERIFY_RELATIVE_THRESHOLD = 0.3


def compare_before_after(
    before_top: list[dict] | None,
    after_top: list[dict] | None,
    *,
    threshold: float = FIX_VERIFY_RELATIVE_THRESHOLD,
) -> dict:
    """Compare the dominant hotspot between a before and after profile task.

    A fix is VERIFIED when the before-task hotspot function has disappeared
    from the after top list or its percent dropped by at least ``threshold``
    (relative). Pure and deterministic so it can be unit-tested.
    """
    def _hotspots(rows):
        return sorted(
            [row for row in (rows or []) if isinstance(row, dict)],
            key=lambda row: float(row.get("percent") or 0),
            reverse=True,
        )

    before = _hotspots(before_top)
    after = _hotspots(after_top)
    if not before:
        return {
            "outcome": "REJECTED",
            "reason": "修复前任务没有有效 TopN 热点数据，无法建立对比基线",
        }
    if not after:
        return {
            "outcome": "REJECTED",
            "reason": "修复后任务没有有效 TopN 热点数据，不能把数据缺失当作热点消失",
        }
    hotspot = before[0]
    name = str(hotspot.get("name") or "")
    before_pct = float(hotspot.get("percent") or 0)
    after_names = {row.get("name") for row in after if row.get("name")}
    after_same = next((row for row in after if row.get("name") == name), None)
    after_pct = float(after_same.get("percent") or 0) if after_same else 0.0

    if name and name not in after_names:
        outcome, reason = "VERIFIED", f"修复后热点 {name} 已从 TopN 消失"
    elif after_pct <= before_pct * (1 - threshold):
        outcome, reason = (
            "VERIFIED",
            f"热点 {name} 占比由 {before_pct:.1f}% 降至 {after_pct:.1f}%",
        )
    else:
        outcome, reason = (
            "REJECTED",
            f"热点 {name} 占比未显著下降（{before_pct:.1f}% -> {after_pct:.1f}%）",
        )
    return {
        "outcome": outcome,
        "reason": reason,
        "before_hotspot": hotspot,
        "after_hotspot": after_same,
        "before_percent": before_pct,
        "after_percent": after_pct,
    }


def _task_top_functions(task_id: str) -> list[dict]:
    session = new_session()
    try:
        artifacts = (
            session.query(ArtifactModel)
            .filter(
                ArtifactModel.task_id == task_id,
                ArtifactModel.artifact_type == "top_json",
            )
            .all()
        )
        for artifact in artifacts:
            top = (artifact.meta_json or {}).get("top_functions")
            if isinstance(top, list):
                return top
        return []
    finally:
        session.close()


def verify_diagnosis_fix(
    diagnosis_id: str,
    *,
    before_task_id: str,
    after_task_id: str,
    fix_summary: str | None = None,
    created_by: str | None = None,
) -> dict | None:
    """Apply-fix -> same-load re-test -> before/after comparison."""
    before_top = _task_top_functions(before_task_id)
    after_top = _task_top_functions(after_task_id)
    comparison = compare_before_after(before_top, after_top)
    session = new_session()
    try:
        model = FixVerificationModel(
            id=f"fix_{uuid4().hex}",
            diagnosis_id=diagnosis_id,
            fix_summary=fix_summary,
            before_task_id=before_task_id,
            after_task_id=after_task_id,
            outcome=comparison["outcome"],
            before_hotspot_json=comparison.get("before_hotspot"),
            after_hotspot_json=comparison.get("after_hotspot"),
            comparison_json=comparison,
            created_by=created_by,
            created_at=now_utc(),
        )
        session.add(model)
        session.commit()
        session.refresh(model)
        return _fix_view(model)
    finally:
        session.close()


def list_fix_verifications(
    diagnosis_id: str, *, limit: int = 50
) -> list[dict]:
    session = new_session()
    try:
        rows = (
            session.query(FixVerificationModel)
            .filter(FixVerificationModel.diagnosis_id == diagnosis_id)
            .order_by(FixVerificationModel.created_at.desc())
            .limit(max(1, int(limit)))
            .all()
        )
        return [_fix_view(row) for row in rows]
    finally:
        session.close()


def _fix_view(model) -> dict:
    return {
        "id": model.id,
        "diagnosis_id": model.diagnosis_id,
        "fix_summary": model.fix_summary,
        "before_task_id": model.before_task_id,
        "after_task_id": model.after_task_id,
        "outcome": model.outcome,
        "comparison": model.comparison_json or {},
        "created_at": model.created_at,
    }


def clarify_diagnosis(
    diagnosis_id: str,
    payload: ClarifyDiagnosisRequest,
) -> dict | None:
    """Fill missing scope on a NEEDS_CLARIFICATION session, then resume planning.

    Updates target / time range under the session CAS and transitions
    NEEDS_CLARIFICATION -> UNDERSTANDING so the planner can resume.
    """
    session = new_session()
    try:
        diagnosis = _lock_diagnosis(session, diagnosis_id, payload.expected_version)
        if diagnosis is None:
            return None
        ts = now_utc()
        if payload.target is not None:
            diagnosis.target_json = {
                **(diagnosis.target_json or {}),
                **{
                    key: value
                    for key, value in payload.target.model_dump(mode="json").items()
                    if value is not None
                },
            }
        if payload.time_range is not None:
            diagnosis.time_range_json = payload.time_range.model_dump(mode="json")
        remaining_questions = _scope_questions(
            diagnosis.target_json,
            diagnosis.time_range_json,
        )
        diagnosis.clarification_questions_json = remaining_questions
        _append_event(
            session,
            diagnosis_id,
            "diagnosis.clarified",
            "USER",
            {
                "target": diagnosis.target_json,
                "time_range": diagnosis.time_range_json,
            },
            ts,
        )
        next_status = "NEEDS_CLARIFICATION" if remaining_questions else "UNDERSTANDING"
        _cas_session_update(session, diagnosis, status=next_status, timestamp=ts)
        session.commit()
        session.refresh(diagnosis)
        return diagnosis.to_dict()
    finally:
        session.close()
