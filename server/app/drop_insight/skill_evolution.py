from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import func

from server.app.database import new_session
from server.app.drop_insight.evidence import EvidenceEnvelope, classify_evidence
from server.app.models import (
    DiagnosticSkillActivationModel,
    DiagnosticSkillEvaluationModel,
    DiagnosticSkillModel,
    DropInsightEvidenceModel,
    DropInsightHypothesisModel,
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
_CAMPAIGN_SOURCE_TOOL = {
    "sys_metrics": "collect_sys_metrics",
    "system_metrics": "collect_sys_metrics",
    "campaign_fault_snapshot": "collect_sys_metrics",
    "campaign_recovery_control": "collect_sys_metrics",
    "perf": "start_perf_profile",
    "perf_cpu": "start_perf_profile",
    "pyspy": "start_pyspy_profile",
    "py-spy": "start_pyspy_profile",
    "ebpf_io": "start_ebpf_io_profile",
    "jvm": "start_jvm_profile",
}
_MATCH_THRESHOLD = 700
_GENERIC_ROUTE_CATEGORIES = {"SYSTEM_RESOURCE"}
_SUBSYSTEM_CATEGORY = {
    "cpu": "CPU_HOTSPOT",
    "python": "PYTHON_RUNTIME",
    "storage": "IO_LATENCY",
    "io": "IO_LATENCY",
    "jvm": "JVM_GC",
    "database": "DATABASE_LOCK",
    "network": "NETWORK_DEGRADATION",
}
_TOOL_REQUIRED_CAPABILITIES = {
    "collect_sys_metrics": {"sys_metrics"},
    "start_perf_profile": {"perf_cpu"},
    "start_ebpf_io_profile": {"ebpf_io"},
    "start_pyspy_profile": {"pyspy"},
    "collect_database_diagnostics": {"database_lock"},
}


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


_PRUNED_HYPOTHESIS_STATUSES = {
    "REFUTED",
    "FALSIFIED",
    "DISPROVED",
    "RULED_OUT",
    "REJECTED",
    "CLOSED",
}
_PRUNED_TOOL_STATUSES = {"FAILED", "REJECTED", "CANCELLED", "DENIED"}


def _actual_exploration(
    hypotheses: list[DropInsightHypothesisModel],
    calls: list[DropInsightToolCallModel],
    report: DropInsightReportModel,
) -> dict:
    """Serialize the route actually explored, including dead ends."""
    nodes: list[dict] = []
    edges: list[dict] = []
    pruned: list[dict] = []
    switches: list[dict] = []
    hypothesis_ids = {item.id for item in hypotheses}

    for hypothesis in hypotheses:
        status = str(hypothesis.status or "OPEN").upper()
        nodes.append(
            {
                "id": hypothesis.id,
                "kind": "HYPOTHESIS",
                "label": hypothesis.statement,
                "status": status,
                "round": hypothesis.round_index,
                "reason": hypothesis.generation_reason or "",
            }
        )
        parent = hypothesis.parent_hypothesis_id
        if parent and parent in hypothesis_ids:
            edges.append({"from": parent, "to": hypothesis.id, "kind": "BRANCH"})
        if status in _PRUNED_HYPOTHESIS_STATUSES:
            pruned.append(
                {
                    "node_id": hypothesis.id,
                    "label": hypothesis.statement,
                    "reason": hypothesis.generation_reason or f"假设状态为 {status}",
                }
            )

    previous_call = None
    for index, call in enumerate(calls, start=1):
        status = str(call.status or "UNKNOWN").upper()
        category = _TOOL_CATEGORY.get(call.tool_name, "GENERAL")
        node_id = f"tool:{call.id}"
        nodes.append(
            {
                "id": node_id,
                "kind": "TOOL",
                "label": call.tool_name,
                "status": status,
                "category": category,
                "order": index,
                "hypothesis_id": call.hypothesis_id,
                "reason": call.policy_reason or "",
            }
        )
        if call.hypothesis_id in hypothesis_ids:
            edges.append({"from": call.hypothesis_id, "to": node_id, "kind": "INVESTIGATE"})
        if status in _PRUNED_TOOL_STATUSES:
            pruned.append(
                {
                    "node_id": node_id,
                    "label": call.tool_name,
                    "reason": call.policy_reason or f"工具状态为 {status}",
                }
            )
        if previous_call is not None:
            previous_category = _TOOL_CATEGORY.get(previous_call.tool_name, "GENERAL")
            if category != previous_category:
                switches.append(
                    {
                        "from_tool": previous_call.tool_name,
                        "from_category": previous_category,
                        "to_tool": call.tool_name,
                        "to_category": category,
                        "reason": "上一方向尚未形成充分证据，切换到新的取证方向",
                    }
                )
        previous_call = call

    return {
        "version": 1,
        "nodes": nodes,
        "edges": edges,
        "actual_route": [item.tool_name for item in calls],
        "pruned_branches": pruned,
        "direction_switches": switches,
        "verified_hypothesis_id": report.hypothesis_id,
        "summary": {
            "hypotheses_explored": len(hypotheses),
            "tool_calls": len(calls),
            "pruned_branches": len(pruned),
            "direction_switches": len(switches),
        },
    }


def _campaign_probe_route(
    session, diagnosis: DropInsightSessionModel, report: DropInsightReportModel
) -> list[str]:
    """Recover the real probe route from a verified Campaign trust chain.

    Campaign promotion imports an immutable TaskAttempt -> Artifact ->
    AnalyzerJob chain instead of replaying the same probe through the
    interactive tool-call table.  Only controlled reproductions with complete
    provenance may use this bridge; ordinary diagnoses still require real
    completed tool calls.
    """
    if str(diagnosis.mode or "").upper() != "REPRODUCTION":
        return []
    evidence_ids = list(report.evidence_refs_json or [])
    if not evidence_ids:
        return []
    rows = (
        session.query(DropInsightEvidenceModel)
        .filter(
            DropInsightEvidenceModel.diagnosis_id == diagnosis.id,
            DropInsightEvidenceModel.id.in_(evidence_ids),
        )
        .all()
    )
    if len(rows) != len(set(evidence_ids)):
        return []

    route: list[str] = []
    for row in rows:
        try:
            envelope = EvidenceEnvelope.model_validate(row.envelope_json or {})
        except ValidationError:
            return []
        metadata = dict((envelope.observation or {}).get("metadata") or {})
        source = envelope.source
        provenance = (
            source.task_id,
            source.task_attempt_id,
            source.artifact_id,
            source.artifact_sha256,
            source.analysis_job_id,
            source.analyzer_type,
            source.analyzer_version,
            source.analyzer_output_schema_version,
        )
        if not metadata.get("campaign_run_id") or not all(provenance):
            return []
        if (
            envelope.quality.level != "HIGH"
            or envelope.quality.degraded
            or not envelope.quality.target_match
            or not envelope.quality.time_overlap
            or not envelope.quality.schema_valid
            or not envelope.quality.analyzer_validated
        ):
            return []
        mapped = _CAMPAIGN_SOURCE_TOOL.get(source.tool_name.strip().lower())
        if mapped:
            route.append(mapped)
    return list(dict.fromkeys(route))


def _route_compatible(category: str, baseline_tool: str, target: dict) -> tuple[bool, dict]:
    baseline_category = _TOOL_CATEGORY.get(baseline_tool)
    if (
        baseline_category
        and baseline_category not in _GENERIC_ROUTE_CATEGORIES
        and baseline_category != category
    ):
        return False, {
            "route_conflict": "baseline_tool",
            "baseline_category": baseline_category,
            "skill_category": category,
        }

    subsystem = str(target.get("suspected_subsystem") or "").strip().lower()
    subsystem_category = _SUBSYSTEM_CATEGORY.get(subsystem)
    if subsystem_category and subsystem_category != category:
        return False, {
            "route_conflict": "suspected_subsystem",
            "subsystem": subsystem,
            "subsystem_category": subsystem_category,
            "skill_category": category,
        }
    return True, {}


def _tool_available(tool_name: str, target: dict) -> bool:
    required = _TOOL_REQUIRED_CAPABILITIES.get(tool_name, set())
    denied = {str(item) for item in (target.get("permission_denied") or [])}
    if required.intersection(denied):
        return False
    if "collector_capabilities" not in target:
        return True
    available = {str(item) for item in (target.get("collector_capabilities") or [])}
    return required.issubset(available)


def _validate_report_evidence(session, diagnosis_id: str, report: DropInsightReportModel) -> None:
    supporting_refs = list(report.evidence_refs_json or [])
    counter_refs = list(report.counter_evidence_refs_json or [])
    refs = supporting_refs + counter_refs
    if not supporting_refs:
        raise ValueError("报告没有可信 evidence 引用，不能沉淀技能")
    if len(refs) != len(set(refs)):
        raise ValueError("报告 evidence 引用重复，无法确认 provenance")

    rows = (
        session.query(DropInsightEvidenceModel)
        .filter(DropInsightEvidenceModel.id.in_(refs))
        .all()
    )
    by_id = {row.id: row for row in rows}
    missing = [ref for ref in refs if ref not in by_id]
    if missing:
        raise ValueError(f"evidence 引用不存在或不可追溯: {missing}")

    for ref in refs:
        evidence = by_id[ref]
        if evidence.diagnosis_id != diagnosis_id:
            raise ValueError(f"evidence provenance 与诊断不匹配: {ref}")
        if evidence.hypothesis_id != report.hypothesis_id:
            raise ValueError(f"evidence provenance 与报告假设不匹配: {ref}")
        try:
            envelope = EvidenceEnvelope.model_validate(evidence.envelope_json)
        except ValidationError as exc:
            raise ValueError(f"evidence envelope integrity 校验失败: {ref}") from exc
        if envelope.evidence_id != ref or envelope.diagnosis_id != diagnosis_id:
            raise ValueError(f"evidence envelope provenance 不匹配: {ref}")

        computed = classify_evidence(envelope)
        stored = evidence.classification_json or {}
        if (
            computed.get("decision") != "ACCEPT_SUPPORT"
            or computed.get("can_support_conclusion") is not True
            or stored.get("decision") != "ACCEPT_SUPPORT"
            or stored.get("can_support_conclusion") is not True
        ):
            raise ValueError(f"evidence provenance 或 integrity 不足以支持结论: {ref}")


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
    """Extract a candidate strategy from a verified, evidence-backed report.

    Candidate generation is automatic so the diagnosis workspace can make
    learning visible as soon as a trustworthy investigation finishes.  Human
    approval is still required by the publish boundary before later incidents
    are allowed to reuse the strategy.
    """
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
        _validate_report_evidence(session, diagnosis_id, report)
        # PostgreSQL's JSON type has no equality operator.  Keep this lookup
        # portable across PostgreSQL and SQLite by narrowing in SQL and
        # comparing the small source-id lists in Python.
        existing_candidate = next(
            (
                item
                for item in session.query(DiagnosticSkillModel)
                .order_by(DiagnosticSkillModel.version.desc())
                .all()
                if list(item.source_diagnosis_ids_json or []) == [diagnosis_id]
            ),
            None,
        )
        if existing_candidate is not None:
            return existing_candidate.to_dict()
        calls = (
            session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightToolCallModel.created_at.asc())
            .all()
        )
        completed_calls = [item for item in calls if item.status == "COMPLETED"]
        route = list(dict.fromkeys(item.tool_name for item in completed_calls))
        route_source = "TOOL_CALL"
        if not route:
            route = _campaign_probe_route(session, diagnosis, report)
            if route:
                route_source = "CAMPAIGN_TRUST_CHAIN"
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
        hypotheses = (
            session.query(DropInsightHypothesisModel)
            .filter(DropInsightHypothesisModel.diagnosis_id == diagnosis_id)
            .order_by(
                DropInsightHypothesisModel.round_index.asc(),
                DropInsightHypothesisModel.created_at.asc(),
            )
            .all()
        )
        exploration = _actual_exploration(hypotheses, calls, report)
        if not exploration["actual_route"] and route:
            exploration["actual_route"] = route
            exploration["summary"]["tool_calls"] = len(route)
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
                "route_source": route_source,
                "minimum_evidence": max(1, len(report.evidence_refs_json or [])),
                "confidence_floor": (
                    report.confidence
                    if report.confidence <= 1
                    else report.confidence / 1000
                ),
                "stop_rule": "VERIFIED_REPORT_OR_EXHAUSTED_SAFE_PROBES",
                "refutation_rule": "COUNTER_EVIDENCE_OVERRIDES_ROUTE_PRIOR",
                "actual_exploration": exploration,
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
        misleading_tool = next(
            (
                tool_name
                for tool_name, tool_category in _TOOL_CATEGORY.items()
                if tool_category not in _GENERIC_ROUTE_CATEGORIES
                and tool_category != skill.category
            ),
            "collect_sys_metrics",
        )
        negative_pass, negative_reason = _route_compatible(
            skill.category, misleading_tool, source_target
        )
        negative_score = 0 if not negative_pass else positive_score
        negative_pass = not negative_pass
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
            ("MISLEADING_NEGATIVE", negative_pass, 1000 if negative_pass else 0, {"match_score": negative_score, "match_reason": negative_reason, "expected": "ABSTAIN"}),
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
    baseline_tool = plan["tool_name"]
    compatible, _ = _route_compatible(category, baseline_tool, target)
    if not compatible:
        return None

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
            (
                tool_name
                for tool_name in route
                if tool_name not in completed_tools and _tool_available(tool_name, target)
            ),
            None,
        )
        if selected_tool is None:
            return None
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
