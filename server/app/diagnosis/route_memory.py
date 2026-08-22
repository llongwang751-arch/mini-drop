"""受控诊断路线记忆。

这里只学习“哪类症状、哪种运行时下，哪个已注册探针更容易产出有效证据”，
不学习命令，也不允许历史经验绕过能力检查、风险审批和诊断预算。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


TERMINAL_OUTCOME_WEIGHT = {
    "COMPLETED": 1.0,
    "PARTIAL_COMPLETED": 0.55,
    "INSUFFICIENT_EVIDENCE": 0.1,
    "BUDGET_EXHAUSTED": 0.05,
    "FAILED": 0.0,
}


def build_contextual_route_memory(
    sessions: list[dict[str, Any]],
    probes_for_session: Callable[[str], list[dict[str, Any]]],
    *,
    symptom: str,
    runtime: str,
    exclude_diagnosis_id: str | None = None,
) -> dict[str, Any]:
    """计算带上下文的探针成功先验，并返回可展示的学习摘要。"""

    attempts: dict[str, float] = {}
    successes: dict[str, float] = {}
    supporting_sessions: dict[str, set[str]] = {}

    for session in sessions:
        diagnosis_id = str(session.get("diagnosis_id") or session.get("id") or "")
        if not diagnosis_id or diagnosis_id == exclude_diagnosis_id:
            continue
        status = str(session.get("status", ""))
        if status not in TERMINAL_OUTCOME_WEIGHT:
            continue

        intent = session.get("normalized_intent") or {}
        historical_symptom = str(intent.get("symptom", ""))
        historical_runtime = _session_runtime(session)
        context_weight = 0.35
        if symptom and historical_symptom == symptom:
            context_weight += 1.0
        if runtime not in {"", "unknown"} and historical_runtime == runtime:
            context_weight += 0.65

        conclusions = session.get("conclusion_versions") or []
        latest = conclusions[-1] if conclusions else {}
        coverage = latest.get("coverage") or {}
        evidence_count = int(coverage.get("evidence_count", 0) or 0)
        evidence_yield = min(1.0, evidence_count / 3.0)
        outcome = TERMINAL_OUTCOME_WEIGHT[status] * (0.65 + 0.35 * evidence_yield)

        for probe in probes_for_session(diagnosis_id):
            probe_id = str(probe.get("probe_id", ""))
            if not probe_id:
                continue
            attempts[probe_id] = attempts.get(probe_id, 0.0) + context_weight
            if probe.get("status") == "COMPLETED":
                successes[probe_id] = successes.get(probe_id, 0.0) + context_weight * outcome
                if outcome >= 0.5:
                    supporting_sessions.setdefault(probe_id, set()).add(diagnosis_id)

    # Beta(1, 1) 平滑，避免只有一次成功就被误认为 100% 可靠。
    priors = {
        probe_id: round((successes.get(probe_id, 0.0) + 1.0) / (weight + 2.0), 4)
        for probe_id, weight in attempts.items()
    }
    ranked = sorted(priors, key=lambda item: (-priors[item], item))
    return {
        "symptom": symptom or "unknown",
        "runtime": runtime or "unknown",
        "priors": priors,
        "ranked_routes": [
            {
                "probe_id": probe_id,
                "success_prior": priors[probe_id],
                "supporting_session_count": len(supporting_sessions.get(probe_id, set())),
            }
            for probe_id in ranked
        ],
        "safety_boundary": "历史路线仅参与排序；能力检查、预算和审批仍由服务端强制执行。",
    }


def _session_runtime(session: dict[str, Any]) -> str:
    scope = session.get("target_scope") or {}
    instances = scope.get("instances") or []
    runtimes = {str(item.get("runtime", "unknown")) for item in instances if isinstance(item, dict)}
    runtimes.discard("unknown")
    return sorted(runtimes)[0] if len(runtimes) == 1 else "unknown"
