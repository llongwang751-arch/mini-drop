from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from server.app.diagnosis.store import DiagnosisStore
from server.app.evaluation.artifacts import canonicalize


SCHEMA_VERSION = "mini-drop.agent-read-projection.v1"
MAX_TOOL_PROJECTION_BYTES = 131_072
PAGE_SIZE = 50
SNAPSHOT_ITEM_LIMIT = 100

PROJECTION_KINDS = frozenset({
    "identity",
    "signal",
    "context",
    "quality",
    "provenance",
    "claims",
})
COMPARISON_DIMENSIONS = frozenset({
    "signal",
    "target",
    "window",
    "quality",
    "source",
})


class ReadToolError(ValueError):
    pass


class DiagnosisNotFound(ReadToolError):
    pass


class EvidenceNotFound(ReadToolError):
    pass


class InvalidReadRequest(ReadToolError):
    pass


class ProjectionTooLarge(ReadToolError):
    def __init__(self, actual_bytes: int, max_bytes: int) -> None:
        self.actual_bytes = actual_bytes
        self.max_bytes = max_bytes
        super().__init__(
            f"canonical projection is {actual_bytes} UTF-8 bytes; limit is {max_bytes}"
        )


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def projection_hash(serialized: str) -> str:
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def seal_projection(
    projection: dict[str, Any],
    *,
    max_bytes: int = MAX_TOOL_PROJECTION_BYTES,
) -> dict[str, Any]:
    serialized = canonical_json(projection)
    size = len(serialized.encode("utf-8"))
    if size > max_bytes:
        raise ProjectionTooLarge(size, max_bytes)
    return {
        "projection": canonicalize(projection),
        "projection_hash": projection_hash(serialized),
        "projection_bytes": size,
    }


def _is_eligible(evidence: dict[str, Any]) -> bool:
    return (
        evidence.get("lifecycle_status") == "ACTIVE"
        and evidence.get("trust_status") == "TRUSTED"
        and evidence.get("superseded_by") is None
    )


def _pick(source: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    return {key: source[key] for key in keys if key in source}


def _safe_string(value: Any, *, max_length: int = 4096) -> str | None:
    if not isinstance(value, str):
        return None
    return value[:max_length]


def _safe_string_list(value: Any, *, limit: int = SNAPSHOT_ITEM_LIMIT) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item[:1024] for item in value[:limit] if isinstance(item, str)]


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return _safe_string(value)


def _safe_strings(
    value: Any,
    fields: tuple[str, ...],
    *,
    max_length: int = 1024,
) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for field in fields:
        item = _safe_string(value.get(field), max_length=max_length)
        if item is not None:
            result[field] = item
    return result


def _safe_finite_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _safe_time_range(value: Any) -> dict[str, Any]:
    return _safe_strings(value, ("start", "end", "source"))


