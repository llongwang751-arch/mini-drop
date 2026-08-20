"""统一诊断案例的只读兼容视图。

该模块不会修改 v1 集群诊断或 v2 Drop Insight 的存储结构，只把两种
会话投影为稳定的 ``DiagnosticCase`` 视图，供统一入口和评测中心读取。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable


def canonical_diagnosis_status(native_status: Any) -> str:
    status = str(native_status or "").strip().upper()
    groups = {
        "CREATED": {"CREATED", "PENDING", "UNDERSTANDING", "NEEDS_CLARIFICATION", "NEEDS_SCOPE_CONFIRMATION"},
        "PLANNING": {"PLANNING", "HYPOTHESIZING", "PLAN_READY"},
        "COLLECTING": {"COLLECTING", "RUNNING", "EXECUTING", "PROBING"},
        "ANALYZING": {"ANALYZING", "REPORTING", "VERIFYING"},
        "WAITING_APPROVAL": {"WAITING_APPROVAL", "WAITING_FOR_APPROVAL", "APPROVAL_REQUIRED"},
        "COMPLETED": {"COMPLETED", "DONE", "SUCCEEDED"},
        "PARTIAL": {"PARTIAL_COMPLETED", "PARTIAL", "INSUFFICIENT_EVIDENCE"},
        "FAILED": {"FAILED", "ERROR", "TIMED_OUT"},
        "CANCELLED": {"CANCELLED", "CANCELED"},
    }
    return next((canonical for canonical, values in groups.items() if status in values), "UNKNOWN")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def adapt_cluster_diagnosis(
    payload: dict[str, Any],
    *,
    include_native: bool = False,
) -> dict[str, Any]:
    """把 v1 AI 集群诊断会话映射为 DiagnosticCase。"""

    intent = payload.get("normalized_intent") or {}
    scope = payload.get("target_scope") or {}
    conclusions = payload.get("conclusion_versions") or []
    evidence = payload.get("evidence") or []
    result: dict[str, Any] = {
        "case_id": payload.get("diagnosis_id"),
        "diagnosis_id": payload.get("diagnosis_id"),
        "source": "cluster_diagnosis_v1",
        "strategy": intent.get("analysis_strategy") or "CLUSTER_TOPOLOGY",
        "query": payload.get("raw_query", ""),
        "status": payload.get("status"),
        "canonical_status": canonical_diagnosis_status(payload.get("status")),
        "target": scope,
        "time_range": (
            payload.get("effective_time_range")
            or payload.get("requested_time_range")
            or {}
        ),
        "budget": {
            "policy_profile": payload.get("policy_profile"),
            "risk": payload.get("risk_budget") or {},
            "resource": payload.get("resource_budget") or {},
            "used": payload.get("budget_used") or {},
        },
        "hypothesis_count": len(
            (payload.get("hypothesis_graph") or {}).get("hypotheses")
            or (payload.get("hypothesis_graph") or {}).get("nodes", [])
        ),
        "evidence_count": len(evidence),
        "report_version_count": len(conclusions),
        "task_ids": payload.get("child_task_ids") or [],
        "created_at": _iso(payload.get("created_at")),
        "updated_at": _iso(payload.get("updated_at")),
        "legacy_links": {
            "detail": f"/api/v1/diagnoses/{payload.get('diagnosis_id')}",
        },
    }
    if include_native:
        result["native_payload"] = payload
    return result


def adapt_drop_insight(
    payload: dict[str, Any],
    *,
    include_native: bool = False,
) -> dict[str, Any]:
    """把 v2 Drop Insight 会话映射为 DiagnosticCase。"""

    result: dict[str, Any] = {
        "case_id": payload.get("diagnosis_id"),
        "diagnosis_id": payload.get("diagnosis_id"),
        "source": "drop_insight_v2",
        "strategy": "EVIDENCE_HYPOTHESIS",
        "query": payload.get("query", ""),
        "status": payload.get("status"),
        "canonical_status": canonical_diagnosis_status(payload.get("status")),
        "target": payload.get("target") or {},
        "time_range": payload.get("time_range") or {},
        "budget": payload.get("budget") or {},
        "hypothesis_count": len(payload.get("hypotheses") or []),
        "evidence_count": len(payload.get("evidence") or []),
        "report_version_count": len(payload.get("reports") or []),
        "task_ids": payload.get("task_ids") or [],
        "created_at": _iso(payload.get("created_at")),
        "updated_at": _iso(payload.get("updated_at")),
        "legacy_links": {
            "detail": f"/api/v2/diagnoses/{payload.get('diagnosis_id')}",
            "events": f"/api/v2/diagnoses/{payload.get('diagnosis_id')}/events",
        },
    }
    if include_native:
        result["native_payload"] = payload
    return result


def adapt_legacy_rca(
    payload: dict[str, Any],
    *,
    include_native: bool = False,
) -> dict[str, Any]:
    """把早期的单任务 RCA 结果投影为统一 DiagnosticCase。"""

    run = payload.get("run") or payload
    diagnosis_id = run.get("id") or run.get("diagnosis_id")
    task_id = run.get("task_id")
    tool_results = payload.get("tool_results") or []
    reports = payload.get("reports") or ([] if not payload.get("report") else [payload["report"]])
    status = run.get("status")
    result: dict[str, Any] = {
        "case_id": diagnosis_id,
        "diagnosis_id": diagnosis_id,
        "source": "legacy_rca",
        "strategy": "RULE_LLM_RCA",
        "query": run.get("summary") or "",
        "status": status,
        "canonical_status": canonical_diagnosis_status(status),
        "target": {"task_id": task_id},
        "time_range": {},
        "budget": {},
        "hypothesis_count": 0,
        "evidence_count": len(tool_results),
        "report_version_count": len(reports),
        "task_ids": [task_id] if task_id else [],
        "created_at": _iso(run.get("created_at")),
        "updated_at": _iso(run.get("finished_at") or run.get("created_at")),
        "legacy_links": {
            "detail": f"/api/diagnoses/{diagnosis_id}",
            "task": f"/api/tasks/{task_id}" if task_id else None,
        },
    }
    if include_native:
        result["native_payload"] = payload
    return result


def merge_diagnostic_cases(
    cluster_items: Iterable[dict[str, Any]],
    insight_items: Iterable[dict[str, Any]],
    *,
    legacy_items: Iterable[dict[str, Any]] = (),
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """合并并按更新时间倒序分页，不改变任一旧会话。"""

    cases = [
        *(adapt_cluster_diagnosis(item) for item in cluster_items),
        *(adapt_drop_insight(item) for item in insight_items),
        *(adapt_legacy_rca(item) for item in legacy_items),
    ]
    cases.sort(
        key=lambda item: item.get("updated_at") or item.get("created_at") or "",
        reverse=True,
    )
    total = len(cases)
    return {
        "items": cases[offset : offset + limit],
        "total": total,
        "limit": limit,
        "offset": offset,
        "compatibility": {
            "v1_preserved": True,
            "v2_preserved": True,
            "legacy_rca_preserved": True,
            "write_mode": "native_api_only",
        },
    }
