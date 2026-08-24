from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func

from server.app.database import new_session
from server.app.models import (
    DiagnosticSkillActivationModel,
    DiagnosticSkillEvaluationModel,
    DiagnosticSkillModel,
    DropInsightFeedbackModel,
    DropInsightReportModel,
    DropInsightSessionModel,
    DropInsightToolCallModel,
)


_TOOL_CATEGORY = {
    "start_perf_profile": "CPU_HOTSPOT",
    "start_pyspy_profile": "PYTHON_RUNTIME",
    "start_ebpf_io_profile": "IO_LATENCY",
    "collect_sys_metrics": "SYSTEM_RESOURCE",
    "start_jvm_profile": "JVM_GC",
    "collect_database_diagnostics": "DATABASE_LOCK",
    "collect_network_diagnostics": "NETWORK_DEGRADATION",
}
_MATCH_THRESHOLD = 700


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _category_for_route(route: list[str]) -> str:
    for tool_name in route:
        if tool_name in _TOOL_CATEGORY:
            return _TOOL_CATEGORY[tool_name]
    return "GENERAL"


def _family_key(category: str, target: dict) -> str:
    environment = str(target.get("environment") or "*").strip().lower()
    return f"{category.lower()}:{environment}"


def _match_score(skill: DiagnosticSkillModel, category: str, target: dict) -> tuple[int, dict]:
    trigger = skill.trigger_json or {}
    if skill.category != category:
        return 0, {"category": "mismatch"}
    score = 650
    reasons = {"category": "exact"}
    required_environment = str(trigger.get("environment") or "*").lower()
    actual_environment = str(target.get("environment") or "*").lower()
    if required_environment not in {"", "*"}:
        if actual_environment != required_environment:
            return 300, {
                "category": "exact",
                "environment": "drift",
                "required": required_environment,
                "actual": actual_environment,
            }
        score += 200
        reasons["environment"] = "exact"
    else:
        score += 80
        reasons["environment"] = "wildcard"
    service = str(trigger.get("service") or "").lower()
    actual_service = str(target.get("service") or "").lower()
    if service and actual_service and service == actual_service:
        score += 100
        reasons["service"] = "exact"
    return min(score, 1000), reasons


def list_skills(*, include_retired: bool = True) -> list[dict]:
    session = new_session()
    try:
        query = session.query(DiagnosticSkillModel)
        if not include_retired:
            query = query.filter(DiagnosticSkillModel.status != "RETIRED")
        rows = query.order_by(
            DiagnosticSkillModel.updated_at.desc(), DiagnosticSkillModel.version.desc()
        ).all()
        activations = session.query(DiagnosticSkillActivationModel).all()
        activation_stats: dict[str, dict[str, int]] = {}
        for activation in activations:
            stats = activation_stats.setdefault(
                activation.skill_id,
                {"activation_count": 0, "correct_outcome_count": 0, "wrong_outcome_count": 0},
            )
            stats["activation_count"] += 1
            if activation.outcome == "CORRECT":
                stats["correct_outcome_count"] += 1
            elif activation.outcome == "WRONG":
                stats["wrong_outcome_count"] += 1

        result = []
        for item in rows:
            payload = item.to_dict()
            payload.update(
                activation_stats.get(
                    item.id,
                    {"activation_count": 0, "correct_outcome_count": 0, "wrong_outcome_count": 0},
                )
            )
            result.append(payload)
        return result
    finally:
        session.close()


def get_skill(skill_id: str) -> dict | None:
    session = new_session()
    try:
        skill = session.get(DiagnosticSkillModel, skill_id)
        if skill is None:
            return None
        result = skill.to_dict()
        result["evaluations"] = [
            item.to_dict()
            for item in session.query(DiagnosticSkillEvaluationModel)
            .filter(DiagnosticSkillEvaluationModel.skill_id == skill_id)
            .order_by(DiagnosticSkillEvaluationModel.created_at.asc())
            .all()
        ]
        result["activations"] = [
            item.to_dict()
            for item in session.query(DiagnosticSkillActivationModel)
            .filter(DiagnosticSkillActivationModel.skill_id == skill_id)
            .order_by(DiagnosticSkillActivationModel.created_at.desc())
            .limit(20)
            .all()
        ]
        return result
    finally:
        session.close()