def _safe_evidence_time_range(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = _safe_strings(value, ("start", "end", "source"))
    if not isinstance(value, dict):
        return result
    for field in ("sampling_period_seconds", "clock_skew_estimate_ms"):
        number = _safe_finite_number(value.get(field))
        if number is not None:
            result[field] = number
    return result


def _safe_instance(value: Any) -> dict[str, Any]:
    return _safe_strings(
        value,
        ("service_id", "instance_id", "host_id", "environment", "runtime"),
    )


def _safe_target(value: Any) -> dict[str, Any]:
    return _safe_strings(
        value,
        (
            "service_id",
            "instance_id",
            "host_id",
            "environment",
            "runtime",
            "target_service",
        ),
    )


def _safe_boolean_fields(value: Any, fields: tuple[str, ...]) -> dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    return {
        field: value[field]
        for field in fields
        if isinstance(value.get(field), bool)
    }


def _safe_normalized_intent(value: Any) -> dict[str, Any]:
    intent: dict[str, Any] = _safe_strings(
        value,
        (
            "intent_type",
            "symptom",
            "target_service",
            "environment",
            "diagnosis_mode",
            "analysis_strategy",
        ),
    )
    if not isinstance(value, dict):
        return intent
    intent["ambiguities"] = _safe_string_list(value.get("ambiguities"), limit=32)
    intent["time_range"] = _safe_time_range(value.get("time_range"))
    policy = _safe_boolean_fields(
        value.get("evidence_time_policy"),
        ("require_overlap", "allow_reproduction_evidence"),
    )
    policy_value = value.get("evidence_time_policy")
    if isinstance(policy_value, dict):
        max_skew = _safe_finite_number(policy_value.get("max_clock_skew_seconds"))
        if max_skew is not None:
            policy["max_clock_skew_seconds"] = max_skew
    intent["evidence_time_policy"] = policy
    scope = _safe_boolean_fields(value.get("scope"), ("self", "same_host"))
    scope_value = value.get("scope")
    if isinstance(scope_value, dict):
        downstream_hops = scope_value.get("downstream_hops")
        if isinstance(downstream_hops, int) and not isinstance(downstream_hops, bool):
            scope["downstream_hops"] = downstream_hops
    intent["scope"] = scope
    intent["constraints"] = _safe_boolean_fields(
        value.get("constraints"),
        ("no_high_risk_probe", "registered_probes_only", "no_automatic_remediation"),
    )
    return intent


def _safe_target_scope(value: Any) -> dict[str, Any]:
    scope: dict[str, Any] = _safe_strings(
        value,
        ("target_service", "environment", "scope_completeness"),
    )
    if not isinstance(value, dict):
        return scope
    max_hops = value.get("max_topology_hops")
    if isinstance(max_hops, int) and not isinstance(max_hops, bool):
        scope["max_topology_hops"] = max_hops
    scope["same_host_instance_ids"] = _safe_string_list(
        value.get("same_host_instance_ids")
    )
    scope["downstream_service_ids"] = _safe_string_list(
        value.get("downstream_service_ids")
    )
    anchor = value.get("target_anchor")
    scope["target_anchor"] = _safe_instance(anchor) if anchor is not None else None
    instances = value.get("instances")
    scope["instances"] = [
        _safe_instance(item)
        for item in (instances if isinstance(instances, list) else [])[:SNAPSHOT_ITEM_LIMIT]
        if isinstance(item, dict)
    ]
    excluded_targets = value.get("excluded_targets")
    scope["excluded_targets"] = [
        projected
        for item in (
            excluded_targets if isinstance(excluded_targets, list) else []
        )[:SNAPSHOT_ITEM_LIMIT]
        if isinstance(item, dict)
        and (projected := _safe_strings(item, ("instance_id", "reason")))
    ]
    return scope


_SAFE_SIGNAL_NUMERIC_FIELDS = frozenset({
    "avg_cpu_user_pct",
    "avg_cpu_sys_pct",
    "avg_cpu_iowait_pct",
    "host_core_count",
    "load1m",
    "net_rx_kbps",
    "net_tx_kbps",
    "process_cpu_core_usage",
    "vmrss_mb",
    "vmrss_mb_max",
    "vmrss_slope_bytes_per_second",
    "fd_count",
    "fd_max",
    "fd_growth_per_minute",
    "thread_count",
    "thread_growth_per_minute",
    "process_read_bytes_per_second",
    "process_write_bytes_per_second",
    "packet_loss_pct",
    "tcp_retransmit_pct",
    "network_latency_p95_ms",
    "mysql_lock_wait_count",
    "mysql_lock_wait_seconds",
    "jvm_gc_pause_p95_ms",
    "jvm_gc_time_pct",
    "p95_us",
})
_SAFE_SIGNAL_ENUM_FIELDS = frozenset({
    "vmrss_trend",
    "memory_trend",
    "fd_trend",
    "thread_trend",
})


def _safe_signal_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key in _SAFE_SIGNAL_NUMERIC_FIELDS:
        number = _safe_finite_number(value.get(key))
        if number is not None:
            result[key] = number
    for key in _SAFE_SIGNAL_ENUM_FIELDS:
        item = _safe_string(value.get(key), max_length=32)
        if item is not None:
            result[key] = item
    return result


def _safe_top_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        projected: dict[str, Any] = {}
        name = _safe_string(item.get("name"), max_length=512)
        percent = _safe_finite_number(item.get("percent"))
        if name is not None:
            projected["name"] = name
        if percent is not None:
            projected["percent"] = percent
        if projected:
            items.append(projected)
    return items


def _safe_fact_domains(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    schema_version = _safe_string(value.get("schema_version"), max_length=64)
    normalized_from = _safe_string(value.get("normalized_from"), max_length=64)
    if schema_version is not None:
        result["schema_version"] = schema_version
    if normalized_from is not None:
        result["normalized_from"] = normalized_from
    for namespace in ("host", "process", "container"):
        source = value.get(namespace)
        if not isinstance(source, dict):
            continue
        projected: dict[str, Any] = {}
        for category in ("cpu", "load", "memory", "psi", "network", "fd", "io", "threads"):
            fields = source.get(category)
            if not isinstance(fields, dict):
                continue
            safe_fields: dict[str, Any] = {}
            for key, item in fields.items():
                if key not in {
                    "user_ratio", "system_ratio", "iowait_ratio", "core_count",
                    "load1", "normalized_core_usage", "rss_bytes",
                    "rss_slope_bytes_per_second", "count", "growth_per_minute",
                    "read_bytes_per_second", "write_bytes_per_second", "scope",
                    "rx_bytes_per_second", "tx_bytes_per_second",
                }:
                    continue
                if key == "scope":
                    scalar = _safe_string(item, max_length=32)
                else:
                    scalar = _safe_finite_number(item)
                if scalar is not None:
                    safe_fields[key] = scalar
            if safe_fields:
                projected[category] = safe_fields
        if projected:
            result[namespace] = projected
    return result


def _safe_observed_value(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    status = _safe_string(value.get("status"), max_length=64)
    collector_type = _safe_string(value.get("collector_type"), max_length=128)
    schema_version = _safe_string(value.get("schema_version"), max_length=64)
    if status is not None:
        result["status"] = status
    if collector_type is not None:
        result["collector_type"] = collector_type
    if schema_version is not None:
        result["schema_version"] = schema_version
    if isinstance(value.get("item_count"), int) and not isinstance(
        value.get("item_count"), bool
    ):
        result["item_count"] = value["item_count"]
    summary = _safe_signal_summary(value.get("summary"))
    if summary:
        result["summary"] = summary
    top_items = _safe_top_items(value.get("top_items"))
    if top_items:
        result["top_items"] = top_items
    fact_domains = _safe_fact_domains(value.get("fact_domains"))
    if fact_domains:
        result["fact_domains"] = fact_domains
    scalar_value = _safe_finite_number(value.get("value"))
    if scalar_value is not None:
        result["value"] = scalar_value
    return result


def _safe_data_quality(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    completeness = _safe_string(value.get("completeness"), max_length=32)
    if completeness is not None:
        result["completeness"] = completeness
    result["domains"] = _safe_string_list(value.get("domains"), limit=32)
    for field in ("size_bytes", "sample_count", "sampling_window_seconds"):
        number = _safe_finite_number(value.get(field))
        if number is not None:
            result[field] = number
    result["quality_reasons"] = _safe_string_list(
        value.get("quality_reasons"), limit=32
    )
    return result


def _safe_signal_projection(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "observed_value": _safe_observed_value(evidence.get("observed_value")),
        "baseline_value": {},
        "anomaly_score": {},
    }


def _safe_evidence_projection(
    evidence: dict[str, Any],
    kinds: frozenset[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if "identity" in kinds:
        result.update({
            "evidence_id": evidence["evidence_id"],
            "source_type": evidence.get("source_type"),
            "source_system": evidence.get("source_system"),
            "evidence_role": evidence.get("evidence_role"),
        })
    if "signal" in kinds:
        result.update(_safe_signal_projection(evidence))
    if "context" in kinds:
        result.update({
            "target": _safe_target(evidence.get("target")),
            "event_time_range": _safe_evidence_time_range(
                evidence.get("event_time_range")
            ),
        })
    if "quality" in kinds:
        result["data_quality"] = _safe_data_quality(evidence.get("data_quality"))
    if "provenance" in kinds:
        result.update({
            "ingestion_time": evidence.get("ingestion_time"),
            "query_or_probe": evidence.get("query_or_probe"),
            "derivation_version": evidence.get("derivation_version"),
            "integrity_hash": evidence.get("integrity_hash"),
        })
    if "claims" in kinds:
        result["claim_links"] = []
    return result


class DiagnosisReadTools:
    def __init__(self, store: DiagnosisStore | None = None) -> None:
        self.store = store or DiagnosisStore()

    def _session(self, diagnosis_id: str) -> dict[str, Any]:
        session = self.store.get_session(diagnosis_id)
        if session is None:
            raise DiagnosisNotFound("diagnosis session does not exist")
        return session

    def _eligible_evidence(self, diagnosis_id: str) -> list[dict[str, Any]]:
        self._session(diagnosis_id)
        return self.store.list_evidence(diagnosis_id, eligible_only=True)

    def _selected_evidence(
        self,
        diagnosis_id: str,
        evidence_ids: list[str],
    ) -> list[dict[str, Any]]:
        self._session(diagnosis_id)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise InvalidReadRequest("evidence_ids must be unique")
        selected: list[dict[str, Any]] = []
        for evidence_id in evidence_ids:
            evidence = self.store.get_evidence(diagnosis_id, evidence_id)
            if evidence is None or not _is_eligible(evidence):
                raise EvidenceNotFound("eligible evidence does not exist")
            selected.append(evidence)
        return selected

    def diagnosis_snapshot(self, diagnosis_id: str) -> dict[str, Any]:
        session = self._session(diagnosis_id)
        evidence = self.store.list_evidence(diagnosis_id, eligible_only=True)
        nodes = self.store.list_pipeline_nodes(diagnosis_id)
        probes = self.store.list_probes(diagnosis_id)
        topology = self.store.get_topology(session.get("topology_snapshot_id"))
        inventory = [
            {
                "evidence_id": item["evidence_id"],
                "evidence_role": item.get("evidence_role"),
                "source_type": item.get("source_type"),
                "integrity_hash": item.get("integrity_hash"),
            }
            for item in evidence[:SNAPSHOT_ITEM_LIMIT]
        ]
        pipeline = [
            {
                "node_name": item.get("node_name"),
                "sequence": item.get("sequence"),
                "status": item.get("status"),
                "attempt": item.get("attempt"),
                "error_code": item.get("error_code"),
            }
            for item in nodes[:SNAPSHOT_ITEM_LIMIT]
        ]
        probe_inventory = [
            {
                "step_id": item.get("step_id"),
                "probe_id": item.get("probe_id"),
                "target": _safe_target(item.get("target")),
                "reason": item.get("reason"),
                "risk_level": item.get("risk_level"),
                "status": item.get("status"),
                "evidence_purpose": item.get("evidence_purpose"),
                "round_index": item.get("round_index"),
                "error_code": item.get("error_code"),
                "error_message": item.get("error_message"),
            }
            for item in probes[:SNAPSHOT_ITEM_LIMIT]
        ]
        topology_summary = None
        if topology is not None:
            topology_summary = {
                "snapshot_id": topology.get("snapshot_id"),
                "effective_at": topology.get("effective_at"),
                "node_count": len(topology.get("nodes", [])),
                "edge_count": len(topology.get("edges", [])),
                "confidence_summary": topology.get("confidence_summary", {}),
            }
        projection = {
            "schema_version": SCHEMA_VERSION,
            "kind": "diagnosis_snapshot",
            "diagnosis_id": diagnosis_id,
            "case_id": session.get("case_id"),
            "goal": session.get("raw_query", ""),
            "normalized_intent": _safe_normalized_intent(
                session.get("normalized_intent")
            ),
            "target_scope": _safe_target_scope(session.get("target_scope")),
            "requested_time_range": _safe_time_range(
                session.get("requested_time_range")
            ),
            "effective_time_range": _safe_time_range(
                session.get("effective_time_range")
            ),
            "status": session.get("status"),
            "row_version": session.get("row_version"),
            "topology": topology_summary,
            "pipeline": pipeline,
            "pipeline_count": len(nodes),
            "pipeline_truncated": len(nodes) > len(pipeline),
            "probes": probe_inventory,
            "probe_count": len(probes),
            "probes_truncated": len(probes) > len(probe_inventory),
            "evidence_inventory": inventory,
            "eligible_evidence_count": len(evidence),
            "evidence_inventory_truncated": len(evidence) > len(inventory),
        }
        return seal_projection(projection)

    def list_evidence(
        self,
        diagnosis_id: str,
        *,
        filters: dict[str, str | None],
        cursor: str | None,
    ) -> dict[str, Any]:
        evidence = self._eligible_evidence(diagnosis_id)
        for name, expected in filters.items():
            if expected is not None:
                evidence = [item for item in evidence if item.get(name) == expected]
        start = 0
        if cursor is not None:
            matching = [
                index for index, item in enumerate(evidence)
                if item["evidence_id"] == cursor
            ]
            if not matching:
                raise InvalidReadRequest("invalid evidence cursor")
            start = matching[0] + 1
        page = evidence[start:start + PAGE_SIZE]
        has_more = start + len(page) < len(evidence)
        projection = {
            "schema_version": SCHEMA_VERSION,
            "kind": "evidence_inventory",
            "diagnosis_id": diagnosis_id,
            "filters": filters,
            "items": [
                {
                    "evidence_id": item["evidence_id"],
                    "source_type": item.get("source_type"),
                    "source_system": item.get("source_system"),
                    "evidence_role": item.get("evidence_role"),
                    "ingestion_time": item.get("ingestion_time"),
                    "integrity_hash": item.get("integrity_hash"),
                }
                for item in page
            ],
            "next_cursor": page[-1]["evidence_id"] if has_more and page else None,
            "has_more": has_more,
            "eligible_match_count": len(evidence),
        }
        return seal_projection(projection)

    def evidence_projection(
        self,
        diagnosis_id: str,
        *,
        evidence_ids: list[str],
        projection_kinds: list[str] | None,
        max_bytes: int,
    ) -> dict[str, Any]:
        kinds = frozenset(projection_kinds or PROJECTION_KINDS)
        unknown = kinds - PROJECTION_KINDS
        if unknown:
            raise InvalidReadRequest(
                "unsupported projection kinds: " + ", ".join(sorted(unknown))
            )
        selected = self._selected_evidence(diagnosis_id, evidence_ids)
        projection = {
            "schema_version": SCHEMA_VERSION,
            "kind": "evidence_projection",
            "diagnosis_id": diagnosis_id,
            "projection_kinds": sorted(kinds),
            "items": [
                _safe_evidence_projection(item, kinds)
                for item in selected
            ],
        }
        return seal_projection(projection, max_bytes=max_bytes)

    def compare_evidence(
        self,
        diagnosis_id: str,
        *,
        evidence_ids: list[str],
        dimensions: list[str] | None,
    ) -> dict[str, Any]:
        selected_dimensions = frozenset(dimensions or COMPARISON_DIMENSIONS)
        unknown = selected_dimensions - COMPARISON_DIMENSIONS
        if unknown:
            raise InvalidReadRequest(
                "unsupported comparison dimensions: " + ", ".join(sorted(unknown))
            )
        selected = self._selected_evidence(diagnosis_id, evidence_ids)
        values: dict[str, list[dict[str, Any]]] = {}
        for dimension in sorted(selected_dimensions):
            rows = []
            for item in selected:
                if dimension == "signal":
                    value = _safe_signal_projection(item)
                elif dimension == "target":
                    value = _safe_target(item.get("target"))
                elif dimension == "window":
                    value = _safe_evidence_time_range(item.get("event_time_range"))
                elif dimension == "quality":
                    value = _safe_data_quality(item.get("data_quality"))
                else:
                    value = {
                        "source_type": item.get("source_type"),
                        "source_system": item.get("source_system"),
                        "query_or_probe": item.get("query_or_probe"),
                        "integrity_hash": item.get("integrity_hash"),
                    }
                rows.append({"evidence_id": item["evidence_id"], "value": value})
            values[dimension] = rows
        projection = {
            "schema_version": SCHEMA_VERSION,
            "kind": "evidence_comparison",
            "diagnosis_id": diagnosis_id,
            "evidence_ids": evidence_ids,
            "dimensions": sorted(selected_dimensions),
            "values": values,
        }
        return seal_projection(projection)

    def evidence_gaps(self, diagnosis_id: str) -> dict[str, Any]:
        session = self._session(diagnosis_id)
        eligible_ids = {
            item["evidence_id"]
            for item in self.store.list_evidence(diagnosis_id, eligible_only=True)
        }
        graph = session.get("hypothesis_graph", {}) or {}
        hypotheses = graph.get("hypotheses", graph.get("nodes", [])) or []
        gaps: set[str] = set()
        for hypothesis in hypotheses:
            gaps.update(
                str(item) for item in hypothesis.get("missing_evidence_requirements", [])
                if item
            )
        conclusions = session.get("conclusion_versions", []) or []
        if conclusions:
            latest = conclusions[-1]
            gaps.update(str(item) for item in latest.get("limitations", []) if item)
            for candidate in latest.get("root_cause_candidates", []):
                gaps.update(
                    str(item) for item in candidate.get("missing_evidence", []) if item
                )
        if not eligible_ids:
            gaps.add("没有 ACTIVE、TRUSTED 且未被取代的 Evidence")
        if not gaps:
            gaps.add("缺少能够支持或推翻当前候选假设的独立证据")
        unexplained = sorted(
            str(item) for item in graph.get("unexplained_evidence_refs", [])
            if str(item) in eligible_ids
        )
        projection = {
            "schema_version": SCHEMA_VERSION,
            "kind": "evidence_gaps",
            "diagnosis_id": diagnosis_id,
            "diagnosis_status": session.get("status"),
            "insufficient_evidence": (
                session.get("status") == "INSUFFICIENT_EVIDENCE" or not eligible_ids
            ),
            "eligible_evidence_count": len(eligible_ids),
            "missing_requirements": sorted(gaps),
            "unexplained_evidence_refs": unexplained,
        }
        return seal_projection(projection)

    def evaluate_hypotheses(self, diagnosis_id: str) -> dict[str, Any]:
        session = self._session(diagnosis_id)
        eligible_ids = {
            item["evidence_id"]
            for item in self.store.list_evidence(diagnosis_id, eligible_only=True)
        }
        graph = session.get("hypothesis_graph", {}) or {}
        hypotheses = graph.get("hypotheses", graph.get("nodes", [])) or []
        evaluated = []
        missing: set[str] = set()
        for hypothesis in hypotheses:
            persisted_status = str(hypothesis.get("status", "UNTESTED")).upper()
            supporting = sorted({
                str(item) for item in hypothesis.get("supporting_evidence_refs", [])
                if str(item) in eligible_ids
            })
            contradicting = sorted({
                str(item) for item in hypothesis.get("contradicting_evidence_refs", [])
                if str(item) in eligible_ids
            })
            requirements = sorted({
                str(item)
                for item in hypothesis.get("missing_evidence_requirements", [])
                if item
            })
            if persisted_status == "SUPPORTED" and supporting:
                effective_status = "SUPPORTED"
            elif persisted_status == "RULED_OUT" and contradicting:
                effective_status = "RULED_OUT"
            elif persisted_status in {"INCONCLUSIVE", "SUPPORTED", "RULED_OUT"}:
                effective_status = "INCONCLUSIVE"
                if persisted_status in {"SUPPORTED", "RULED_OUT"}:
                    requirements.append(
                        "持久化判断所引用的 Evidence 当前不具备 AI 资格"
                    )
            else:
                effective_status = "UNEVALUATED"
            requirements = sorted(set(requirements))
            missing.update(requirements)
            evaluated.append({
                "hypothesis_id": hypothesis.get("hypothesis_id", hypothesis.get("id")),
                "type": hypothesis.get("type"),
                "description": hypothesis.get("description"),
                "persisted_status": persisted_status,
                "effective_status": effective_status,
                "supporting_evidence_refs": supporting,
                "contradicting_evidence_refs": contradicting,
                "missing_evidence_requirements": requirements,
                "next_probe_candidates": sorted({
                    str(item) for item in hypothesis.get("next_probe_candidates", [])
                    if item
                }),
            })
        counts = {
            status: sum(
                1 for item in evaluated if item["effective_status"] == status
            )
            for status in ("SUPPORTED", "RULED_OUT", "INCONCLUSIVE", "UNEVALUATED")
        }
        unexplained = sorted({
            str(item) for item in graph.get("unexplained_evidence_refs", [])
            if str(item) in eligible_ids
        })
        projection = {
            "schema_version": SCHEMA_VERSION,
            "kind": "hypothesis_evaluation",
            "diagnosis_id": diagnosis_id,
            "evaluation_source": "persisted_state_read_only",
            "eligible_evidence_count": len(eligible_ids),
            "hypotheses": evaluated,
            "counts": counts,
            "missing_requirements": sorted(missing),
            "unexplained_evidence_refs": unexplained,
            "graph_complete": (
                bool(evaluated)
                and counts["INCONCLUSIVE"] == 0
                and counts["UNEVALUATED"] == 0
                and not missing
                and not unexplained
            ),
        }
        return seal_projection(projection)
