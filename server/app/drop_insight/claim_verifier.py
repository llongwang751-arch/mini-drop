"""Deterministic Claim-Evidence verification for Drop Insight reports.

The model may propose hypotheses, but report facts are derived from immutable
Analyzer observations. Every accepted claim carries a JSON Pointer and the
value at that pointer is resolved again before the claim is persisted.
"""

from __future__ import annotations

import re
from typing import Any

from .evidence import EvidenceEnvelope, classify_evidence


DOMAIN_FIELDS = {
    "avg_cpu_user_pct": ("CPU_USER_PERCENT", "用户态 CPU 占比"),
    "avg_cpu_sys_pct": ("CPU_SYSTEM_PERCENT", "系统态 CPU 占比"),
    "avg_cpu_iowait_pct": ("IO_WAIT_PERCENT", "I/O wait 占比"),
    "process_cpu_core_usage": ("PROCESS_CPU_USAGE", "进程 CPU 核占用"),
    "load1m": ("SYSTEM_LOAD", "系统一分钟负载"),
    "vmrss_mb": ("PROCESS_RSS", "进程 RSS"),
    "vmrss_mb_max": ("PROCESS_RSS_MAX", "进程 RSS 峰值"),
    "rss_mb": ("PROCESS_RSS", "进程 RSS"),
    "memory_usage_pct": ("MEMORY_USAGE_PERCENT", "内存使用率"),
    "swap_used_mb": ("SWAP_USAGE", "Swap 使用量"),
    "fd_count": ("FD_COUNT", "文件描述符数量"),
    "fd_max": ("FD_MAX", "文件描述符峰值"),
    "fd_trend": ("FD_TREND", "文件描述符趋势"),
    "thread_count": ("THREAD_COUNT", "线程数量"),
    "thread_trend": ("THREAD_TREND", "线程数量趋势"),
    "ctx_nonvoluntary_rate": ("CONTEXT_SWITCH_RATE", "非自愿上下文切换速率"),
    "io_latency_us": ("IO_LATENCY", "I/O 延迟分布"),
    "block_latency_us": ("IO_LATENCY", "块设备延迟"),
    "disk_read_kbps": ("DISK_READ_THROUGHPUT", "磁盘读取吞吐"),
    "disk_write_kbps": ("DISK_WRITE_THROUGHPUT", "磁盘写入吞吐"),
    "net_rx_kbps": ("NETWORK_RX_THROUGHPUT", "网络接收吞吐"),
    "net_tx_kbps": ("NETWORK_TX_THROUGHPUT", "网络发送吞吐"),
    "packet_loss_pct": ("NETWORK_PACKET_LOSS", "网络丢包率"),
    "tcp_retransmit_pct": ("TCP_RETRANSMIT", "TCP 重传率"),
    "tcp_retransmits": ("TCP_RETRANSMIT", "TCP 重传数量"),
    "lock_wait_ms": ("DATABASE_LOCK_WAIT", "数据库锁等待时长"),
    "lock_wait_count": ("DATABASE_LOCK_WAIT_COUNT", "数据库锁等待数量"),
    "blocking_session_count": ("DATABASE_BLOCKING_SESSIONS", "数据库阻塞会话数量"),
    "gc_pause_ms": ("JVM_GC_PAUSE", "JVM GC 暂停时长"),
    "gc_time_pct": ("JVM_GC_TIME_PERCENT", "JVM GC 时间占比"),
    "full_gc_count": ("JVM_FULL_GC_COUNT", "JVM Full GC 次数"),
    "heap_used_pct": ("JVM_HEAP_USAGE", "JVM 堆使用率"),
    "queue_depth": ("QUEUE_DEPTH", "队列深度"),
    "queue_wait_ms": ("QUEUE_WAIT", "队列等待时长"),
    "consumer_lag": ("QUEUE_CONSUMER_LAG", "队列消费延迟"),
}


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    if pointer in {"", "/"}:
        return document
    if not pointer.startswith("/"):
        raise ValueError("JSON Pointer must start with '/'")
    current = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise KeyError(pointer)
    return current


