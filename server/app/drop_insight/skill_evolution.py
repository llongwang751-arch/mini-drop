from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError
from server.app.database import new_session
from server.app.drop_insight.evidence import EvidenceEnvelope, classify_evidence
from server.app.drop_insight.schemas import RecordSkillCampaignValidationRequest
from server.app.models import (
    AgentModel,
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
    "collect_memory_profile": "MEMORY_PRESSURE",
    "collect_go_profile": "GO_RUNTIME",
    "start_continuous_profile": "CPU_HOTSPOT",
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
    "memory_smaps": "collect_memory_profile",
    "go_pprof": "collect_go_profile",
    "continuous_perf": "start_continuous_profile",
}
_MATCH_THRESHOLD = 700
_HYBRID_MATCH_THRESHOLD = 0.35
_HYBRID_MIN_MARGIN = 0.04
_EXPLICIT_QUERY_ANCHOR_BONUS = 0.30
_CROSS_CATEGORY_MIN_BM25 = 0.22
_CROSS_CATEGORY_MIN_VECTOR = 0.12
_RELIABILITY_PRIOR_SUCCESSES = 2.0
_RELIABILITY_PRIOR_FAILURES = 2.0
_CAMPAIGN_CASE_KIND = "CROSS_ENVIRONMENT_CAMPAIGN"
_CAMPAIGN_MAX_FALSE_ACTIVATION_RATE = 0.05
_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_SEARCH_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "is",
    "of",
    "on",
    "the",
    "to",
    "with",
    "出现",
    "发生",
    "异常",
    "问题",
}
_VECTOR_CONCEPTS = {
    "cpu": "compute",
    "hotspot": "compute",
    "火焰图": "compute",
    "热点": "compute",
    "busyloop": "compute",
    "p99": "latency",
    "latency": "latency",
    "尾延迟": "latency",
    "延迟": "latency",
    "slow": "latency",
    "io": "storage",
    "iops": "storage",
    "disk": "storage",
    "磁盘": "storage",
    "writeback": "storage",
    "fsync": "storage",
    "lock": "lock",
    "mutex": "lock",
    "futex": "lock",
    "锁": "lock",
    "锁等": "lock",
    "等待": "wait",
    "queue": "wait",
    "排队": "wait",
    "pool": "pool",
    "连接池": "pool",
    "connection": "pool",
    "gc": "runtime",
    "gil": "runtime",
    "python": "runtime",
    "jvm": "runtime",
    "serialisation": "serialization",
    "serialization": "serialization",
    "network": "network",
    "tcp": "network",
    "网络": "network",
    "timeout": "timeout",
    "超时": "timeout",
}
_GENERIC_ROUTE_CATEGORIES = {"SYSTEM_RESOURCE"}
_SUBSYSTEM_CATEGORY = {
    "cpu": "CPU_HOTSPOT",
    "python": "PYTHON_RUNTIME",
    "storage": "IO_LATENCY",
    "io": "IO_LATENCY",
    "jvm": "JVM_GC",
    "memory": "MEMORY_PRESSURE",
    "go": "GO_RUNTIME",
    "database": "DATABASE_LOCK",
    "network": "NETWORK_DEGRADATION",
}
_TOOL_REQUIRED_CAPABILITIES = {
    "collect_sys_metrics": {"sys_metrics"},
    "start_perf_profile": {"perf_cpu"},
    "start_ebpf_io_profile": {"ebpf_io"},
    "start_pyspy_profile": {"pyspy"},
    "start_jvm_profile": {"java_async"},
    "collect_memory_profile": {"memory_smaps"},
    "collect_go_profile": {"go_pprof"},
    "start_continuous_profile": {"continuous_perf"},
    "collect_database_diagnostics": {"database_lock"},
    "collect_network_diagnostics": {"network_diagnostics"},
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _category_for_route(route: list[str]) -> str:
    for tool_name in route:
        if tool_name in _TOOL_CATEGORY:
            return _TOOL_CATEGORY[tool_name]
    return "GENERAL"


def _skill_version_fingerprint(skill: DiagnosticSkillModel) -> str:
    payload = {
        "skill_id": skill.id,
        "family_key": skill.family_key,
        "category": skill.category,
        "version": skill.version,
        "trigger": skill.trigger_json or {},
        "strategy": skill.strategy_json or {},
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    category_exact = skill.category == category
    # The rule planner's category is a useful prior, not a trusted fact.  Keep
    # a sizeable exact-category advantage while allowing strong Skill text to
    # recover from an upstream classification error.
    score = 650 if category_exact else 300
    reasons = {
        "category": "exact" if category_exact else "correction_candidate",
        "requested_category": category,
        "skill_category": skill.category,
    }
    required_environment = str(trigger.get("environment") or "*").lower()
    actual_environment = str(target.get("environment") or "*").lower()
    if required_environment not in {"", "*"}:
        if actual_environment != required_environment:
            return 0, {
                **reasons,
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
    elif service and actual_service:
        # Service affinity is deliberately soft: a reviewed generic route may
        # transfer, but an incident learned from one service must not receive
        # the exact-service boost elsewhere.
        reasons["service"] = "different"
    return min(score, 1000), reasons


def _tokenize_search_text(value: object) -> list[str]:
    """Tokenize mixed Chinese/English incident text for local retrieval."""
    tokens: list[str] = []
    for part in _TOKEN_RE.findall(str(value or "").lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            if len(part) == 1:
                tokens.append(part)
            else:
                tokens.extend(part[index : index + 2] for index in range(len(part) - 1))
                if len(part) <= 8:
                    tokens.append(part)
        else:
            tokens.append(part)
    return [item for item in tokens if item and item not in _SEARCH_STOPWORDS]


def _skill_search_text(skill: DiagnosticSkillModel) -> str:
    trigger = skill.trigger_json or {}
    strategy = skill.strategy_json or {}
    values: list[object] = [
        trigger.get("source_query"),
        " ".join(trigger.get("query_terms") or []),
        trigger.get("service"),
        skill.category,
        " ".join(strategy.get("probe_order") or []),
        " ".join(strategy.get("symptoms") or []),
    ]
    exploration = strategy.get("actual_exploration") or {}
    for node in exploration.get("nodes") or []:
        values.extend((node.get("label"), node.get("reason")))
    return " ".join(str(item or "") for item in values)


def _matching_query_anchors(query: str, anchors: list[object]) -> list[str]:
    """Return explicit runtime anchors present in the raw user query.

    Token retrieval intentionally tolerates punctuation and partial lexical
    overlap. Runtime-specific Skills need a stronger boundary: ``C++`` must
    survive punctuation normalization, while ASCII aliases such as ``cpp``
    must match a whole token rather than an arbitrary substring.
    """

    normalized_query = str(query or "").casefold()
    matched: list[str] = []
    for raw_anchor in anchors:
        anchor = str(raw_anchor or "").strip().casefold()
        if not anchor:
            continue
        if re.fullmatch(r"[a-z0-9_]+", anchor):
            pattern = rf"(?<![a-z0-9_]){re.escape(anchor)}(?![a-z0-9_])"
            present = re.search(pattern, normalized_query) is not None
        else:
            present = anchor in normalized_query
        if present:
            matched.append(anchor)
    return list(dict.fromkeys(matched))


def _bm25_scores(query_tokens: list[str], documents: list[list[str]]) -> list[float]:
    if not query_tokens or not documents:
        return [0.0 for _ in documents]
    document_count = len(documents)
    average_length = sum(len(item) for item in documents) / max(document_count, 1)
    document_frequency = Counter()
    for document in documents:
        document_frequency.update(set(document))
    query_frequency = Counter(query_tokens)
    raw_scores: list[float] = []
    for document in documents:
        frequencies = Counter(document)
        score = 0.0
        for token, query_count in query_frequency.items():
            frequency = frequencies.get(token, 0)
            if frequency == 0:
                continue
            inverse_document_frequency = math.log(
                1 + (document_count - document_frequency[token] + 0.5)
                / (document_frequency[token] + 0.5)
            )
            denominator = frequency + 1.5 * (
                1 - 0.75 + 0.75 * len(document) / max(average_length, 1)
            )
            score += query_count * inverse_document_frequency * frequency * 2.5 / denominator
        raw_scores.append(score)
    # Saturate each score independently. Normalizing by the best candidate
    # made confidence change whenever an unrelated Skill entered the catalog.
    return [1.0 - math.exp(-item) for item in raw_scores]


def _vector_features(tokens: list[str]) -> Counter[str]:
    """Build deterministic lexical n-gram and domain-concept features.

    This is not a neural embedding. It is a local feature-vector backend that
    tolerates punctuation, spelling variants and bounded diagnosis synonyms.
    """
    features: Counter[str] = Counter()
    compact_ascii = "".join(token for token in tokens if token.isascii())
    for token in tokens:
        features[f"token:{token}"] += 1.0
        concept = _VECTOR_CONCEPTS.get(token)
        if concept:
            features[f"concept:{concept}"] += 3.0
        if token.isascii() and len(token) >= 4:
            normalized = re.sub(r"[^a-z0-9]", "", token)
            for width in (3, 4):
                for index in range(max(0, len(normalized) - width + 1)):
                    features[f"char:{normalized[index:index + width]}"] += 0.35
    # Joining ASCII tokens lets write-back/writeback and similar punctuation
    # variants meet in vector space without weakening BM25 exact matching.
    if compact_ascii:
        for width in (3, 4):
            for index in range(max(0, len(compact_ascii) - width + 1)):
                features[f"compact:{compact_ascii[index:index + width]}"] += 0.15
    return features


def _hashed_vector(features: Counter[str], dimensions: int = 512) -> list[float]:
    """Create a deterministic signed-hashing vector from weighted features."""
    vector = [0.0] * dimensions
    for feature, weight in features.items():
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign * weight
    norm = math.sqrt(sum(item * item for item in vector))
    return [item / norm for item in vector] if norm else vector


def _vector_similarity(left: list[str], right: list[str]) -> float:
    if not left or not right:
        return 0.0
    left_vector = _hashed_vector(_vector_features(left))
    right_vector = _hashed_vector(_vector_features(right))
    return max(0.0, min(1.0, sum(a * b for a, b in zip(left_vector, right_vector))))


def _rank_hybrid_skills(
    skills: list[DiagnosticSkillModel], category: str, target: dict, query: str
) -> list[tuple[float, dict, DiagnosticSkillModel]]:
    """Hard-filter incompatible Skills, then rank by BM25 + vector + context.

    A reviewed Skill may classify an otherwise UNKNOWN request, but it cannot
    overwrite a concrete symptom category selected by the baseline planner.
    That boundary prevents a strong runtime token such as ``C++`` from turning
    an explicit I/O incident into a CPU diagnosis.
    """
    query_tokens = _tokenize_search_text(query)
    eligible: list[
        tuple[DiagnosticSkillModel, int, dict, list[str], bool, bool]
    ] = []
    baseline_tool = str(target.get("_baseline_tool") or "")
    for skill in skills:
        context_score, reasons = _match_score(skill, category, target)
        category_exact = skill.category == category
        if not category_exact and category != "UNKNOWN":
            continue
        if reasons.get("environment") == "drift":
            continue
        if category_exact and context_score < _MATCH_THRESHOLD:
            continue
        # Cross-category recovery is restricted to UNKNOWN. Once the baseline
        # has a concrete category, its tool/domain boundary stays authoritative.
        compatible, conflict = _route_compatible(
            skill.category,
            baseline_tool if category_exact else "",
            target,
        )
        if not compatible:
            continue
        route = (skill.strategy_json or {}).get("probe_order") or []
        if not any(_tool_available(tool_name, target) for tool_name in route):
            continue
        trigger = skill.trigger_json or {}
        query_anchors = list(trigger.get("required_query_terms_any") or [])
        matched_anchors = _matching_query_anchors(query, query_anchors)
        if query_anchors and not matched_anchors:
            continue
        if matched_anchors:
            reasons = {**reasons, "query_anchor": matched_anchors}
        has_retrieval_document = bool(trigger.get("source_query") or trigger.get("query_terms"))
        if not category_exact and not (query_tokens and has_retrieval_document):
            continue
        eligible.append(
            (
                skill,
                context_score,
                {**reasons, **conflict},
                _tokenize_search_text(_skill_search_text(skill)),
                has_retrieval_document,
                category_exact,
            )
        )
    if not eligible:
        return []

    documents = [item[3] for item in eligible]
    bm25 = _bm25_scores(query_tokens, documents)
    ranked: list[tuple[float, dict, DiagnosticSkillModel]] = []
    for index, (
        skill,
        context_score,
        reasons,
        document_tokens,
        has_retrieval_document,
        category_exact,
    ) in enumerate(eligible):
        vector_score = _vector_similarity(query_tokens, document_tokens)
        context = context_score / 1000
        has_text_signal = bool(query_tokens and document_tokens and has_retrieval_document)
        score = (
            0.30 * context + 0.45 * bm25[index] + 0.25 * vector_score
            if has_text_signal
            else context
        )
        matched_anchors = list(reasons.get("query_anchor") or [])
        if matched_anchors:
            # An explicit runtime identity is a structured routing signal, not
            # incident evidence. It may prioritize the matching route while
            # the later Evidence gate still decides whether the cause holds.
            score = min(1.0, score + _EXPLICIT_QUERY_ANCHOR_BONUS)
        matched_terms = sorted(set(query_tokens).intersection(document_tokens))[:12]
        if not category_exact:
            if (
                not matched_terms
                or bm25[index] < _CROSS_CATEGORY_MIN_BM25
                or vector_score < _CROSS_CATEGORY_MIN_VECTOR
            ):
                continue
            reasons = {
                **reasons,
                "category_correction": {
                    "from": category,
                    "to": skill.category,
                    "guard": "STRONG_TEXT_SIGNAL",
                },
            }
        ranked.append(
            (
                score,
                {
                    **reasons,
                    "retrieval": "HYBRID_BM25_VECTOR" if has_text_signal else "STRUCTURED_FALLBACK",
                    "bm25": round(bm25[index], 4),
                    "vector": round(vector_score, 4),
                    "vector_backend": "HASHED_NGRAM_CONCEPT_VECTOR",
                    "structured": round(context, 4),
                    "matched_terms": matched_terms,
                },
                skill,
            )
        )
    return sorted(ranked, key=lambda item: (-item[0], item[2].id))


def _select_ranked_skill(
    ranked: list[tuple[float, dict, DiagnosticSkillModel]],
) -> tuple[float, dict, DiagnosticSkillModel] | None:
    """Apply the production confidence and ambiguity gates to a ranking."""
    if not ranked or ranked[0][0] < _HYBRID_MATCH_THRESHOLD:
        return None
    if len(ranked) > 1 and ranked[0][1].get("retrieval") == "HYBRID_BM25_VECTOR":
        total_margin = ranked[0][0] - ranked[1][0]
        bm25_margin = float(ranked[0][1].get("bm25") or 0) - float(
            ranked[1][1].get("bm25") or 0
        )
        vector_margin = float(ranked[0][1].get("vector") or 0) - float(
            ranked[1][1].get("vector") or 0
        )
        if (
            total_margin < _HYBRID_MIN_MARGIN
            and bm25_margin < 0.08
            and vector_margin < 0.08
        ):
            return None
    return ranked[0]


def _activation_summary(activations: list[DiagnosticSkillActivationModel]) -> dict:
    """Summarize real reuse outcomes without treating missing feedback as success.

    A small Beta prior keeps one early label from dominating retrieval. PARTIAL is
    worth half a success; pending activations affect coverage but not reliability.
    """
    counts = Counter(str(item.outcome or "PENDING").upper() for item in activations)
    total = len(activations)
    correct = counts["CORRECT"]
    partial = counts["PARTIAL"]
    wrong = counts["WRONG"]
    labeled = correct + partial + wrong
    posterior = (
        _RELIABILITY_PRIOR_SUCCESSES + correct + 0.5 * partial
    ) / (
        _RELIABILITY_PRIOR_SUCCESSES
        + _RELIABILITY_PRIOR_FAILURES
        + labeled
    )
    return {
        "activation_count": total,
        "labeled_outcome_count": labeled,
        "pending_outcome_count": total - labeled,
        "correct_outcome_count": correct,
        "partial_outcome_count": partial,
        "wrong_outcome_count": wrong,
        "outcome_coverage": round(labeled / total, 4) if total else 0.0,
        "observed_success_rate": round(
            (correct + 0.5 * partial) / labeled, 4
        ) if labeled else None,
        "posterior_reliability": round(posterior, 4),
    }


def _rank_with_observed_reliability(
    ranked: list[tuple[float, dict, DiagnosticSkillModel]],
    activations: list[DiagnosticSkillActivationModel],
) -> list[tuple[float, dict, DiagnosticSkillModel]]:
    """Let production outcomes break close retrieval ties conservatively.

    Text/context similarity still decides whether a Skill is relevant. The
    reliability factor only nudges eligible candidates by at most +/-20% and
    therefore cannot bypass category, environment, route or capability gates.
    """
    by_skill: dict[str, list[DiagnosticSkillActivationModel]] = {}
    for activation in activations:
        by_skill.setdefault(activation.skill_id, []).append(activation)
    adjusted = []
    for retrieval_score, reason, skill in ranked:
        summary = _activation_summary(by_skill.get(skill.id, []))
        labeled = summary["labeled_outcome_count"]
        reliability = summary["posterior_reliability"]
        factor = 1.0 if not labeled else 0.8 + 0.4 * reliability
        score = retrieval_score * factor
        adjusted.append((
            score,
            {
                **reason,
                "retrieval_score_before_reliability": round(retrieval_score, 4),
                "reliability_factor": round(factor, 4),
                "posterior_reliability": reliability,
                "outcome_coverage": summary["outcome_coverage"],
                "observed_outcomes": labeled,
            },
            skill,
        ))
    return sorted(adjusted, key=lambda item: (-item[0], item[2].id))


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
        activations_by_skill: dict[str, list[DiagnosticSkillActivationModel]] = {}
        for activation in activations:
            activations_by_skill.setdefault(activation.skill_id, []).append(activation)

        result = []
        for item in rows:
            payload = item.to_dict()
            payload.update(_activation_summary(activations_by_skill.get(item.id, [])))
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
        result["reuse_metrics"] = _activation_summary(
            session.query(DiagnosticSkillActivationModel)
            .filter(DiagnosticSkillActivationModel.skill_id == skill_id)
            .all()
        )
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
                "source_query": diagnosis.query,
                "query_terms": list(dict.fromkeys(_tokenize_search_text(diagnosis.query)))[:80],
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
            DiagnosticSkillEvaluationModel.skill_id == skill_id,
            DiagnosticSkillEvaluationModel.case_kind.in_([
                "POSITIVE_REPLAY",
                "MISLEADING_NEGATIVE",
                "ENVIRONMENT_DRIFT",
            ]),
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


def record_campaign_validation(
    skill_id: str,
    campaign: dict,
    *,
    recorded_by: str,
) -> dict:
    """Persist an immutable cross-environment campaign result for a Skill.

    The caller supplies observations, not the admission decision.  This
    service derives pass/fail and binds the result to the exact immutable Skill
    version so an old campaign cannot authorize a modified strategy.
    """

    request = RecordSkillCampaignValidationRequest.model_validate(campaign)
    normalized = request.model_dump(mode="json")
    normalized["environment_fingerprints"] = sorted(
        {value.strip() for value in request.environment_fingerprints}
    )
    normalized["collector_kinds"] = sorted(
        {value.strip() for value in request.collector_kinds if value.strip()}
    )
    if not normalized["collector_kinds"]:
        raise ValueError("campaign must cover at least one collector kind")

    session = new_session()
    try:
        skill = session.get(DiagnosticSkillModel, skill_id)
        if skill is None:
            raise ValueError("skill not found")
        if skill.status != "CANDIDATE":
            raise ValueError("only a CANDIDATE skill can receive campaign validation")

        fingerprint = _skill_version_fingerprint(skill)
        pass_rate = request.passed_cases / request.total_cases
        passed = bool(
            request.passed_cases == request.total_cases
            and request.false_activation_rate
            <= _CAMPAIGN_MAX_FALSE_ACTIVATION_RATE
            and request.policy_violation_count == 0
        )
        details = {
            **normalized,
            "recorded_by": recorded_by.strip() or "unknown",
            "skill_version_fingerprint": fingerprint,
            "pass_rate": round(pass_rate, 6),
            "max_false_activation_rate": _CAMPAIGN_MAX_FALSE_ACTIVATION_RATE,
        }

        existing = (
            session.query(DiagnosticSkillEvaluationModel)
            .filter(
                DiagnosticSkillEvaluationModel.skill_id == skill_id,
                DiagnosticSkillEvaluationModel.case_kind == _CAMPAIGN_CASE_KIND,
            )
            .order_by(DiagnosticSkillEvaluationModel.created_at.desc())
            .all()
        )
        same_campaign = next(
            (
                item
                for item in existing
                if (item.details_json or {}).get("campaign_id")
                == request.campaign_id
            ),
            None,
        )
        if same_campaign is not None:
            if (same_campaign.details_json or {}) != details:
                raise ValueError("campaign_id already exists with different evidence")
            return get_skill(skill_id) or {}

        timestamp = _now()
        session.add(
            DiagnosticSkillEvaluationModel(
                id=f"skill_eval_{uuid4().hex}",
                skill_id=skill_id,
                case_kind=_CAMPAIGN_CASE_KIND,
                diagnosis_id=None,
                passed=passed,
                score=int(round(pass_rate * 1000)),
                details_json=details,
                created_at=timestamp,
            )
        )
        metrics = dict(skill.gate_metrics_json or {})
        metrics["campaign_validation"] = {
            "campaign_id": request.campaign_id,
            "passed": passed,
            "pass_rate": round(pass_rate, 6),
            "environment_count": len(normalized["environment_fingerprints"]),
            "report_sha256": request.report_sha256.lower(),
        }
        skill.gate_metrics_json = metrics
        skill.updated_at = timestamp
        session.commit()
        return get_skill(skill_id) or {}
    finally:
        session.close()


def publish_skill(skill_id: str) -> dict:
    session = new_session()
    try:
        skill = session.get(DiagnosticSkillModel, skill_id)
        if skill is None:
            raise ValueError("skill not found")
        if not (skill.gate_metrics_json or {}).get("eligible"):
            raise ValueError("技能尚未通过正例、误导反例和环境漂移门禁")
        campaign = (
            session.query(DiagnosticSkillEvaluationModel)
            .filter(
                DiagnosticSkillEvaluationModel.skill_id == skill_id,
                DiagnosticSkillEvaluationModel.case_kind == _CAMPAIGN_CASE_KIND,
            )
            .order_by(DiagnosticSkillEvaluationModel.created_at.desc())
            .first()
        )
        if campaign is None:
            raise ValueError("技能尚未登记跨环境 Campaign 评测，不能发布")
        if not campaign.passed:
            raise ValueError("最新跨环境 Campaign 评测未通过，不能发布")
        campaign_details = campaign.details_json or {}
        if campaign_details.get("skill_version_fingerprint") != _skill_version_fingerprint(skill):
            raise ValueError("Campaign 评测与当前 Skill 版本不匹配")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(campaign_details.get("report_sha256") or "")):
            raise ValueError("Campaign 评测缺少有效报告摘要")
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


def _upsert_reuse_trace(existing: list[dict], step: dict) -> list[dict]:
    """Keep one deterministic Skill decision per diagnosis round and phase."""

    trace_key = str(step.get("trace_key") or "")
    previous = next(
        (
            dict(item)
            for item in existing
            if str((item or {}).get("trace_key") or "") == trace_key
        ),
        None,
    )
    if previous is not None:
        previous_state = str(previous.get("state") or "").upper()
        next_state = str(step.get("state") or "").upper()
        # Retrying the same idempotent planner phase must not rewrite the
        # historical first activation into a synthetic reuse.  Terminal route
        # transitions still win because they explain why execution stopped.
        if previous_state in {"ACTIVATED", "SWITCHED"} and next_state == "REUSED":
            step = {
                **step,
                "state": previous_state,
                "applied_at": previous.get("applied_at") or step.get("applied_at"),
            }
    retained = [
        dict(item)
        for item in existing
        if str((item or {}).get("trace_key") or "") != trace_key
    ]
    retained.append(step)
    retained.sort(
        key=lambda item: (
            int(item.get("round_index") or 0),
            str(item.get("phase") or ""),
            str(item.get("trace_key") or ""),
        )
    )
    return retained[-32:]


def _no_skill_decision(
    *,
    state: str,
    exit_reason: str,
    round_index: int | None,
    phase: str,
    category: str,
    baseline_tool: str,
    skill: DiagnosticSkillModel | None = None,
    reuse_step: dict | None = None,
    reuse_trace: list[dict] | None = None,
    skill_instructions: dict | None = None,
) -> dict:
    skill_category = skill.category if skill is not None else None
    skill_name = (
        skill_instructions.get("name")
        if isinstance(skill_instructions, dict)
        else skill.family_key if skill is not None else None
    )
    load_mode = (
        skill_instructions.get("load_mode")
        if isinstance(skill_instructions, dict)
        else (
            "REPOSITORY_INSTRUCTION_REJECTED"
            if skill is not None
            and (skill.trigger_json or {}).get("repository_builtin") is True
            else "STRUCTURED_STRATEGY_ONLY" if skill is not None else None
        )
    )
    return {
        "applied": False,
        "state": state,
        "exit_reason": exit_reason,
        "skill_id": skill.id if skill is not None else None,
        "skill_name": skill_name,
        "version": skill.version if skill is not None else None,
        "category": skill_category,
        "skill_category": skill_category,
        "baseline_category": category,
        "selected_category": skill_category,
        "requested_category": category,
        "baseline_tool": baseline_tool,
        "selected_tool": None,
        "round_index": round_index,
        "phase": str(phase or "PLANNER").strip().upper()[:64],
        "reuse_step": reuse_step,
        "reuse_trace": reuse_trace or [],
        "skill_instructions": skill_instructions,
        "load_mode": load_mode,
    }


def get_persisted_skill_context(
    diagnosis_id: str,
    *,
    skill_id: str | None = None,
) -> dict | None:
    """Return the exact Skill context previously persisted for a diagnosis.

    This lets later planners keep stop/refutation instructions after the route
    has no remaining executable tool, without manufacturing another
    activation.  The payload is the immutable snapshot used at activation,
    not a fresh unchecked file read.
    """

    session = new_session()
    try:
        query = session.query(DiagnosticSkillActivationModel).filter(
            DiagnosticSkillActivationModel.diagnosis_id == diagnosis_id
        )
        if skill_id is not None:
            query = query.filter(DiagnosticSkillActivationModel.skill_id == skill_id)
        activation = query.order_by(
            DiagnosticSkillActivationModel.updated_at.desc(),
            DiagnosticSkillActivationModel.created_at.desc(),
        ).first()
        if activation is None:
            return None
        reason = dict(activation.match_reason_json or {})
        skill = session.get(DiagnosticSkillModel, activation.skill_id)
        instructions = reason.get("skill_instructions")
        selected_category = reason.get("skill_category") or (
            skill.category if skill else None
        )
        return {
            "activation_id": activation.id,
            "skill_id": activation.skill_id,
            "skill_name": (
                instructions.get("name")
                if isinstance(instructions, dict)
                else skill.family_key if skill else None
            ),
            "version": reason.get("skill_version") or (skill.version if skill else None),
            "category": selected_category,
            "skill_category": selected_category,
            "baseline_category": reason.get("requested_category"),
            "selected_category": selected_category,
            "match_score": activation.match_score / 1000,
            "baseline_tool": activation.baseline_tool,
            "selected_tool": activation.selected_tool,
            "probe_order": list(reason.get("route") or []),
            "skill_instructions": instructions,
            "load_mode": (
                instructions.get("load_mode")
                if isinstance(instructions, dict)
                else "STRUCTURED_STRATEGY_ONLY" if skill else None
            ),
            "reuse_trace": list(
                reason.get("reuse_trace") or reason.get("applications") or []
            ),
            "outcome": activation.outcome,
        }
    finally:
        session.close()


def apply_active_skill(
    diagnosis_id: str,
    category: str,
    plan: dict,
    target: dict,
    *,
    round_index: int | None = None,
    phase: str = "INITIAL_PLAN",
    attempted_tools: list[str] | set[str] | tuple[str, ...] | None = None,
    available_tools: list[str] | set[str] | tuple[str, ...] | None = None,
    reuse_existing: bool = True,
    return_decision: bool = False,
) -> dict | None:
    """Retrieve a route Skill and persist an auditable per-round application.

    ``category`` is a planner prior rather than a hard fact.  Strong lexical
    evidence may select a Skill from another category, while environment,
    explicit query anchors, target subsystem and collector availability remain
    hard gates.  Repository Markdown is hash-verified before it enters the
    trusted model context.
    """

    baseline_tool = plan["tool_name"]
    session = new_session()
    try:
        skills = (
            session.query(DiagnosticSkillModel)
            .filter(DiagnosticSkillModel.status == "ACTIVE")
            .all()
        )
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        query = diagnosis.query if diagnosis is not None else str(target.get("query") or "")
        ranking_target = {**target, "_baseline_tool": baseline_tool}
        if "collector_capabilities" not in ranking_target:
            agent_id = str(
                ranking_target.get("agent_id")
                or ((diagnosis.target_json or {}).get("agent_id") if diagnosis else "")
                or ""
            )
            agent = session.get(AgentModel, agent_id) if agent_id else None
            if agent is not None:
                ranking_target["collector_capabilities"] = list(agent.capabilities or [])

        existing_activations = (
            session.query(DiagnosticSkillActivationModel)
            .filter(DiagnosticSkillActivationModel.diagnosis_id == diagnosis_id)
            .all()
        )
        existing_skill_ids = {item.skill_id for item in existing_activations}
        if not reuse_existing and existing_skill_ids:
            skills = [item for item in skills if item.id not in existing_skill_ids]
        ranked = _rank_hybrid_skills(skills, category, ranking_target, query)
        skill_ids = [item[2].id for item in ranked]
        historical_activations = (
            session.query(DiagnosticSkillActivationModel)
            .filter(DiagnosticSkillActivationModel.skill_id.in_(skill_ids))
            .all()
            if skill_ids else []
        )
        ranked = _rank_with_observed_reliability(ranked, historical_activations)
        # A diagnosis that already activated a reviewed Skill should finish the
        # remaining safe route before ordinary re-ranking can move it to an
        # unrelated family.  Re-ranking is still used as a hard eligibility
        # gate (environment, runtime anchors and collector availability), while
        # ``reuse_existing=False`` remains the explicit escape hatch for
        # counter-evidence or human-directed direction changes.
        persisted_route_selection = None
        normalized_phase_hint = str(phase or "PLANNER").strip().upper()[:64]
        try:
            is_follow_up_round = int(round_index or 1) > 1
        except (TypeError, ValueError):
            is_follow_up_round = normalized_phase_hint != "INITIAL_PLAN"
        is_follow_up_round = (
            is_follow_up_round or normalized_phase_hint != "INITIAL_PLAN"
        )
        if reuse_existing and existing_activations and is_follow_up_round:
            completed_for_continuity = {
                item.tool_name
                for item in session.query(DropInsightToolCallModel)
                .filter(
                    DropInsightToolCallModel.diagnosis_id == diagnosis_id,
                    DropInsightToolCallModel.status == "COMPLETED",
                )
                .all()
            }
            excluded_for_continuity = completed_for_continuity.union(
                str(item) for item in (attempted_tools or []) if str(item)
            )
            explicit_available_for_continuity = (
                {str(item) for item in available_tools if str(item)}
                if available_tools is not None
                else None
            )
            ranked_by_skill_id = {item[2].id: item for item in ranked}
            active_skills_by_id = {item.id: item for item in skills}
            ordered_existing = sorted(
                existing_activations,
                key=lambda item: (item.updated_at, item.created_at, item.id),
                reverse=True,
            )
            for existing_activation in ordered_existing:
                ranked_existing = ranked_by_skill_id.get(
                    existing_activation.skill_id
                )
                if ranked_existing is None:
                    # New cross-category activation is forbidden once the
                    # planner has a concrete category. An already activated
                    # route is different: a fallback replan may temporarily
                    # use another category while the reviewed route still has
                    # safe steps left. Re-check its original hard gates and
                    # allow continuity without treating it as a new match.
                    existing_skill = active_skills_by_id.get(
                        existing_activation.skill_id
                    )
                    if existing_skill is None:
                        continue
                    context_score, continuity_reason = _match_score(
                        existing_skill,
                        existing_skill.category,
                        ranking_target,
                    )
                    if (
                        context_score < _MATCH_THRESHOLD
                        or continuity_reason.get("environment") == "drift"
                    ):
                        continue
                    compatible, conflict = _route_compatible(
                        existing_skill.category,
                        "",
                        ranking_target,
                    )
                    if not compatible:
                        continue
                    ranked_existing = (
                        context_score / 1000,
                        {
                            **continuity_reason,
                            **conflict,
                            "retrieval": "PERSISTED_ROUTE_CONTINUITY",
                        },
                        existing_skill,
                    )
                existing_skill = ranked_existing[2]
                existing_route = list(
                    (existing_skill.strategy_json or {}).get("probe_order") or []
                )
                has_remaining_route = any(
                    tool_name not in excluded_for_continuity
                    and _tool_available(tool_name, ranking_target)
                    and (
                        explicit_available_for_continuity is None
                        or tool_name in explicit_available_for_continuity
                    )
                    for tool_name in existing_route
                )
                if not has_remaining_route:
                    continue
                continuity_score = max(
                    ranked_existing[0],
                    existing_activation.match_score / 1000,
                )
                persisted_route_selection = (
                    continuity_score,
                    {
                        **ranked_existing[1],
                        "continuity_guard": (
                            "PERSISTED_ROUTE_HAS_REMAINING_SAFE_TOOL"
                        ),
                    },
                    existing_skill,
                )
                break
        if reuse_existing and existing_skill_ids:
            ranked = sorted(
                (
                    (
                        min(1.0, score + (0.05 if skill.id in existing_skill_ids else 0.0)),
                        {
                            **reason,
                            "existing_activation_bonus": (
                                0.05 if skill.id in existing_skill_ids else 0.0
                            ),
                        },
                        skill,
                    )
                    for score, reason, skill in ranked
                ),
                key=lambda item: (-item[0], item[2].id),
            )
        selected = persisted_route_selection or _select_ranked_skill(ranked)
        if selected is None:
            if existing_activations:
                previous = max(
                    existing_activations,
                    key=lambda item: (item.updated_at, item.created_at, item.id),
                )
                previous_skill = session.get(DiagnosticSkillModel, previous.skill_id)
                previous_reason = dict(previous.match_reason_json or {})
                previous_trace = list(
                    previous_reason.get("reuse_trace")
                    or previous_reason.get("applications")
                    or []
                )
                try:
                    deviated_round = max(
                        1,
                        int(round_index or len(previous_trace) + 1),
                    )
                except (TypeError, ValueError):
                    deviated_round = len(previous_trace) + 1
                normalized_phase = str(phase or "PLANNER").strip().upper()[:64]
                completed_tools = {
                    item.tool_name
                    for item in session.query(DropInsightToolCallModel)
                    .filter(
                        DropInsightToolCallModel.diagnosis_id == diagnosis_id,
                        DropInsightToolCallModel.status == "COMPLETED",
                    )
                    .all()
                }
                excluded_tools = completed_tools.union(
                    str(item) for item in (attempted_tools or []) if str(item)
                )
                explicit_available = (
                    {str(item) for item in available_tools if str(item)}
                    if available_tools is not None
                    else None
                )
                previous_route = list(previous_reason.get("route") or [])
                remaining_route = [
                    tool_name
                    for tool_name in previous_route
                    if tool_name not in excluded_tools
                    and _tool_available(tool_name, ranking_target)
                    and (
                        explicit_available is None
                        or tool_name in explicit_available
                    )
                ]
                if not reuse_existing:
                    exit_state = "DEVIATED"
                    exit_reason = "PREVIOUS_SKILL_EXCLUDED_BY_REPLAN"
                elif previous_route and not remaining_route:
                    # Retrieval may have hard-filtered the old Skill before
                    # route selection (for example after the runtime
                    # capability set changed).  Preserve its already trusted
                    # stop/refutation context and explicitly close the route.
                    exit_state = "EXHAUSTED"
                    exit_reason = "NO_REMAINING_AVAILABLE_ROUTE_TOOL"
                else:
                    exit_state = "DEVIATED"
                    exit_reason = "SKILL_NO_LONGER_ELIGIBLE_OR_UNAMBIGUOUS"
                exit_step = {
                    "trace_key": f"{deviated_round}:{normalized_phase}",
                    "round_index": deviated_round,
                    "phase": normalized_phase,
                    "state": exit_state,
                    "baseline_tool": baseline_tool,
                    "selected_tool": None,
                    "completed_route_tools": sorted(completed_tools),
                    "attempted_tools": sorted(excluded_tools),
                    "category_before": category,
                    "category_after": (
                        previous_skill.category if previous_skill is not None else None
                    ),
                    "category_corrected": bool(
                        previous_skill is not None
                        and previous_skill.category != category
                    ),
                    "exit_reason": exit_reason,
                    "applied_at": _now().isoformat(),
                }
                persisted_trace = _upsert_reuse_trace(previous_trace, exit_step)
                previous.match_reason_json = {
                    **previous_reason,
                    "current_selected_tool": None,
                    "reuse_trace": persisted_trace,
                    "applications": persisted_trace,
                }
                previous.updated_at = _now()
                session.commit()
                if return_decision:
                    return _no_skill_decision(
                        state=exit_state,
                        exit_reason=exit_reason,
                        round_index=deviated_round,
                        phase=normalized_phase,
                        category=category,
                        baseline_tool=baseline_tool,
                        skill=previous_skill,
                        reuse_step=exit_step,
                        reuse_trace=persisted_trace,
                        skill_instructions=previous_reason.get("skill_instructions"),
                    )
            if not return_decision:
                return None
            return _no_skill_decision(
                state="NOT_MATCHED",
                exit_reason=(
                    "NO_ELIGIBLE_SKILL"
                    if not ranked
                    else "BELOW_THRESHOLD_OR_AMBIGUOUS"
                ),
                round_index=round_index,
                phase=phase,
                category=category,
                baseline_tool=baseline_tool,
            )
        score_value, reasons, skill = selected
        score = int(round(score_value * 1000))
        route = (skill.strategy_json or {}).get("probe_order") or []
        completed_calls = (
            session.query(DropInsightToolCallModel)
            .filter(
                DropInsightToolCallModel.diagnosis_id == diagnosis_id,
                DropInsightToolCallModel.status == "COMPLETED",
            )
            .order_by(DropInsightToolCallModel.created_at.asc())
            .all()
        )
        completed_tools = {item.tool_name for item in completed_calls}
        excluded_tools = completed_tools.union(
            str(item) for item in (attempted_tools or []) if str(item)
        )
        explicit_available = (
            {str(item) for item in available_tools if str(item)}
            if available_tools is not None
            else None
        )
        inferred_round = len(completed_calls) + 1
        try:
            reuse_round = max(1, int(round_index or inferred_round))
        except (TypeError, ValueError):
            reuse_round = inferred_round
        normalized_phase = str(phase or "PLANNER").strip().upper()[:64]
        skill_instructions = None
        if (skill.trigger_json or {}).get("repository_builtin") is True:
            from .builtin_skills import load_repository_skill_instructions

            try:
                skill_instructions = load_repository_skill_instructions(skill)
            except (OSError, ValueError, UnicodeError):
                # A repository body that no longer matches its seeded digest is
                # not safe model context.  Fall back to the baseline planner.
                if not return_decision:
                    return None
                return _no_skill_decision(
                    state="REJECTED",
                    exit_reason="REPOSITORY_INSTRUCTION_INTEGRITY_FAILED",
                    round_index=reuse_round,
                    phase=normalized_phase,
                    category=category,
                    baseline_tool=baseline_tool,
                    skill=skill,
                )
        selected_tool = next(
            (
                tool_name
                for tool_name in route
                if tool_name not in excluded_tools
                and _tool_available(tool_name, ranking_target)
                and (explicit_available is None or tool_name in explicit_available)
            ),
            None,
        )
        if selected_tool is None:
            exit_step = {
                "trace_key": f"{reuse_round}:{normalized_phase}",
                "round_index": reuse_round,
                "phase": normalized_phase,
                "state": "EXHAUSTED",
                "baseline_tool": baseline_tool,
                "selected_tool": None,
                "completed_route_tools": sorted(completed_tools),
                "attempted_tools": sorted(excluded_tools),
                "category_before": category,
                "category_after": skill.category,
                "category_corrected": skill.category != category,
                "exit_reason": "NO_REMAINING_AVAILABLE_ROUTE_TOOL",
                "applied_at": _now().isoformat(),
            }
            existing = next(
                (item for item in existing_activations if item.skill_id == skill.id),
                None,
            )
            reuse_trace: list[dict] = []
            if existing is not None:
                previous_reason = dict(existing.match_reason_json or {})
                reuse_trace = _upsert_reuse_trace(
                    list(
                        previous_reason.get("reuse_trace")
                        or previous_reason.get("applications")
                        or []
                    ),
                    exit_step,
                )
                existing.match_reason_json = {
                    **previous_reason,
                    "current_selected_tool": None,
                    "reuse_trace": reuse_trace,
                    "applications": reuse_trace,
                }
                existing.updated_at = _now()
                session.commit()
            if not return_decision:
                return None
            return _no_skill_decision(
                state="EXHAUSTED",
                exit_reason="NO_REMAINING_AVAILABLE_ROUTE_TOOL",
                round_index=reuse_round,
                phase=normalized_phase,
                category=category,
                baseline_tool=baseline_tool,
                skill=skill,
                reuse_step=exit_step,
                reuse_trace=reuse_trace,
                skill_instructions=skill_instructions,
            )

        timestamp = _now()
        route_index = route.index(selected_tool) + 1
        existing = next(
            (item for item in existing_activations if item.skill_id == skill.id),
            None,
        )
        activation_state = (
            "REUSED"
            if existing is not None
            else "SWITCHED"
            if existing_activations
            else "ACTIVATED"
        )
        reuse_step = {
            "trace_key": f"{reuse_round}:{normalized_phase}",
            "round_index": reuse_round,
            "phase": normalized_phase,
            "state": activation_state,
            "baseline_tool": baseline_tool,
            "selected_tool": selected_tool,
            "selected_route_index": route_index,
            "route_length": len(route),
            "completed_route_tools": sorted(completed_tools),
            "attempted_tools": sorted(excluded_tools),
            "category_before": category,
            "category_after": skill.category,
            "category_corrected": skill.category != category,
            "applied_at": timestamp.isoformat(),
        }
        previous_reason = dict(existing.match_reason_json or {}) if existing else {}
        previous_trace = list(
            previous_reason.get("reuse_trace")
            or previous_reason.get("applications")
            or []
        )
        reuse_trace = _upsert_reuse_trace(previous_trace, reuse_step)
        persisted_reason = {
            **reasons,
            "route": route,
            "skill_version": skill.version,
            "skill_category": skill.category,
            "completed_route_tools": sorted(completed_tools),
            "current_selected_tool": selected_tool,
            "applications": reuse_trace,
            "reuse_trace": reuse_trace,
            "skill_instructions": skill_instructions,
        }
        activation = DiagnosticSkillActivationModel(
            id=f"skill_activation_{uuid4().hex}", skill_id=skill.id,
            diagnosis_id=diagnosis_id, match_score=score,
            match_reason_json=persisted_reason,
            baseline_tool=baseline_tool, selected_tool=selected_tool,
            created_at=timestamp, updated_at=timestamp,
        )
        if existing is None:
            session.add(activation)
            session.commit()
        else:
            existing.match_score = score
            existing.match_reason_json = persisted_reason
            existing.baseline_tool = baseline_tool
            existing.selected_tool = selected_tool
            existing.updated_at = timestamp
            session.commit()
        plan["tool_name"] = selected_tool
        return {
            "applied": True,
            "state": activation_state,
            "skill_id": skill.id,
            "skill_name": (
                skill_instructions.get("name")
                if isinstance(skill_instructions, dict)
                else skill.family_key
            ),
            "version": skill.version,
            "match_score": score / 1000,
            "match_reason": reasons,
            "category": skill.category,
            "baseline_category": category,
            "selected_category": skill.category,
            "baseline_tool": baseline_tool,
            "selected_tool": selected_tool,
            "probe_order": route,
            "skill_category": skill.category,
            "category_correction": reasons.get("category_correction"),
            "skill_instructions": skill_instructions,
            "load_mode": (
                skill_instructions.get("load_mode")
                if isinstance(skill_instructions, dict)
                else "STRUCTURED_STRATEGY_ONLY"
            ),
            "reuse_step": reuse_step,
            "reuse_trace": reuse_trace,
        }
    finally:
        session.close()


def record_activation_outcome(diagnosis_id: str, feedback_label: str) -> None:
    session = new_session()
    try:
        activations = session.query(DiagnosticSkillActivationModel).filter(
            DiagnosticSkillActivationModel.diagnosis_id == diagnosis_id
        ).all()
        if not activations:
            return
        timestamp = _now()
        for activation in activations:
            activation.outcome = feedback_label.upper()
            activation.updated_at = timestamp
        session.commit()
        for skill_id in {item.skill_id for item in activations}:
            skill_activations = session.query(DiagnosticSkillActivationModel).filter(
                DiagnosticSkillActivationModel.skill_id == skill_id
            ).all()
            summary = _activation_summary(skill_activations)
            total = summary["labeled_outcome_count"]
            wrong = summary["wrong_outcome_count"]
            skill = session.get(DiagnosticSkillModel, skill_id)
            if skill is not None:
                metrics = dict(skill.gate_metrics_json or {})
                metrics["production_reuse"] = summary
                skill.gate_metrics_json = metrics
                skill.updated_at = _now()
            if total >= 2 and wrong >= 2 and wrong / total >= 0.5:
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
