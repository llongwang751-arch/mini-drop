"""Build restart-safe snapshots of the live diagnosis exploration tree."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from server.app.database import new_session
from server.app.models import (
    DiagnosticSkillActivationModel,
    DiagnosticSkillModel,
    DropInsightEvidenceModel,
    DropInsightEventModel,
    DropInsightHypothesisModel,
    DropInsightReportModel,
    DropInsightSessionModel,
    DropInsightToolCallModel,
    FixVerificationModel,
)

from .lats import build_search_projection
from .rounds import effective_round_by_hypothesis


_REFUTED = {
    "COUNTER",
    "REFUTED",
    "FALSIFIED",
    "DISPROVED",
    "RULED_OUT",
    "REJECTED",
    "FAILED",
    "CANCELLED",
    "DENIED",
    "DEPRIORITIZED",
}
_TOOL_DOMAIN = {
    "collect_sys_metrics": ("SYSTEM_RESOURCE", "系统基线"),
    "start_perf_profile": ("CPU_HOTSPOT", "CPU"),
    "start_pyspy_profile": ("PYTHON_RUNTIME", "Python"),
    "start_ebpf_io_profile": ("IO_LATENCY", "I/O"),
    "start_jvm_profile": ("JVM_RUNTIME", "JVM"),
    "collect_memory_profile": ("MEMORY_PRESSURE", "内存"),
    "collect_go_profile": ("GO_RUNTIME", "Go"),
    "start_continuous_profile": ("CPU_TREND", "连续 CPU"),
    "collect_database_diagnostics": ("DATABASE_LOCK", "数据库"),
    "get_agent_status": ("AGENT_HEALTH", "采集节点"),
}
_TOOL_LABEL = {
    "collect_sys_metrics": "采集系统基线",
    "start_perf_profile": "采集 CPU 火焰图",
    "start_pyspy_profile": "采集 Python 调用栈",
    "start_ebpf_io_profile": "采集 I/O 延迟分布",
    "start_jvm_profile": "采集 JVM 调用栈",
    "collect_memory_profile": "采集进程内存剖面",
    "collect_go_profile": "采集 Go pprof",
    "start_continuous_profile": "采集连续 CPU 剖面",
    "collect_database_diagnostics": "采集数据库锁与会话",
    "get_agent_status": "检查采集节点",
}


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _clip(value: Any, limit: int = 140) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _positive_rounds(items: list[dict[str, Any]]) -> list[int]:
    rounds: set[int] = set()
    for item in items:
        try:
            value = int(item.get("round_index") or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            rounds.add(value)
    return sorted(rounds)


def _decision(row: DropInsightEvidenceModel) -> str:
    return str((row.classification_json or {}).get("decision") or "UNKNOWN").upper()


def _evidence_summary(row: DropInsightEvidenceModel) -> str:
    envelope = row.envelope_json or {}
    observation = envelope.get("observation") or {}
    if isinstance(observation, dict):
        for key in ("summary", "finding", "message", "symbol", "hotspot"):
            if observation.get(key):
                return _clip(observation[key])
        metadata = observation.get("metadata") or {}
        if isinstance(metadata, dict):
            for key in ("summary", "finding", "reason"):
                if metadata.get(key):
                    return _clip(metadata[key])
    return _clip(envelope.get("evidence_type") or row.role or "证据已入库")


def _report_verified(row: DropInsightReportModel) -> bool:
    verification = row.verification_json or {}
    return verification.get("status") in {"VERIFIED", "PARTIAL_WITHOUT_COUNTER"}


def _hypothesis_state(
    hypothesis: DropInsightHypothesisModel,
    evidence: list[DropInsightEvidenceModel],
    report: DropInsightReportModel | None,
    has_tool: bool,
) -> str:
    if report is not None and _report_verified(report) and report.confidence >= 600:
        return "confirmed"
    status = str(hypothesis.status or "OPEN").upper()
    decisions = {_decision(item) for item in evidence}
    if status in _REFUTED or "ACCEPT_COUNTER" in decisions:
        return "refuted"
    if has_tool or evidence or status in {"SUPPORTED", "INCONCLUSIVE"}:
        return "visited"
    return "unvisited"


def _active_node_ids(last_event: DropInsightEventModel | None) -> list[str]:
    if last_event is None:
        return []
    payload = last_event.payload_json or {}
    candidates = (
        # LATS event payloads already carry the public ``hypothesis:`` prefix.
        # An empty prefix avoids manufacturing ``hypothesis:hypothesis:...``.
        ("node_id", ""),
        ("intervention_id", "intervention:"),
        ("hypothesis_id", "hypothesis:"),
        ("tool_call_id", "tool:"),
        ("evidence_id", "evidence:"),
        ("report_id", "report:"),
        ("verification_id", "fix:"),
    )
    return [f"{prefix}{payload[key]}" for key, prefix in candidates if payload.get(key)]


def _skill_trace_item(
    activation: DiagnosticSkillActivationModel,
    skill: DiagnosticSkillModel | None,
) -> dict[str, Any]:
    """Project a compact, replay-safe view of one Skill activation."""

    reason = dict(activation.match_reason_json or {})
    instructions = dict(reason.get("skill_instructions") or {})
    trace = [
        dict(item)
        for item in (
            reason.get("reuse_trace")
            or reason.get("applications")
            or []
        )
        if isinstance(item, dict)
    ]
    if not trace:
        trace = [
            {
                "trace_key": "legacy:activation",
                "round_index": 1,
                "phase": "LEGACY_ACTIVATION",
                "baseline_tool": activation.baseline_tool,
                "selected_tool": activation.selected_tool,
                "category_before": reason.get("requested_category"),
                "category_after": reason.get("skill_category") or (
                    skill.category if skill else None
                ),
                "category_corrected": bool(reason.get("category_correction")),
                "applied_at": _iso(activation.created_at),
            }
        ]
    trace.sort(
        key=lambda item: (
            int(item.get("round_index") or 0),
            str(item.get("phase") or ""),
        )
    )
    route = [str(item) for item in (reason.get("route") or []) if str(item)]
    skill_category = reason.get("skill_category") or (
        skill.category if skill else None
    )
    source_sha256 = instructions.get("source_sha256") or instructions.get(
        "content_sha256"
    )
    return {
        "activation_id": activation.id,
        "skill_id": activation.skill_id,
        "skill_name": instructions.get("name") or (skill.family_key if skill else None),
        "skill_version": reason.get("skill_version") or (skill.version if skill else None),
        "category": skill_category,
        "skill_category": skill_category,
        "state": str(trace[-1].get("state") or "ACTIVE").upper(),
        "summary": _clip(instructions.get("summary") or "已发布诊断路线", 240),
        "source_path": instructions.get("source_path"),
        "source_sha256": source_sha256,
        "content_sha256": instructions.get("content_sha256") or source_sha256,
        "instruction_sha256": source_sha256,
        "load_mode": instructions.get("load_mode"),
        "loaded_sections": list(instructions.get("loaded_sections") or []),
        "trust": instructions.get("trust"),
        "instruction_trust": instructions.get("trust"),
        "match_score": activation.match_score / 1000,
        "retrieval": reason.get("retrieval"),
        "matched_terms": list(reason.get("matched_terms") or []),
        "category_correction": reason.get("category_correction"),
        "baseline_tool": activation.baseline_tool,
        "selected_tool": activation.selected_tool,
        "probe_order": route,
        "outcome": activation.outcome,
        "reuse_trace": trace,
        "created_at": _iso(activation.created_at),
        "updated_at": _iso(activation.updated_at),
    }


def _skill_route_overlay(
    skill_trace: list[dict[str, Any]],
    tools: list[DropInsightToolCallModel],
) -> dict[str, Any]:
    """Join planned Skill routes to observed tools without inventing causality."""

    observed_by_name: dict[str, list[DropInsightToolCallModel]] = defaultdict(list)
    for tool in tools:
        observed_by_name[tool.tool_name].append(tool)
    routes = []
    for activation in skill_trace:
        selected_by_step: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for step in activation.get("reuse_trace") or []:
            selected_by_step[str(step.get("selected_tool") or "")].append(step)
        steps = []
        for index, tool_name in enumerate(activation.get("probe_order") or [], start=1):
            observed = observed_by_name.get(tool_name, [])
            selected_steps = selected_by_step.get(tool_name, [])
            steps.append(
                {
                    "route_index": index,
                    "tool": tool_name,
                    "selected_by_skill": bool(selected_steps),
                    "selected_rounds": _positive_rounds(selected_steps),
                    # Name-based correlation is explicitly labelled observed;
                    # ToolCall has no activation FK, so this is not attribution.
                    "observed_tool_call_ids": [item.id for item in observed],
                    "observed_statuses": [str(item.status or "UNKNOWN") for item in observed],
                }
            )
        routes.append(
            {
                "activation_id": activation["activation_id"],
                "skill_id": activation["skill_id"],
                "skill_version": activation.get("skill_version"),
                "summary": activation.get("summary"),
                "steps": steps,
            }
        )
    return {
        "correlation": "TOOL_NAME_OBSERVATION_NOT_CAUSAL_ATTRIBUTION",
        "routes": routes,
    }


def get_live_exploration_tree(diagnosis_id: str) -> dict[str, Any] | None:
    """Derive one current tree from durable diagnosis facts."""

    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if diagnosis is None:
            return None
        hypotheses = (
            session.query(DropInsightHypothesisModel)
            .filter(DropInsightHypothesisModel.diagnosis_id == diagnosis_id)
            .order_by(
                DropInsightHypothesisModel.round_index.asc(),
                DropInsightHypothesisModel.created_at.asc(),
            )
            .all()
        )
        tools = (
            session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightToolCallModel.created_at.asc())
            .all()
        )
        evidence_rows = (
            session.query(DropInsightEvidenceModel)
            .filter(DropInsightEvidenceModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightEvidenceModel.created_at.asc())
            .all()
        )
        reports = (
            session.query(DropInsightReportModel)
            .filter(DropInsightReportModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightReportModel.created_at.asc())
            .all()
        )
        fixes = (
            session.query(FixVerificationModel)
            .filter(FixVerificationModel.diagnosis_id == diagnosis_id)
            .order_by(FixVerificationModel.created_at.asc())
            .all()
        )
        events = (
            session.query(DropInsightEventModel)
            .filter(DropInsightEventModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightEventModel.sequence.asc())
            .all()
        )
        skill_activations = (
            session.query(DiagnosticSkillActivationModel)
            .filter(DiagnosticSkillActivationModel.diagnosis_id == diagnosis_id)
            .order_by(DiagnosticSkillActivationModel.created_at.asc())
            .all()
        )
        skill_ids = {item.skill_id for item in skill_activations}
        skill_by_id = {
            item.id: item
            for item in (
                session.query(DiagnosticSkillModel)
                .filter(DiagnosticSkillModel.id.in_(skill_ids))
                .all()
                if skill_ids
                else []
            )
        }
        skill_trace = [
            _skill_trace_item(item, skill_by_id.get(item.skill_id))
            for item in skill_activations
        ]
        skill_overlay = _skill_route_overlay(skill_trace, tools)
        skill_route_refs_by_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for route in skill_overlay["routes"]:
            for step in route["steps"]:
                skill_route_refs_by_tool[step["tool"]].append(
                    {
                        "activation_id": route["activation_id"],
                        "skill_id": route["skill_id"],
                        "skill_version": route.get("skill_version"),
                        "route_index": step["route_index"],
                        "selected_by_skill": step["selected_by_skill"],
                    }
                )

        evidence_by_hypothesis: dict[str, list[DropInsightEvidenceModel]] = defaultdict(list)
        tools_by_hypothesis: dict[str, list[DropInsightToolCallModel]] = defaultdict(list)
        report_by_hypothesis: dict[str, DropInsightReportModel] = {}
        for item in evidence_rows:
            if item.hypothesis_id:
                evidence_by_hypothesis[item.hypothesis_id].append(item)
        for item in tools:
            if item.hypothesis_id:
                tools_by_hypothesis[item.hypothesis_id].append(item)
        for item in reports:
            if item.hypothesis_id:
                report_by_hypothesis[item.hypothesis_id] = item

        verified_report = next((item for item in reversed(reports) if _report_verified(item)), None)
        verified_hypothesis_id = verified_report.hypothesis_id if verified_report else None
        root_id = f"diagnosis:{diagnosis_id}"
        nodes: list[dict[str, Any]] = [
            {
                "id": root_id,
                "parent_id": None,
                "kind": "diagnosis",
                "title": _clip(diagnosis.query, 180),
                "state": "confirmed" if verified_report else "visited" if hypotheses else "unvisited",
                "status": diagnosis.status,
                "domain": "诊断入口",
                "round_index": 0,
                "evidence": "探索树随诊断事件实时生长",
                "changed_at": _iso(diagnosis.updated_at),
            }
        ]

        hypothesis_ids = {item.id for item in hypotheses}
        birth_round_by_hypothesis = {
            item.id: item.round_index or 1 for item in hypotheses
        }
        round_by_hypothesis = effective_round_by_hypothesis(
            events,
            birth_round_by_hypothesis,
        )
        intervention_events = [
            item
            for item in events
            if item.event_type == "diagnosis.intervention_submitted"
        ]
        intervention_by_revision: dict[str, dict[str, Any]] = {}
        for event in intervention_events:
            payload = event.payload_json or {}
            intervention_id = payload.get("intervention_id")
            if not intervention_id:
                continue
            revision_id = payload.get("revision_hypothesis_id")
            if revision_id:
                intervention_by_revision[str(revision_id)] = payload
            parent_hypothesis_id = payload.get("hypothesis_id")
            nodes.append(
                {
                    "id": f"intervention:{intervention_id}",
                    "parent_id": (
                        f"hypothesis:{parent_hypothesis_id}"
                        if parent_hypothesis_id in hypothesis_ids
                        else root_id
                    ),
                    "kind": "intervention",
                    "title": _clip(payload.get("message") or "用户补充诊断上下文", 180),
                    "state": "visited",
                    "status": payload.get("action") or "ADD_CONTEXT",
                    "domain": "人工干预",
                    "round_index": int(payload.get("round_index") or 1),
                    "evidence": "用户输入只改变探索方向，不会被当作事实证据",
                    "changed_at": _iso(event.occurred_at),
                }
            )
        for hypothesis in hypotheses:
            report = report_by_hypothesis.get(hypothesis.id)
            related_evidence = evidence_by_hypothesis.get(hypothesis.id, [])
            related_tools = tools_by_hypothesis.get(hypothesis.id, [])
            execution_round = round_by_hypothesis.get(
                hypothesis.id,
                hypothesis.round_index or 1,
            )
            intervention = intervention_by_revision.get(hypothesis.id)
            if intervention and intervention.get("intervention_id"):
                parent_id = f"intervention:{intervention['intervention_id']}"
            else:
                parent_id = (
                    f"hypothesis:{hypothesis.parent_hypothesis_id}"
                    if hypothesis.parent_hypothesis_id in hypothesis_ids
                    else root_id
                )
            nodes.append(
                {
                    "id": f"hypothesis:{hypothesis.id}",
                    "parent_id": parent_id,
                    "kind": "hypothesis",
                    "title": _clip(hypothesis.statement, 180),
                    "state": _hypothesis_state(
                        hypothesis, related_evidence, report, bool(related_tools)
                    ),
                    "status": hypothesis.status,
                    "domain": f"第 {execution_round} 轮假设",
                    "round_index": execution_round,
                    "tree_depth": hypothesis.round_index or 1,
                    "evidence": _clip(hypothesis.generation_reason or "等待工具证据验证或反证"),
                    "changed_at": _iso(hypothesis.updated_at),
                }
            )

        latest_tool_by_hypothesis: dict[str, str] = {}
        for index, tool in enumerate(tools, start=1):
            domain, domain_label = _TOOL_DOMAIN.get(tool.tool_name, ("GENERAL", "其他方向"))
            status = str(tool.status or "UNKNOWN").upper()
            state = "refuted" if status in _REFUTED else "visited"
            if tool.hypothesis_id == verified_hypothesis_id and status in {
                "COMPLETED",
                "TASK_CREATED",
                "RUNNING",
            }:
                state = "confirmed"
            parent_id = (
                f"hypothesis:{tool.hypothesis_id}"
                if tool.hypothesis_id in hypothesis_ids
                else root_id
            )
            node_id = f"tool:{tool.id}"
            nodes.append(
                {
                    "id": node_id,
                    "parent_id": parent_id,
                    "kind": "tool",
                    "title": f"{index}. {_TOOL_LABEL.get(tool.tool_name, tool.tool_name)}",
                    "state": state,
                    "status": status,
                    "domain": domain_label,
                    "domain_key": domain,
                    "round_index": round_by_hypothesis.get(tool.hypothesis_id, 1),
                    "tool": tool.tool_name,
                    "skill_route_refs": list(
                        skill_route_refs_by_tool.get(tool.tool_name, [])
                    ),
                    "evidence": _clip(tool.policy_reason or "策略门禁已完成"),
                    "changed_at": _iso(tool.executed_at or tool.decided_at or tool.created_at),
                }
            )
            if tool.hypothesis_id:
                latest_tool_by_hypothesis[tool.hypothesis_id] = node_id

        for evidence in evidence_rows:
            decision = _decision(evidence)
            if decision == "ACCEPT_SUPPORT":
                state = "confirmed" if evidence.hypothesis_id == verified_hypothesis_id else "visited"
            elif decision in {"ACCEPT_COUNTER", "REJECT", "REJECT_LOW_QUALITY"}:
                state = "refuted"
            else:
                state = "visited"
            parent_id = latest_tool_by_hypothesis.get(
                evidence.hypothesis_id or "",
                f"hypothesis:{evidence.hypothesis_id}"
                if evidence.hypothesis_id in hypothesis_ids
                else root_id,
            )
            nodes.append(
                {
                    "id": f"evidence:{evidence.id}",
                    "parent_id": parent_id,
                    "kind": "evidence",
                    "title": "支持证据" if decision == "ACCEPT_SUPPORT" else "反证或低质量证据",
                    "state": state,
                    "status": decision,
                    "domain": "证据裁决",
                    "round_index": round_by_hypothesis.get(evidence.hypothesis_id, 1),
                    "evidence": _evidence_summary(evidence),
                    "changed_at": _iso(evidence.created_at),
                }
            )

        for report in reports:
            verified = _report_verified(report) and report.confidence >= 600
            nodes.append(
                {
                    "id": f"report:{report.id}",
                    "parent_id": (
                        f"hypothesis:{report.hypothesis_id}"
                        if report.hypothesis_id in hypothesis_ids
                        else root_id
                    ),
                    "kind": "report",
                    "title": "根因结论" if verified else "阶段性结论",
                    "state": "confirmed" if verified else "refuted" if not report.evidence_refs_json else "visited",
                    "status": (report.verification_json or {}).get("status", "UNKNOWN"),
                    "domain": "结论",
                    "round_index": round_by_hypothesis.get(report.hypothesis_id, 1),
                    "evidence": _clip(report.conclusion, 180),
                    "changed_at": _iso(report.created_at),
                }
            )

        report_parent = f"report:{verified_report.id}" if verified_report else root_id
        for fix in fixes:
            nodes.append(
                {
                    "id": f"fix:{fix.id}",
                    "parent_id": report_parent,
                    "kind": "verification",
                    "title": "修复后复测",
                    "state": "confirmed" if fix.outcome == "VERIFIED" else "refuted",
                    "status": fix.outcome,
                    "domain": "恢复验证",
                    "round_index": max(round_by_hypothesis.values(), default=1),
                    "evidence": _clip(fix.fix_summary or "已完成修复前后对照"),
                    "changed_at": _iso(fix.created_at),
                }
            )

        switches: list[dict[str, Any]] = []
        previous = None
        for tool in tools:
            current_key, current_label = _TOOL_DOMAIN.get(tool.tool_name, ("GENERAL", "其他方向"))
            if previous and previous[0] != current_key:
                switch = {
                    "from": previous[1],
                    "to": current_label,
                    "from_key": previous[0],
                    "to_key": current_key,
                    "reason": "上一方向证据不足或被反证，转向新的取证域",
                }
                intervention = intervention_by_revision.get(tool.hypothesis_id or "")
                if intervention:
                    switch["reason"] = _clip(
                        intervention.get("message") or "用户要求切换诊断方向"
                    )
                    switch["source"] = "USER_INTERVENTION"
                    switch["intervention_id"] = intervention.get("intervention_id")
                switches.append(switch)
            previous = (current_key, current_label)

        rounds: list[dict[str, Any]] = []
        for round_index in sorted(set(round_by_hypothesis.values())):
            round_hypotheses = [
                item
                for item in hypotheses
                if round_by_hypothesis.get(item.id, item.round_index or 1)
                == round_index
            ]
            round_ids = {item.id for item in round_hypotheses}
            round_tools = [item for item in tools if item.hypothesis_id in round_ids]
            round_evidence = [item for item in evidence_rows if item.hypothesis_id in round_ids]
            round_interventions = [
                item
                for item in intervention_events
                if int((item.payload_json or {}).get("round_index") or 0) == round_index
            ]
            rounds.append(
                {
                    "round_index": round_index,
                    "title": _clip(round_hypotheses[0].statement) if round_hypotheses else "",
                    "hypothesis_count": len(round_hypotheses),
                    "tool_call_count": len(round_tools),
                    "evidence_count": len(round_evidence),
                    "intervention_count": len(round_interventions),
                    "status": (
                        "CONFIRMED"
                        if any(item.id == verified_hypothesis_id for item in round_hypotheses)
                        else "REFUTED"
                        if round_hypotheses
                        and all(str(item.status or "").upper() in _REFUTED for item in round_hypotheses)
                        else "INVESTIGATING"
                    ),
                }
            )

        last_event = events[-1] if events else None
        revision = last_event.sequence if last_event else 0
        search = build_search_projection(
            mode=diagnosis.mode,
            budget=diagnosis.budget_json or {},
            status=diagnosis.status,
            events=events,
            tool_calls_used=len(tools),
        )
        if search is not None:
            search_metrics = search.pop("node_metrics", {})
            for node in nodes:
                if node["kind"] == "hypothesis" and node["id"] in search_metrics:
                    node["search_metrics"] = search_metrics[node["id"]]
        response = {
            "diagnosis_id": diagnosis_id,
            "revision": revision,
            # Compatibility alias used by the persisted live A/B campaign
            # report. ``revision`` remains the canonical public name.
            "version": revision,
            "status": diagnosis.status,
            "updated_at": _iso(diagnosis.updated_at),
            "last_event": last_event.to_dict() if last_event else None,
            "active_node_ids": _active_node_ids(last_event),
            "nodes": nodes,
            "switches": switches,
            "rounds": rounds,
            "skill_trace": skill_trace,
            "skill_route_overlay": skill_overlay,
            "stats": {
                "rounds": len(rounds),
                "nodes": len(nodes),
                "tool_calls": len(tools),
                "evidence": len(evidence_rows),
                "human_interventions": len(intervention_events),
                "skill_activations": len(skill_trace),
                "skill_reuse_rounds": sum(
                    len(item.get("reuse_trace") or []) for item in skill_trace
                ),
                "pruned": sum(item["state"] == "refuted" for item in nodes),
                "current_round": max([item["round_index"] for item in rounds] or [0]),
            },
        }
        if search is not None:
            response["search"] = search
        return response
    finally:
        session.close()