def _reference_tokens(reference: str) -> list[str]:
    """Split a legacy evidence reference into JSON Pointer tokens."""
    if not reference:
        return []
    return [token for token in reference.split("/") if token != ""]


def _escape_pointer_token(token: str) -> str:
    """Escape a single JSON Pointer reference token (RFC 6901)."""
    return token.replace("~", "~0").replace("/", "~1")


def evidence_ref_to_json_pointer(document: Any, reference: str) -> str:
    tokens = _reference_tokens(reference)
    if tokens and tokens[0] == "tool_results" and len(tokens) > 1 and not str(tokens[1]).isdigit():
        tool_name = str(tokens[1])
        rows = document.get("tool_results", []) if isinstance(document, dict) else []
        index = next(
            (index for index, row in enumerate(rows) if isinstance(row, dict) and row.get("tool_name") == tool_name),
            None,
        )
        if index is None:
            raise KeyError(reference)
        tokens = ["tool_results", str(index), *tokens[2:]]
    pointer = "/" + "/".join(_escape_pointer_token(str(token)) for token in tokens)
    resolve_json_pointer(document, pointer)
    return pointer


def verify_report_claims(
    evidence: list[tuple[str, EvidenceEnvelope]],
    *,
    expected_observations: list[str],
    falsification_criteria: list[str],
) -> dict[str, Any]:
    claims: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    covered_expected: set[int] = set()
    covered_falsification: set[int] = set()

    for role, envelope in evidence:
        if role not in {"SUPPORT", "COUNTER"}:
            continue
        classification = classify_evidence(envelope)
        if not classification["can_support_conclusion"]:
            rejected.append({
                "evidence_id": envelope.evidence_id,
                "artifact_id": envelope.source.artifact_id,
                "artifact_sha256": envelope.source.artifact_sha256,
                "direction": role,
                "claim_type": "EVIDENCE_REJECTED",
                "statement": "Evidence did not pass the trusted provenance boundary",
                "json_pointer": envelope.source.observation_json_pointer,
                "claimed_value": None,
                "criterion": None,
                "valid": False,
                "reasons": list(classification["reasons"]),
            })
            continue
        for candidate in _claim_candidates(role, envelope):
            reasons = _validate_claim(candidate, envelope.observation)
            if reasons:
                rejected.append({**candidate, "valid": False, "reasons": reasons})
                continue
            candidate = {**candidate, "valid": True, "reasons": []}
            claims.append(candidate)
            _record_coverage(candidate, covered_expected, covered_falsification)

    return _verification_result(
        claims,
        rejected,
        covered_expected,
        covered_falsification,
        len(expected_observations),
        len(falsification_criteria),
    )


def verify_legacy_report_claims(report: Any, evidence: Any) -> dict[str, Any]:
    report_document = report.model_dump(mode="json") if hasattr(report, "model_dump") else report
    evidence_document = evidence.model_dump(mode="json") if hasattr(evidence, "model_dump") else evidence
    causes = report_document.get("ranked_causes", []) if isinstance(report_document, dict) else []
    claims: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    covered_expected: set[int] = set()

    for index, cause in enumerate(causes):
        references = cause.get("evidence_refs", []) if isinstance(cause, dict) else []
        resolved: list[tuple[str, str, Any]] = []
        for reference in references:
            try:
                pointer = evidence_ref_to_json_pointer(evidence_document, str(reference))
                value = resolve_json_pointer(evidence_document, pointer)
            except (KeyError, IndexError, ValueError, TypeError):
                rejected.append({
                    "evidence_id": str(reference),
                    "direction": "SUPPORT",
                    "claim_type": "LEGACY_EVIDENCE_REFERENCE",
                    "statement": str(cause.get("claim") or ""),
                    "json_pointer": None,
                    "claimed_value": None,
                    "criterion": f"expected:{index}",
                    "valid": False,
                    "reasons": ["legacy evidence reference cannot be resolved"],
                })
                continue
            resolved.append((str(reference), pointer, value))

        numeric_reasons = _validate_statement_numbers(
            str(cause.get("claim") or ""), [item[2] for item in resolved]
        )
        for reference, pointer, value in resolved:
            reasons = list(numeric_reasons)
            claim = {
                "evidence_id": str(reference),
                "direction": "SUPPORT",
                "claim_type": "LEGACY_CAUSE",
                "statement": str(cause.get("claim") or ""),
                "json_pointer": pointer,
                "claimed_value": value,
                "criterion": {"kind": "expected", "index": index},
                "valid": not reasons,
                "reasons": reasons,
            }
            if reasons:
                rejected.append(claim)
            else:
                claims.append(claim)
                covered_expected.add(index)

    return _verification_result(
        claims,
        rejected,
        covered_expected,
        set(),
        len(causes),
        0,
    )


