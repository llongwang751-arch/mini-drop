"""RCA 自动修复计划。

自动执行只覆盖 safe_auto 动作，例如创建二次采集任务。
高风险命令只作为建议进入计划，由用户审查后手工执行。
"""

from __future__ import annotations

from uuid import uuid4

from server.app.rca.models import DiagnosisReport, EvidenceInput, RepairAction, RepairPlan


SAFE_AUTO = "safe_auto"
CONFIRM_REQUIRED = "confirm_required"
MANUAL_ONLY = "manual_only"


def build_repair_plan(task_id: str, report: DiagnosisReport, evidence: EvidenceInput) -> RepairPlan:
    cause = report.ranked_causes[0] if report.ranked_causes else None
    cause_id = cause.cause_id if cause else "insufficient_data"
    actions: list[RepairAction] = []

    collector_type = evidence.task_metadata.get("collector_type", "unknown")
    agent_id = evidence.task_metadata.get("agent_id")
    target_pid = evidence.task_metadata.get("target_pid")

    if cause_id.startswith("cpu_hotspot") and collector_type != "pyspy" and agent_id and target_pid:
        actions.append(RepairAction(
            action_id=f"action_{uuid4().hex[:8]}",
            action_type="create_followup_task",
            risk_level=SAFE_AUTO,
            description="创建 py-spy 二次采集任务，验证热点是否位于 Python 用户态代码。",
            payload={
                "name": f"followup_pyspy_{task_id}",
                "agent_id": agent_id,
                "target_pid": target_pid,
                "collector_type": "pyspy",
                "sample_rate": 99,
                "duration_sec": max(int(evidence.task_metadata.get("duration_sec", 15)), 15),
                "options": {"source_diagnosis_task_id": task_id},
            },
        ))
        actions.append(RepairAction(
            action_id=f"action_{uuid4().hex[:8]}",
            action_type="code_change_suggestion",
            risk_level=MANUAL_ONLY,
            description="热点函数疑似计算密集，建议人工审查算法复杂度、缓存策略或循环边界。",
        ))

    if cause_id == "io_wait_high" and agent_id and target_pid:
        actions.append(RepairAction(
            action_id=f"action_{uuid4().hex[:8]}",
            action_type="create_followup_task",
            risk_level=SAFE_AUTO,
            description="创建 eBPF IO 二次采集任务，复核块设备延迟分布。",
            payload={
                "name": f"followup_ebpf_{task_id}",
                "agent_id": agent_id,
                "target_pid": target_pid,
                "collector_type": "ebpf_io",
                "sample_rate": 99,
                "duration_sec": max(int(evidence.task_metadata.get("duration_sec", 15)), 15),
                "options": {"source_diagnosis_task_id": task_id},
            },
        ))
        actions.append(RepairAction(
            action_id=f"action_{uuid4().hex[:8]}",
            action_type="system_tuning_suggestion",
            risk_level=MANUAL_ONLY,
            description="建议人工检查磁盘队列、IO 调度器、容器限速和底层存储吞吐。",
        ))
    elif cause_id == "io_wait_high":
        actions.append(RepairAction(
            action_id=f"action_{uuid4().hex[:8]}",
            action_type="collect_more_evidence",
            risk_level=MANUAL_ONLY,
            description="缺少明确的 Agent 或目标 PID，先确认采集范围；系统不会隐式扩大到 PID 1。",
        ))

    if cause_id == "collector_permission_denied":
        actions.append(RepairAction(
            action_id=f"action_{uuid4().hex[:8]}",
            action_type="permission_command_suggestion",
            risk_level=CONFIRM_REQUIRED,
            description="采集权限不足。需用户确认后调整 perf/eBPF 权限或以具备权限的用户运行 Agent。",
            command="sudo sysctl kernel.perf_event_paranoid=1",
        ))

    if not actions:
        actions.append(RepairAction(
            action_id=f"action_{uuid4().hex[:8]}",
            action_type="collect_more_evidence",
            risk_level=MANUAL_ONLY,
            description="当前证据不足，建议补充 baseline、火焰图 TopN 或 eBPF 延迟数据后重试。",
        ))

    risk_order = {SAFE_AUTO: 0, CONFIRM_REQUIRED: 1, MANUAL_ONLY: 2}
    plan_risk = max((item.risk_level for item in actions), key=lambda level: risk_order[level])
    return RepairPlan(
        plan_id=f"repair_{uuid4().hex[:10]}",
        task_id=task_id,
        cause_id=cause_id,
        risk_level=plan_risk,
        actions=actions,
        requires_user_confirm=any(item.risk_level != SAFE_AUTO for item in actions),
    )


def execute_safe_actions(plan: RepairPlan, repo) -> RepairPlan:
    """阻止 Legacy RCA 绕过统一策略控制面产生副作用。

    旧入口仍保留该函数以兼容调用方，但采集任务必须转交 Drop Insight
    ToolCall，由 Policy、Approval、Budget 和 Audit 统一处理。
    """
    del repo
    handed_off = False
    for action in plan.actions:
        if action.risk_level != SAFE_AUTO:
            continue
        if action.action_type != "create_followup_task":
            continue
        action.status = "policy_handoff_required"
        action.result = (
            "Legacy RCA 不直接执行；请通过 Drop Insight ToolCall 提交，"
            "由统一策略、审批、预算和审计链路处理。"
        )
        handed_off = True

    if handed_off:
        plan.status = "policy_handoff_required"
        plan.requires_user_confirm = True
    return plan
