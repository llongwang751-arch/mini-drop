"""Executable observations: metric names and numeric comparisons, never keyword proof."""
from __future__ import annotations

import json
import operator
import re

from .evidence import observed_nonnegative

# Measurements retain their native units. Rates/bytes/counts are not latency.
SIGNAL_FIELDS = {
    "queue_backlog": ("producer_rate", "consumer_rate", "queue_lag"),
    "load_saturation": ("offered_rps", "completed_rps", "rejected_requests", "queue_depth"),
    "noisy_neighbor": ("peer_cpu_ticks_delta",),
    "lock_contention": ("lock_wait_ms_delta", "lock_contentions_delta", "lock_acquisitions_delta"),
    "jvm_gc": ("allocated_bytes_delta", "gc_count_delta", "gc_time_ms_delta"),
    "downstream_latency": ("average_latency_ms", "request_count_delta", "failure_count_delta"),
    "network_latency": ("average_latency_ms", "request_count_delta", "failure_count_delta"),
    "io_latency": ("latency_p95_us", "latency_p99_us"),
    "io_activity": ("disk_write_kbps", "io_bytes_written_delta", "io_operations_delta"),
    "memory_growth": ("rss_delta_mb", "pss_delta_mb", "retained_memory_delta_mb"),
    "memory_retention": ("retained_memory_mb",),
    "cpu_hotspot": ("process_cpu_core_usage", "application_cpu_percent"),
    "http_service_degradation": ("average_latency_ms", "recent_p95_latency_ms", "failure_rate", "request_count_delta"),
}
_PATTERN = re.compile(r"([a-z_]+)\.([a-z_]+)\s*(>=|<=|>|<|==)\s*(\d+(?:\.\d+)?)", re.ASCII)
_OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "==": operator.eq}

_RULE_PLANS = {
    "DOWNSTREAM_DEPENDENCY": ("下游调用耗时的有界观测", "downstream_latency", "average_latency_ms", 100),
    "NETWORK_LATENCY": ("HTTP 网络路径耗时的有界观测，尚未确定服务处理或传输原因", "network_latency", "average_latency_ms", 100),
    "QUEUE_CONGESTION": ("目标进程队列积压的有界观测", "queue_backlog", "queue_lag", 1),
    "LOAD_SATURATION": ("入口拒绝计数的有界观测", "load_saturation", "rejected_requests", 1),
    "MEMORY_PRESSURE": ("目标进程窗口内 RSS 增长的有界观测，尚未证明泄漏", "memory_growth", "rss_delta_mb", 8),
}


def performance_observation_plan(category):
    spec = _RULE_PLANS.get(category)
    if spec is None:
        return None
    statement, signal, metric, threshold = spec
    return {"statement": statement, "expected": [f"{signal}.{metric} >= {threshold}"],
            "falsification": [f"{signal}.{metric} < {threshold}"]}


def evaluate_performance_criterion(text, signals):
    """Return None for an unsupported/missing slot; absence is never a zero."""
    match = _PATTERN.fullmatch(text.strip()) if isinstance(text, str) else None
    if not match:
        return None
    signal, field, operation, threshold = match.groups()
    if field not in SIGNAL_FIELDS.get(signal, ()):
        return None
    observation = signals.get(signal)
    if not isinstance(observation, dict):
        return None
    metrics = observation.get("metrics")
    if not isinstance(metrics, dict):
        return None
    raw_value = metrics.get(field)
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        return None
    value = observed_nonnegative(raw_value, maximum=1 if field == "failure_rate" else None)
    if value is None:
        return None
    threshold_value = observed_nonnegative(float(threshold))
    if threshold_value is None:
        return None
    return {"signal": signal, "field": field, "value": value,
            "threshold": threshold_value, "matches": _OPS[operation](value, threshold_value)}


PERFORMANCE_PLANNING_REQUIREMENT = (
    " 性能信号的覆盖只支持完整数值判据 signal.metric >= N（亦支持 >/< /<=/==）。"
    "必须声明阈值，不能将写入量当作fsync延迟、HTTP路径耗时当作丢包/重传、"
    "保留内存绝对量当作持续增长、同宿主peer活动当作资源争抢。"
    "未采到字段不能证明为0或正常；同一Artifact的两字段不能当作独立对照。"
    "这些判据只能证明有界观测；因果主张须另有同负载干预验证。可执行字段："
    + json.dumps(SIGNAL_FIELDS, ensure_ascii=False)
    + " 范例：" + json.dumps([performance_observation_plan(category) for category in _RULE_PLANS], ensure_ascii=False)
)