def _claim_candidates(role: str, envelope: EvidenceEnvelope) -> list[dict[str, Any]]:
    """Derive candidate claims from an accepted evidence envelope.

    A candidate claim carries a `criterion` that maps it to one of the
    expected-observation or falsification-criterion coverage slots, so the
    final verification can compute how much of the hypothesis plan the
    evidence actually covers. Every claim is re-validated against the
    observation later by :func:`_validate_claim`.
    """
    observation = envelope.observation if isinstance(envelope.observation, dict) else {}
    metadata = observation.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    direction = role
    base = {
        "evidence_id": envelope.evidence_id,
        "artifact_id": envelope.source.artifact_id,
        "artifact_sha256": envelope.source.artifact_sha256,
        "direction": direction,
        "valid": True,
        "reasons": [],
    }

    candidates: list[dict[str, Any]] = []
    predicate = metadata.get("hypothesis_predicate")
    if isinstance(predicate, dict) and predicate.get("outcome") == role:
        raw_indexes = predicate.get("criterion_indexes")
        indexes = raw_indexes if isinstance(raw_indexes, list) and raw_indexes else [0]
        for index in indexes:
            if not isinstance(index, int) or index < 0:
                continue
            candidates.append({
                **base,
                "claim_type": "HYPOTHESIS_PREDICATE",
                "statement": str(predicate.get("reason") or f"evidence predicate is {role}"),
                "json_pointer": "/metadata/hypothesis_predicate/outcome",
                "claimed_value": predicate.get("outcome"),
                "criterion": {
                    "kind": "expected" if role == "SUPPORT" else "falsification",
                    "index": index,
                },
            })

    top_functions = metadata.get("top_functions")
    if isinstance(top_functions, list):
        for index, fn in enumerate(top_functions):
            if not isinstance(fn, dict):
                continue
            name = str(fn.get("name", f"function_{index}"))
            percent = fn.get("percent")
            candidates.append({
                **base,
                "claim_type": "TOP_FUNCTION_PERCENT",
                "statement": f"{name} 占 {percent}% 样本",
                "json_pointer": f"/metadata/top_functions/{index}/percent",
                "claimed_value": percent,
                "criterion": None,
            })

    candidates.extend(_domain_field_claims(base, observation))
    return candidates


def _domain_field_claims(base: dict[str, Any], observation: Any) -> list[dict[str, Any]]:
    """Emit SYS_METRIC claims for known domain fields present in the observation.

    sys_metrics evidence carries a `summary` (and optionally `process`) block of
    threshold-relevant fields (CPU, RSS, fd, threads, IO, network, DB locks,
    JVM GC, queue depth). Every known field becomes a claim with an exact JSON
    Pointer so `_validate_claim` can re-resolve it against the immutable
    observation. Criterion is None: these add supporting evidence mass but do
    not by themselves satisfy a hypothesis coverage slot.
    """
    claims: list[dict[str, Any]] = []
    if not isinstance(observation, dict):
        return claims
    containers: list[tuple[str, dict]] = [("/", observation)]
    for key in ("summary", "process"):
        child = observation.get(key)
        if isinstance(child, dict):
            containers.append((f"/{key}", child))
    for prefix, container in containers:
        for field, (metric_code, label) in DOMAIN_FIELDS.items():
            if field not in container:
                continue
            value = container[field]
            pointer = f"{prefix}/{field}" if prefix != "/" else f"/{field}"
            claims.append({
                **base,
                "claim_type": f"SYS_METRIC_{metric_code}",
                "statement": f"{label}: {value}",
                "json_pointer": pointer,
                "claimed_value": value,
                "criterion": None,
            })
    return claims


