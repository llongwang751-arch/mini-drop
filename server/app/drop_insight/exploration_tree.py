"""Build restart-safe snapshots of the live diagnosis exploration tree."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from server.app.database import new_session
from server.app.models import (
    DropInsightEvidenceModel,
    DropInsightEventModel,
    DropInsightHypothesisModel,
    DropInsightReportModel,
    DropInsightSessionModel,
    DropInsightToolCallModel,
    FixVerificationModel,
)


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
}
_TOOL_DOMAIN = {
    "collect_sys_metrics": ("SYSTEM_RESOURCE", "系统基线"),
    "start_perf_profile": ("CPU_HOTSPOT", "CPU"),
    "start_pyspy_profile": ("PYTHON_RUNTIME", "Python"),
    "start_ebpf_io_profile": ("IO_LATENCY", "I/O"),
    "collect_database_diagnostics": ("DATABASE_LOCK", "数据库"),
    "get_agent_status": ("AGENT_HEALTH", "采集节点"),
}
_TOOL_LABEL = {
    "collect_sys_metrics": "采集系统基线",
    "start_perf_profile": "采集 CPU 火焰图",
    "start_pyspy_profile": "采集 Python 调用栈",
    "start_ebpf_io_profile": "采集 I/O 延迟分布",
    "collect_database_diagnostics": "采集数据库锁与会话",
    "get_agent_status": "检查采集节点",
}


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _clip(value: Any, limit: int = 140) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


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
        ("hypothesis_id", "hypothesis:"),
        ("tool_call_id", "tool:"),
        ("evidence_id", "evidence:"),
        ("report_id", "report:"),
        ("verification_id", "fix:"),
    )
    return [f"{prefix}{payload[key]}" for key, prefix in candidates if payload.get(key)]


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
        round_by_hypothesis = {item.id: item.round_index or 1 for item in hypotheses}
        for hypothesis in hypotheses:
            report = report_by_hypothesis.get(hypothesis.id)
            related_evidence = evidence_by_hypothesis.get(hypothesis.id, [])
            related_tools = tools_by_hypothesis.get(hypothesis.id, [])
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
                    "domain": f"第 {hypothesis.round_index or 1} 轮假设",
                    "round_index": hypothesis.round_index or 1,
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
                switches.append(
                    {
                        "from": previous[1],
                        "to": current_label,
                        "from_key": previous[0],
                        "to_key": current_key,
                        "reason": "上一方向证据不足或被反证，转向新的取证域",
                    }
                )
            previous = (current_key, current_label)

        rounds: list[dict[str, Any]] = []
        for round_index in sorted({item.round_index or 1 for item in hypotheses}):
            round_hypotheses = [item for item in hypotheses if (item.round_index or 1) == round_index]
            round_ids = {item.id for item in round_hypotheses}
            round_tools = [item for item in tools if item.hypothesis_id in round_ids]
            round_evidence = [item for item in evidence_rows if item.hypothesis_id in round_ids]
            rounds.append(
                {
                    "round_index": round_index,
                    "title": _clip(round_hypotheses[0].statement) if round_hypotheses else "",
                    "hypothesis_count": len(round_hypotheses),
                    "tool_call_count": len(round_tools),
                    "evidence_count": len(round_evidence),
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
        return {
            "diagnosis_id": diagnosis_id,
            "revision": last_event.sequence if last_event else 0,
            "status": diagnosis.status,
            "updated_at": _iso(diagnosis.updated_at),
            "last_event": last_event.to_dict() if last_event else None,
            "active_node_ids": _active_node_ids(last_event),
            "nodes": nodes,
            "switches": switches,
            "rounds": rounds,
            "stats": {
                "rounds": len(rounds),
                "nodes": len(nodes),
                "tool_calls": len(tools),
                "evidence": len(evidence_rows),
                "pruned": sum(item["state"] == "refuted" for item in nodes),
                "current_round": max([item["round_index"] for item in rounds] or [0]),
            },
        }
    finally:
        session.close()