def create_candidate_from_diagnosis(diagnosis_id: str, *, created_by: str) -> dict:
    """Extract a strategy only from a verified report confirmed by a human."""
    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if diagnosis is None:
            raise ValueError("diagnosis not found")
        report = (
            session.query(DropInsightReportModel)
            .filter(DropInsightReportModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightReportModel.created_at.desc())
            .first()
        )
        if report is None or (report.verification_json or {}).get("status") != "VERIFIED":
            raise ValueError("只有通过证据完整性校验的诊断才能沉淀技能")
        if not (report.evidence_refs_json or []):
            raise ValueError("报告没有可信证据引用，不能沉淀技能")
        feedback = (
            session.query(DropInsightFeedbackModel)
            .filter(DropInsightFeedbackModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightFeedbackModel.created_at.desc())
            .first()
        )
        if feedback is None or feedback.feedback_label != "correct":
            raise ValueError("需要人工确认结论正确后才能沉淀技能")
        existing_candidate = (
            session.query(DiagnosticSkillModel)
            .filter(DiagnosticSkillModel.source_diagnosis_ids_json == [diagnosis_id])
            .order_by(DiagnosticSkillModel.version.desc())
            .first()
        )
        if existing_candidate is not None:
            return existing_candidate.to_dict()
        calls = (
            session.query(DropInsightToolCallModel)
            .filter(
                DropInsightToolCallModel.diagnosis_id == diagnosis_id,
                DropInsightToolCallModel.status == "COMPLETED",
            )
            .order_by(DropInsightToolCallModel.created_at.asc())
            .all()
        )
        route = list(dict.fromkeys(item.tool_name for item in calls))
        if not route:
            raise ValueError("诊断没有已完成的真实工具调用，不能沉淀技能")
        category = _category_for_route(route)
        target = diagnosis.target_json or {}
        family_key = _family_key(category, target)
        latest = (
            session.query(DiagnosticSkillModel)
            .filter(DiagnosticSkillModel.family_key == family_key)
            .order_by(DiagnosticSkillModel.version.desc())
            .first()
        )
        timestamp = _now()
        skill = DiagnosticSkillModel(
            id=f"skill_{uuid4().hex}",
            family_key=family_key,
            category=category,
            version=(latest.version + 1) if latest else 1,
            status="CANDIDATE",
            source_diagnosis_ids_json=[diagnosis_id],
            trigger_json={
                "environment": target.get("environment") or "*",
                "service": target.get("service") or "",
                "query_terms": sorted(set(diagnosis.query.lower().split()))[:20],
            },
            strategy_json={
                "probe_order": route,
                "minimum_evidence": max(1, len(report.evidence_refs_json or [])),
                "confidence_floor": report.confidence / 1000,
                "stop_rule": "VERIFIED_REPORT_OR_EXHAUSTED_SAFE_PROBES",
                "refutation_rule": "COUNTER_EVIDENCE_OVERRIDES_ROUTE_PRIOR",
            },
            gate_metrics_json={"eligible": False, "reason": "not_evaluated"},
            parent_skill_id=latest.id if latest else None,
            created_by=created_by,
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(skill)
        session.commit()
        session.refresh(skill)
        return skill.to_dict()
    finally:
        session.close()


def evaluate_skill(skill_id: str) -> dict:
    """Run positive replay, misleading-negative and environment-drift gates."""
    session = new_session()
    try:
        skill = session.get(DiagnosticSkillModel, skill_id)
        if skill is None:
            raise ValueError("skill not found")
        source_id = (skill.source_diagnosis_ids_json or [None])[0]
        source = session.get(DropInsightSessionModel, source_id) if source_id else None
        report = (
            session.query(DropInsightReportModel)
            .filter(DropInsightReportModel.diagnosis_id == source_id)
            .order_by(DropInsightReportModel.created_at.desc())
            .first()
            if source_id else None
        )
        route = (skill.strategy_json or {}).get("probe_order") or []
        source_target = source.target_json if source else {}
        positive_score, positive_reason = _match_score(
            skill, skill.category, source_target
        )
        positive_pass = bool(
            source
            and report
            and (report.verification_json or {}).get("status") == "VERIFIED"
            and report.evidence_refs_json
            and route
            and positive_score >= _MATCH_THRESHOLD
        )
        negative_score, _ = _match_score(
            skill, "MISLEADING_OTHER_CATEGORY", source_target
        )
        negative_pass = negative_score < _MATCH_THRESHOLD
        drift_target = dict(source.target_json or {}) if source else {}
        drift_target["environment"] = "__incompatible_environment__"
        drift_score, _ = _match_score(skill, skill.category, drift_target)
        drift_pass = drift_score < _MATCH_THRESHOLD
        cases = [
            (
                "POSITIVE_REPLAY",
                positive_pass,
                1000 if positive_pass else 0,
                {
                    "source_diagnosis_id": source_id,
                    "verified_route": route,
                    "match_score": positive_score,
                    "match_reason": positive_reason,
                },
            ),
            ("MISLEADING_NEGATIVE", negative_pass, 1000 if negative_pass else 0, {"match_score": negative_score, "expected": "ABSTAIN"}),
            ("ENVIRONMENT_DRIFT", drift_pass, 1000 if drift_pass else 0, {"match_score": drift_score, "expected": "FALLBACK"}),
        ]
        session.query(DiagnosticSkillEvaluationModel).filter(
            DiagnosticSkillEvaluationModel.skill_id == skill_id
        ).delete(synchronize_session=False)
        timestamp = _now()
        for kind, passed, score, details in cases:
            session.add(DiagnosticSkillEvaluationModel(
                id=f"skill_eval_{uuid4().hex}", skill_id=skill_id,
                case_kind=kind, diagnosis_id=source_id if kind == "POSITIVE_REPLAY" else None,
                passed=passed, score=score, details_json=details, created_at=timestamp,
            ))
        eligible = all(item[1] for item in cases)
        skill.gate_metrics_json = {
            "eligible": eligible,
            "passed": sum(1 for item in cases if item[1]),
            "total": len(cases),
            "evaluation_mode": "DETERMINISTIC_CONTRACT_GATE",
            "requires_campaign_validation": True,
            "negative_transfer_guard": negative_pass,
            "environment_drift_guard": drift_pass,
        }
        skill.updated_at = timestamp
        session.commit()
    finally:
        session.close()
    return get_skill(skill_id) or {}


def publish_skill(skill_id: str) -> dict:
    session = new_session()
    try:
        skill = session.get(DiagnosticSkillModel, skill_id)
        if skill is None:
            raise ValueError("skill not found")
        if not (skill.gate_metrics_json or {}).get("eligible"):
            raise ValueError("技能尚未通过正例、误导反例和环境漂移门禁")
        timestamp = _now()
        session.query(DiagnosticSkillModel).filter(
            DiagnosticSkillModel.family_key == skill.family_key,
            DiagnosticSkillModel.status == "ACTIVE",
            DiagnosticSkillModel.id != skill.id,
        ).update({"status": "RETIRED", "updated_at": timestamp}, synchronize_session=False)
        skill.status = "ACTIVE"
        skill.published_at = timestamp
        skill.updated_at = timestamp
        session.commit()
        session.refresh(skill)
        return skill.to_dict()
    finally:
        session.close()


def quarantine_skill(skill_id: str, *, reason: str) -> dict:
    session = new_session()
    try:
        skill = session.get(DiagnosticSkillModel, skill_id)
        if skill is None:
            raise ValueError("skill not found")
        metrics = dict(skill.gate_metrics_json or {})
        metrics["quarantine_reason"] = reason
        skill.gate_metrics_json = metrics
        skill.status = "QUARANTINED"
        skill.updated_at = _now()
        session.commit()
        session.refresh(skill)
        return skill.to_dict()
    finally:
        session.close()


def rollback_skill(skill_id: str) -> dict:
    session = new_session()
    try:
        current = session.get(DiagnosticSkillModel, skill_id)
        if current is None:
            raise ValueError("skill not found")
        previous = (
            session.query(DiagnosticSkillModel)
            .filter(
                DiagnosticSkillModel.family_key == current.family_key,
                DiagnosticSkillModel.version < current.version,
                DiagnosticSkillModel.status.in_(["RETIRED", "QUARANTINED"]),
            )
            .order_by(DiagnosticSkillModel.version.desc())
            .first()
        )
        if previous is None:
            raise ValueError("没有可回滚的已发布历史版本")
        timestamp = _now()
        current.status = "QUARANTINED"
        current.updated_at = timestamp
        previous.status = "ACTIVE"
        previous.updated_at = timestamp
        previous.published_at = timestamp
        session.commit()
        return previous.to_dict()
    finally:
        session.close()


def apply_active_skill(diagnosis_id: str, category: str, plan: dict, target: dict) -> dict | None:
    session = new_session()
    try:
        skills = session.query(DiagnosticSkillModel).filter(
            DiagnosticSkillModel.status == "ACTIVE",
            DiagnosticSkillModel.category == category,
        ).all()
        ranked = sorted(
            ((_match_score(skill, category, target), skill) for skill in skills),
            key=lambda item: item[0][0], reverse=True,
        )
        if not ranked or ranked[0][0][0] < _MATCH_THRESHOLD:
            return None
        (score, reasons), skill = ranked[0]
        route = (skill.strategy_json or {}).get("probe_order") or []
        baseline_tool = plan["tool_name"]
        completed_tools = {
            item[0]
            for item in session.query(DropInsightToolCallModel.tool_name)
            .filter(
                DropInsightToolCallModel.diagnosis_id == diagnosis_id,
                DropInsightToolCallModel.status == "COMPLETED",
            )
            .all()
        }
        selected_tool = next(
            (tool_name for tool_name in route if tool_name not in completed_tools),
            baseline_tool,
        )
        timestamp = _now()
        activation = DiagnosticSkillActivationModel(
            id=f"skill_activation_{uuid4().hex}", skill_id=skill.id,
            diagnosis_id=diagnosis_id, match_score=score,
            match_reason_json={
                **reasons,
                "route": route,
                "skill_version": skill.version,
                "completed_route_tools": sorted(completed_tools),
            },
            baseline_tool=baseline_tool, selected_tool=selected_tool,
            created_at=timestamp, updated_at=timestamp,
        )
        existing = session.query(DiagnosticSkillActivationModel).filter(
            DiagnosticSkillActivationModel.diagnosis_id == diagnosis_id
        ).first()
        if existing is None:
            session.add(activation)
            session.commit()
        else:
            existing.match_score = score
            existing.match_reason_json = {
                **reasons,
                "route": route,
                "skill_version": skill.version,
                "completed_route_tools": sorted(completed_tools),
                "current_selected_tool": selected_tool,
            }
            existing.updated_at = timestamp
            session.commit()
        plan["tool_name"] = selected_tool
        return {
            "skill_id": skill.id,
            "version": skill.version,
            "match_score": score / 1000,
            "match_reason": reasons,
            "baseline_tool": baseline_tool,
            "selected_tool": selected_tool,
            "probe_order": route,
        }
    finally:
        session.close()


def record_activation_outcome(diagnosis_id: str, feedback_label: str) -> None:
    session = new_session()
    try:
        activation = session.query(DiagnosticSkillActivationModel).filter(
            DiagnosticSkillActivationModel.diagnosis_id == diagnosis_id
        ).first()
        if activation is None:
            return
        activation.outcome = feedback_label.upper()
        activation.updated_at = _now()
        session.commit()
        total = session.query(func.count(DiagnosticSkillActivationModel.id)).filter(
            DiagnosticSkillActivationModel.skill_id == activation.skill_id,
            DiagnosticSkillActivationModel.outcome.isnot(None),
        ).scalar() or 0
        wrong = session.query(func.count(DiagnosticSkillActivationModel.id)).filter(
            DiagnosticSkillActivationModel.skill_id == activation.skill_id,
            DiagnosticSkillActivationModel.outcome == "WRONG",
        ).scalar() or 0
        if total >= 2 and wrong >= 2 and wrong / total >= 0.5:
            skill = session.get(DiagnosticSkillModel, activation.skill_id)
            if skill is not None and skill.status == "ACTIVE":
                metrics = dict(skill.gate_metrics_json or {})
                metrics.update({"negative_transfer_count": wrong, "observed_outcomes": total})
                skill.gate_metrics_json = metrics
                skill.status = "QUARANTINED"
                skill.updated_at = _now()
                session.commit()
    finally:
        session.close()


def list_activations(diagnosis_id: str) -> list[dict]:
    session = new_session()
    try:
        return [
            item.to_dict()
            for item in session.query(DiagnosticSkillActivationModel)
            .filter(DiagnosticSkillActivationModel.diagnosis_id == diagnosis_id)
            .order_by(DiagnosticSkillActivationModel.created_at.desc())
            .all()
        ]
    finally:
        session.close()