def _validate_claim(candidate: dict[str, Any], observation: Any) -> list[str]:
    """Re-resolve the claimed JSON Pointer and reject numeric drift.

    A claim's numeric value must equal the value at its JSON Pointer inside
    the immutable Analyzer observation. This is what prevents a report from
    asserting a number the evidence does not actually contain.
    """
    pointer = candidate.get("json_pointer")
    claimed = candidate.get("claimed_value")
    if not pointer:
        return []
    try:
        actual = resolve_json_pointer(observation, pointer)
    except (KeyError, IndexError, ValueError, TypeError):
        return [f"claim json pointer cannot be resolved: {pointer}"]
    if isinstance(claimed, bool):
        return []
    if isinstance(claimed, (int, float)):
        try:
            actual_number = float(actual)
        except (TypeError, ValueError):
            return [f"evidence value at {pointer} is not numeric"]
        if abs(float(claimed) - actual_number) > 1e-9:
            return [
                f"claimed value {claimed} differs from evidence value {actual_number}"
            ]
    return []


def _record_coverage(
    candidate: dict[str, Any],
    covered_expected: set[int],
    covered_falsification: set[int],
) -> None:
    criterion = candidate.get("criterion")
    if not isinstance(criterion, dict):
        return
    index = criterion.get("index")
    if index is None:
        return
    if criterion.get("kind") == "expected":
        covered_expected.add(int(index))
    elif criterion.get("kind") == "falsification":
        covered_falsification.add(int(index))


def _verification_result(
    claims: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    covered_expected: set[int],
    covered_falsification: set[int],
    n_expected: int,
    n_falsification: int,
) -> dict[str, Any]:
    support_count = sum(1 for item in claims if item.get("direction") == "SUPPORT")
    counter_count = sum(1 for item in claims if item.get("direction") == "COUNTER")
    denominator = n_expected + n_falsification
    coverage_ratio = (
        (len(covered_expected) + len(covered_falsification)) / denominator
        if denominator > 0
        else 0.0
    )
    has_counter = counter_count > 0

    if not claims:
        status = "INSUFFICIENT_EVIDENCE"
    elif support_count > 0 and has_counter and coverage_ratio >= 1.0:
        status = "VERIFIED"
    elif support_count > 0:
        status = "PARTIAL_WITHOUT_COUNTER"
    else:
        status = "INSUFFICIENT_EVIDENCE"

    verification = {
        "status": status,
        "has_independent_counter_or_control": has_counter,
        "support_claim_count": support_count,
        "counter_claim_count": counter_count,
        "coverage_ratio": coverage_ratio,
        "covered_expected": sorted(covered_expected),
        "covered_falsification": sorted(covered_falsification),
        "rejected_count": len(rejected),
    }
    return {
        "status": status,
        "claims": claims,
        "rejected_claims": rejected,
        "support_claim_count": support_count,
        "counter_claim_count": counter_count,
        "coverage_ratio": coverage_ratio,
        "has_independent_counter_or_control": has_counter,
        "verification": verification,
    }


def _validate_statement_numbers(claim_text: str, resolved_values: list[Any]) -> list[str]:
    """Compare numbers mentioned in a claim statement against resolved evidence.

    Legacy RCA claims are prose. If the claim names a number that no evidence
    value matches, the claim is flagged rather than trusted on its face.
    """
    if not claim_text or not resolved_values:
        return []
    numbers = [float(match) for match in re.findall(r"\d+(?:\.\d+)?", claim_text)]
    if not numbers:
        return []
    evidence_numbers = [
        float(value)
        for value in resolved_values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not evidence_numbers:
        return []
    reasons: list[str] = []
    for number in numbers:
        if not any(abs(number - expected) <= 1e-9 for expected in evidence_numbers):
            reasons.append(
                f"claim statement number {number} is not supported by evidence values"
            )
    return reasons
