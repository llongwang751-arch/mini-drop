"""RCA 工具层。

LLM 不直接读取杂散文本，而是基于这些工具的结构化输出进行推理。
每个工具结果都会进入 evidence.tool_results，并可被报告通过 evidence_refs 引用。
"""

from __future__ import annotations

from typing import Any

from server.app.rca.models import ToolResult


def run_rca_tools(
    task_record,
    top_functions: list[dict] | None = None,
    ebpf_metrics: dict | None = None,
    baseline_diff: dict | None = None,
    task_events: list[dict] | None = None,
    agent_record: Any | None = None,
) -> list[ToolResult]:
    """Execute the read-only diagnostic tools, collecting evidence in parallel.

    The tools are independent in-memory computations today; running them on a
    small worker pool keeps the pipeline flat when any tool later becomes
    I/O-bound (DB / object storage reads). Results preserve the canonical order.
    """
    from concurrent.futures import ThreadPoolExecutor

    workers = [
        lambda: _get_flamegraph_top(top_functions or []),
        lambda: _get_ebpf_latency_summary(ebpf_metrics or {}),
        lambda: _compare_baseline(baseline_diff or {}),
        lambda: _inspect_task_events(task_events or []),
        lambda: _check_agent_health(agent_record, task_record),
    ]
    with ThreadPoolExecutor(max_workers=len(workers)) as pool:
        return list(pool.map(lambda fn: fn(), workers))


def tool_results_to_evidence(tool_results: list[ToolResult]) -> list[dict]:
    return [
        {
            "tool_name": item.tool_name,
            "status": item.status,
            "evidence_ref": item.evidence_ref,
            "output": item.output,
            "error_message": item.error_message,
        }
        for item in tool_results
    ]


def _get_flamegraph_top(top_functions: list[dict]) -> ToolResult:
    output = {
        "top_functions": top_functions[:10],
        "top_percent": top_functions[0].get("percent", 0) if top_functions else 0,
        "sample_count": sum(int(item.get("samples", 0)) for item in top_functions),
    }
    status = "success" if top_functions else "missing"
    return ToolResult(
        tool_name="get_flamegraph_top",
        status=status,
        evidence_ref="tool_results.get_flamegraph_top",
        input={},
        output=output,
        error_message=None if top_functions else "缺少火焰图 TopN 数据",
    )


def _get_ebpf_latency_summary(ebpf_metrics: dict) -> ToolResult:
    histogram = ebpf_metrics.get("io_latency_us", {}) if ebpf_metrics else {}
    total = sum(int(v) for v in histogram.values()) if isinstance(histogram, dict) else 0
    max_bucket = None
    if isinstance(histogram, dict) and histogram:
        max_bucket = max(histogram.items(), key=lambda item: int(item[1]))[0]
    output = {
        "total_samples": total,
        "dominant_bucket": max_bucket,
        "histogram": histogram,
    }
    return ToolResult(
        tool_name="get_ebpf_latency_summary",
        status="success" if total > 0 else "missing",
        evidence_ref="tool_results.get_ebpf_latency_summary",
        input={},
        output=output,
        error_message=None if total > 0 else "缺少 eBPF IO 延迟样本",
    )


def _compare_baseline(baseline_diff: dict) -> ToolResult:
    return ToolResult(
        tool_name="compare_baseline",
        status="success" if baseline_diff else "missing",
        evidence_ref="tool_results.compare_baseline",
        input={},
        output=baseline_diff,
        error_message=None if baseline_diff else "缺少历史 baseline 对比",
    )


def _inspect_task_events(task_events: list[dict]) -> ToolResult:
    output = {
        "events": task_events[-10:],
        "failure_reasons": [
            item.get("reason", "") for item in task_events
            if item.get("to_status") == "FAILED" or "失败" in item.get("reason", "")
        ],
    }
    return ToolResult(
        tool_name="inspect_task_events",
        status="success" if task_events else "missing",
        evidence_ref="tool_results.inspect_task_events",
        input={},
        output=output,
        error_message=None if task_events else "缺少任务状态事件",
    )


def _check_agent_health(agent_record, task_record) -> ToolResult:
    if agent_record is None:
        output = {"agent_id": getattr(task_record, "agent_id", None), "status": "UNKNOWN"}
        return ToolResult(
            tool_name="check_agent_health",
            status="missing",
            evidence_ref="tool_results.check_agent_health",
            input={"agent_id": output["agent_id"]},
            output=output,
            error_message="未找到 Agent 记录",
        )

    output = {
        "agent_id": getattr(agent_record, "id", None),
        "status": getattr(agent_record, "status", "UNKNOWN"),
        "capabilities": getattr(agent_record, "capabilities", []) or [],
        "last_heartbeat_at": str(getattr(agent_record, "last_heartbeat_at", "")),
    }
    return ToolResult(
        tool_name="check_agent_health",
        status="success",
        evidence_ref="tool_results.check_agent_health",
        input={"agent_id": output["agent_id"]},
        output=output,
    )
