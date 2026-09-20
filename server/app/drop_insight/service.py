"""Drop Insight 领域服务：把一次 AI 调查推进成可审计的持久化状态机。

这个文件是诊断主链路的业务权威，不是 HTTP handler，也不是自由执行的 Agent。
它负责目标绑定、假设、策略与预算门禁、工具调用、Evidence 准入、报告和反馈；
模型只能提出选择，真正的权限、状态迁移和副作用都在这里复核并写入数据库。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import NoReturn
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from server.app.database import new_session
from server.app.agent_runtime.retrieval import build_retrieval_trace
from server.app.agent_runtime.deadlines import probe_deadline_check
from server.app.models import (
    AgentModel,
    AnalysisJobModel,
    ArtifactModel,
    DropInsightEventModel,
    DropInsightEvidenceModel,
    DropInsightFeedbackModel,
    DropInsightHypothesisModel,
    DropInsightReportModel,
    DropInsightSessionModel,
    DropInsightTargetBindingModel,
    DropInsightTargetDiscoveryModel,
    DropInsightToolCallModel,
    FixVerificationModel,
    OutboxMessageModel,
    ProcessCandidateModel,
    ProcessCandidateSnapshotModel,
    TaskAttemptModel,
    TaskModel,
    TaskUploadAuthorizationModel,
)
from server.app.state_machine import Actor, TaskStatus, now_utc

from .evidence import EvidenceEnvelope, calibrate_confidence, classify_evidence
from .artifact_evidence import assess_artifact_evidence
from .claim_verifier import generate_sre_remediation_advice, verify_report_claims
from .policy import PolicyContext, evaluate_tool_call
from .tools import TOOLS, TOOL_TO_COLLECTOR
from server.app.artifact_contracts import CONTRACT_VERSION
from .schemas import (
    AddEvidenceRequest,
    ClarificationTarget,
    ClarifyDiagnosisRequest,
    CreateDiagnosisRequestV2,
    CreateHypothesisRequest,
    CreateToolCallRequest,
    DecideToolCallRequest,
    GenerateReportRequest,
    ImportTaskEvidenceRequest,
    InterveneDiagnosisRequest,
    PreviewToolCallRequest,
    RunPlannerRequest,
    SubmitDiagnosisFeedbackRequest,
    DiagnosticTimeRange,
)
from server.app.schemas import CreateTaskRequest, ProcessIdentityBindingRequest
from server.app.generated.taskkind_contract import TASK_KINDS
from server.app.process_attestation import (
    PROCESS_SNAPSHOT_MAX_AGE,
    ProcessIdentityBinding,
)
from server.app.sql_repository import SqlRepository
from server.app.storage import presigned_put_url
from server.app.drop_insight.source_mapper import map_hot_functions
from .adaptive_planner import propose_hypothesis_plan
from .lats import (
    CONFIDENCE_SCALE,
    LATSConfig,
    execution_semantics,
    hypothesis_path,
    order_progressive_frontier,
    prepare_candidates,
    reflection_from_outcome,
    replay_search_events,
    reward_from_outcome,
    select_puct_candidate,
    stable_candidate_key,
)
from .rounds import report_execution_rounds
from .event_store import (
    _SESSION_TRANSITIONS,
    _append_event,
    _cas_session_update,
    _enqueue_diagnosis_event,
    _event_semantic_scope,
    _freeze_event_value,
    _latest_semantic_event_has_payload,
    _lock_diagnosis,
)
from .fix_verification import (
    FIX_VERIFY_RELATIVE_THRESHOLD,
    _as_utc_with_timezone,
    _fix_view,
    _parse_datetime,
    _task_top_functions,
    _time_ranges_overlap,
    compare_before_after,
    list_fix_verifications,
    verify_diagnosis_fix,
)
from .hypothesis_predicate import (
    _compute_hypothesis_predicate,
    _criterion_text_indexes,
    _derive_imported_evidence_role,
    _safe_percent,
    _structured_signal_predicate,
)
from .report_conclusion import (
    _concrete_report_finding,
    _derive_next_actions,
    _derive_report_conclusion,
)


logger = logging.getLogger(__name__)
_REPORT_EFFECT_LEASE = timedelta(minutes=5)
_REPORT_EFFECT_RECONCILIATION = ContextVar(
    "drop_insight_report_effect_reconciliation",
    default=False,
)


class _StaleReportEffectAuthority(RuntimeError):
    pass


_TARGET_DISCOVERY_TTL = timedelta(seconds=60)
_AGENT_HEARTBEAT_MAX_AGE = timedelta(
    seconds=max(1, int(os.getenv("AGENT_OFFLINE_TIMEOUT_SEC", "30")))
)

_AUTO_SCOPE_STOP_WORDS = {
    "cpu", "io", "service", "the", "this", "please", "recent", "minutes",
    "minute", "high", "定位", "原因", "最近", "分钟", "服务", "飙高", "异常",
}
_AUTO_SCOPE_ALIASES = {
    "订单": ("order", "orders"),
    "支付": ("payment", "pay"),
    "用户": ("user", "account"),
    "库存": ("inventory", "stock"),
    "网关": ("gateway", "api-gateway"),
    "数据库": ("database", "mysql", "postgres"),
    "缓存": ("cache", "redis"),
    "python": ("python",),
    "java": ("java", "jvm"),
    "go": ("golang", "go-"),
}

_DATABASE_QUERY_TOKENS = (
    "数据库锁", "deadlock", "mysql lock", "postgres lock", "db lock",
)


def _is_database_query(query: str) -> bool:
    lowered = query.casefold()
    if any(token in lowered for token in _DATABASE_QUERY_TOKENS):
        return True
    # “锁等待”也会出现在 JVM/C++ 诊断的反证描述里。只有查询同时明确
    # 提到数据库或数据库引擎时，才允许它把自动发现范围收窄到数据库。
    return "锁等待" in lowered and any(
        marker in lowered
        for marker in ("数据库", "mysql", "postgres", "database", " db ")
    )


def _scope_questions(target: dict | None, time_range: dict | None) -> list[dict]:
    """Return scope gaps without accepting raw client process authority."""

    target = target or {}
    questions = []
    fields = (
        ("target.service", "service", "要诊断哪个服务？"),
        ("target.environment", "environment", "目标属于哪个环境？"),
        (
            "target.binding",
            "process_binding",
            "请从服务端发现的进程候选中安全绑定诊断目标。",
        ),
    )
    for question_id, key, prompt in fields:
        if not target.get(key):
            questions.append({"question_id": question_id, "prompt": prompt})
    if not (time_range or {}).get("start") or not (time_range or {}).get("end"):
        questions.append({"question_id": "time_range", "prompt": "故障发生在哪个时间范围？"})
    return questions


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _candidate_matches_filter(candidate, service: str | None, environment: str | None) -> bool:
    service_text = " ".join(
        str(value or "")
        for value in (
            candidate.service_hint,
            candidate.comm,
            candidate.executable_identity,
            candidate.cgroup,
        )
    ).casefold()
    environment_text = " ".join(
        str(value or "")
        for value in (
            candidate.instance_hint,
            candidate.cgroup,
            candidate.service_hint,
        )
    ).casefold()
    return (
        not service or service.casefold() in service_text
    ) and (
        not environment or environment.casefold() in environment_text
    )


def _auto_scope_tokens(query: str) -> set[str]:
    lowered = query.casefold()
    tokens = {
        token
        for token in re.findall(r"[a-z][a-z0-9_.-]{1,127}", lowered)
        if token not in _AUTO_SCOPE_STOP_WORDS
    }
    for phrase, aliases in _AUTO_SCOPE_ALIASES.items():
        present = (
            re.search(rf"\b{re.escape(phrase)}\b", lowered) is not None
            if phrase.isascii()
            else phrase in lowered
        )
        if present:
            tokens.update(aliases)
    return tokens


def _auto_scope_score(query: str, candidate: dict) -> int:
    haystack = " ".join(
        str(candidate.get(key) or "")
        for key in ("service", "environment", "instance", "process")
    ).casefold()
    score = 0
    for token in _auto_scope_tokens(query):
        if token == haystack:
            score += 100
        elif token in haystack:
            score += 20
    return score


def _explicit_process_name(query: str) -> str | None:
    match = re.search(
        r"(?:进程名(?:为|是)?|process(?:\s+name)?(?:\s+is|\s*[:=])?)\s*"
        r"([a-z][a-z0-9_.-]{0,127})",
        query.casefold(),
    )
    return match.group(1) if match else None


def _auto_scope_service_filter(query: str) -> str | None:
    """Extract an explicit machine-style service name, when the user gave one."""

    # An explicit process name must be resolved against the process snapshot,
    # not converted into a service filter.  More importantly, free-form dotted
    # tokens such as ``FileChannel.force`` are method names, not necessarily
    # service identities.  Treating every dotted token as a service used to
    # turn an otherwise valid Java diagnosis into an EMPTY discovery forever.
    if _explicit_process_name(query):
        return None
    if _is_database_query(query):
        return os.getenv("MINI_DROP_DATABASE_SERVICE", "mini-drop-postgres").strip() or None

    lowered = query.casefold()
    explicit_service_patterns = (
        r"(?:环境中(?:的)?|诊断|检查)\s*"
        r"([a-z][a-z0-9]*(?:[-_][a-z0-9]+)+)(?![a-z0-9_.-])",
        r"(?:服务名|service(?:\s+name)?)\s*(?:为|是|[:=])?\s*"
        r"([a-z][a-z0-9_.-]{0,127})",
        r"([a-z][a-z0-9_.-]{0,127})\s*(?:服务|service)\b",
    )
    for pattern in explicit_service_patterns:
        match = re.search(pattern, lowered)
        if match:
            return match.group(1)
    return None


def _auto_scope_capability_score(query: str, candidate: dict) -> int:
    lowered = query.casefold()
    capabilities = {
        str(value).casefold()
        for value in (candidate.get("collector_capabilities") or [])
    }
    score = 0
    requested = (
        (("cpu", "负载", "系统"), {"sys_metrics", "perf_cpu", "continuous_perf"}),
        (("内存", "memory", "rss", "oom"), {"memory_smaps", "sys_metrics"}),
        (("磁盘", "disk", "io", "i/o"), {"ebpf_io", "sys_metrics"}),
        (("python", "py-spy"), {"pyspy"}),
        (("java", "jvm"), {"java_async"}),
        (("golang", "go ", "pprof"), {"go_pprof"}),
    )
    for keywords, expected in requested:
        if any(keyword in lowered for keyword in keywords):
            score += 8 * len(capabilities.intersection(expected))
    identity = " ".join(
        str(candidate.get(key) or "")
        for key in ("service", "instance", "process")
    ).casefold()
    if "mini-drop" in identity or "drop_agent" in identity or "drop-agent" in identity:
        score += 6
    elif "agent" in identity:
        score += 3
    return score


def _select_auto_scope_candidate(
    query: str,
    discovery: dict,
    *,
    diagnosis_id: str | None = None,
) -> dict | None:
    candidates = [item for item in discovery.get("candidates", []) if item.get("eligible")]
    if not candidates:
        return None

    # An explicit process name is part of the user's scope authority.  When it
    # identifies one fresh eligible candidate, bind it before asking the model
    # to rank unrelated processes.  This keeps autonomous scope selection
    # deterministic for requests such as “进程名为 java”, while ambiguous
    # duplicate process names still fall through to the normal scorer/model.
    requested_process = _explicit_process_name(query)
    if requested_process:
        exact_matches = [
            item
            for item in candidates
            if str(item.get("process") or "").casefold() == requested_process
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]
        # An explicit but absent process is not permission to select a worker
        # of the same language. Multiple exact matches may be ranked below.
        if not exact_matches:
            return None
        candidates = exact_matches

    # A concrete machine-style name can be followed by a symptom rather than
    # the word 'service', e.g. 'python-hotspot 入口请求被拒绝'. The old service
    # regex missed that form and the model could choose our own Python worker.
    # Narrow to names actually present in the trusted candidate set first.
    lowered = query.casefold()
    mentioned = []
    for candidate in candidates:
        names = {str(candidate.get(key) or "").casefold() for key in ("service", "process")}
        if any(
            ("-" in name or "_" in name)
            and re.search(r"(?<![a-z0-9_.-])" + re.escape(name) + r"(?![a-z0-9_.-])", lowered)
            for name in names if name
        ):
            mentioned.append(candidate)
    if mentioned:
        candidates = mentioned
    if len(candidates) == 1:
        return candidates[0]
    if _is_database_query(query):
        postgres = [
            item for item in candidates
            if "postgres" in str(item.get("process") or "").casefold()
        ]
        instances = {str(item.get("instance") or "") for item in postgres}
        if postgres and len(instances) == 1:
            return sorted(
                postgres, key=lambda item: str(item.get("binding_id") or "")
            )[0]

    if diagnosis_id:
        try:
            from server.app.ai_provider import get_ai_settings
            from .diagnosis_agent import select_scope_with_diagnosis_agent

            settings = get_ai_settings()
            if settings.nlp_enabled and settings.api_key:
                selected = select_scope_with_diagnosis_agent(
                    diagnosis_id=diagnosis_id,
                    query=query,
                    candidates=candidates,
                    settings=settings,
                )
                if selected is not None:
                    return selected
        except Exception:
            logger.exception(
                "AI scope selection failed; using deterministic safe fallback",
                extra={"diagnosis_id": diagnosis_id},
            )

    ranked = sorted(
        (
            (
                _auto_scope_score(query, item),
                _auto_scope_capability_score(query, item),
                item,
            )
            for item in candidates
        ),
        key=lambda row: (-row[0], -row[1], str(row[2].get("binding_id") or "")),
    )
    # Autonomous mode must make progress without asking the operator to
    # transcribe Agent/PID details. Every row is already an opaque, fresh,
    # server-attested binding; this tie-break only chooses among existing
    # authority and cannot widen it.
    return ranked[0][2]


def _default_auto_scope_range(query: str, *, timestamp: datetime) -> DiagnosticTimeRange:
    minutes = 5
    minute_match = re.search(
        r"(?:最近|过去|last|past)\s*(\d{1,3})\s*(?:分钟|分|min(?:ute)?s?)",
        query,
        re.IGNORECASE,
    )
    hour_match = re.search(
        r"(?:最近|过去|last|past)\s*(\d{1,2})\s*(?:小时|h(?:ou)?rs?)",
        query,
        re.IGNORECASE,
    )
    if minute_match:
        minutes = max(1, min(180, int(minute_match.group(1))))
    elif hour_match:
        minutes = max(1, min(180, int(hour_match.group(1)) * 60))
    return DiagnosticTimeRange(
        # Auto-scoped diagnoses use live collectors. Reserve a bounded forward
        # observation window so evidence gathered after the user presses Send
        # is inside scope. Explicit user-supplied historical ranges are never
        # rewritten by this path.
        start=timestamp,
        end=timestamp + timedelta(minutes=minutes),
        timezone="Asia/Shanghai",
    )


def _auto_scope_environment(candidate: dict) -> str:
    value = str(candidate.get("environment") or candidate.get("instance") or "").strip()
    return value[:64] if value else "discovered"


def _invalidate_discovery(discovery, *, timestamp: datetime) -> None:
    discovery.status = "INVALIDATED"
    discovery.invalidated_at = timestamp


class _DiscoveryInvalidationError(ValueError):
    """Selection failure whose discovery must remain durably invalidated."""

    def __init__(self, message: str, *, discovery_id: str) -> None:
        super().__init__(message)
        self.discovery_id = discovery_id


def _raise_discovery_invalidation(discovery, message: str) -> NoReturn:
    raise _DiscoveryInvalidationError(message, discovery_id=discovery.id)


def _persist_discovery_invalidation(
    discovery_id: str,
    diagnosis_id: str,
    *,
    timestamp: datetime,
) -> None:
    """Persist fail-closed invalidation after the selecting transaction rolls back."""

    session = new_session()
    try:
        discovery = (
            session.query(DropInsightTargetDiscoveryModel)
            .filter(
                DropInsightTargetDiscoveryModel.id == discovery_id,
                DropInsightTargetDiscoveryModel.diagnosis_id == diagnosis_id,
            )
            .with_for_update()
            .first()
        )
        if discovery is None:
            return
        _invalidate_discovery(discovery, timestamp=timestamp)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _invalidate_diagnosis_discoveries(
    session,
    diagnosis_id: str,
    *,
    timestamp: datetime,
) -> None:
    discoveries = (
        session.query(DropInsightTargetDiscoveryModel)
        .filter(
            DropInsightTargetDiscoveryModel.diagnosis_id == diagnosis_id,
            DropInsightTargetDiscoveryModel.status.in_(["READY", "AMBIGUOUS"]),
        )
        .with_for_update()
        .all()
    )
    for discovery in discoveries:
        _invalidate_discovery(discovery, timestamp=timestamp)


def _selected_discovery_binding(
    session,
    diagnosis,
    *,
    discovery_id: str,
    binding_id: str,
    timestamp: datetime,
) -> ProcessIdentityBinding:
    discovery = (
        session.query(DropInsightTargetDiscoveryModel)
        .filter(DropInsightTargetDiscoveryModel.id == discovery_id)
        .with_for_update()
        .first()
    )
    if discovery is None:
        raise ValueError("target discovery not found")
    if discovery.diagnosis_id != diagnosis.id:
        raise ValueError("target discovery does not belong to diagnosis")
    if discovery.diagnosis_version != diagnosis.version:
        _raise_discovery_invalidation(
            discovery,
            "target discovery belongs to a stale diagnosis version",
        )
    if discovery.status not in {"READY", "AMBIGUOUS"}:
        raise ValueError(f"target discovery is not selectable: {discovery.status}")
    if discovery.invalidated_at is not None:
        raise ValueError("target discovery is invalidated")
    if _as_utc(discovery.expires_at) <= timestamp:
        _raise_discovery_invalidation(discovery, "target discovery has expired")
    member = (
        session.query(DropInsightTargetBindingModel)
        .filter(
            DropInsightTargetBindingModel.id == binding_id,
            DropInsightTargetBindingModel.discovery_id == discovery.id,
        )
        .with_for_update()
        .first()
    )
    if member is None:
        raise ValueError("binding is not a member of target discovery")
    try:
        binding = ProcessIdentityBinding.from_mapping(member.process_binding_json)
    except (KeyError, TypeError, ValueError) as exc:
        raise _DiscoveryInvalidationError(
            "persisted target binding is invalid",
            discovery_id=discovery.id,
        ) from exc
    if (
        member.agent_id != binding.agent_id
        or member.pid != binding.pid
        or member.process_snapshot_id != binding.process_snapshot_id
    ):
        _raise_discovery_invalidation(
            discovery,
            "persisted target binding metadata disagrees with authority",
        )
    if not SqlRepository()._validate_process_binding_in_session(
        session,
        binding,
        agent_id=binding.agent_id,
        target_pid=binding.pid,
        now=timestamp,
    ):
        _raise_discovery_invalidation(
            discovery,
            "target discovery authority is absent, stale, or mismatched",
        )
    return binding


def _discovery_authority_is_current(session, discovery, *, timestamp: datetime) -> bool:
    if discovery.status not in {"READY", "AMBIGUOUS"}:
        return False
    if discovery.invalidated_at is not None or _as_utc(discovery.expires_at) <= timestamp:
        return False
    bindings = (
        session.query(DropInsightTargetBindingModel)
        .filter(DropInsightTargetBindingModel.discovery_id == discovery.id)
        .all()
    )
    if not bindings:
        return False
    repository = SqlRepository()
    return all(
        repository._validate_process_binding_in_session(
            session,
            item.process_binding_json,
            agent_id=item.agent_id,
            target_pid=item.pid,
            now=timestamp,
        )
        for item in bindings
    )


def _render_target_discovery(session, discovery_id: str) -> dict:
    discovery = session.get(DropInsightTargetDiscoveryModel, discovery_id)
    if discovery is None:
        raise ValueError("target discovery not found")
    timestamp = now_utc()
    if discovery.status in {"READY", "AMBIGUOUS"} and not _discovery_authority_is_current(
        session, discovery, timestamp=timestamp
    ):
        _invalidate_discovery(discovery, timestamp=timestamp)
        session.commit()
    candidates = (
        session.query(DropInsightTargetBindingModel)
        .filter(DropInsightTargetBindingModel.discovery_id == discovery.id)
        .order_by(DropInsightTargetBindingModel.id.asc())
        .all()
        if discovery.status in {"READY", "AMBIGUOUS"}
        else []
    )
    return {
        "diagnosis_id": discovery.diagnosis_id,
        "diagnosis_version": discovery.diagnosis_version,
        "discovery_id": discovery.id,
        "status": discovery.status,
        "created_at": discovery.created_at,
        "expires_at": discovery.expires_at,
        "filters": {
            "service": discovery.service_filter,
            "environment": discovery.environment_filter,
        },
        "candidates": [
            {"binding_id": item.id, **(item.display_json or {})}
            for item in candidates
        ],
    }


def discover_target_candidates(
    diagnosis_id: str,
    *,
    service: str | None = None,
    environment: str | None = None,
    agent_id: str | None = None,
) -> dict | None:
    """Persist one opaque, diagnosis-scoped view of latest Agent snapshots."""

    service = (service or "").strip() or None
    environment = (environment or "").strip() or None
    if service and len(service) > 128:
        raise ValueError("service filter is too long")
    if environment and len(environment) > 64:
        raise ValueError("environment filter is too long")

    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if diagnosis is None or diagnosis.deleted_at is not None:
            return None
        target = diagnosis.target_json or {}
        service = service or target.get("service")
        environment = environment or target.get("environment")
        timestamp = now_utc()
        discovery = DropInsightTargetDiscoveryModel(
            id=f"discovery_{uuid4().hex}",
            diagnosis_id=diagnosis.id,
            diagnosis_version=diagnosis.version,
            status="UNAVAILABLE",
            service_filter=service,
            environment_filter=environment,
            snapshot_state_json=[],
            created_at=timestamp,
            expires_at=timestamp + _TARGET_DISCOVERY_TTL,
        )
        session.add(discovery)
        # Bindings reference this row without an ORM relationship, so make
        # the parent INSERT order explicit for PostgreSQL.
        session.flush()

        heartbeat_cutoff = timestamp - _AGENT_HEARTBEAT_MAX_AGE
        agents = (
            session.query(AgentModel)
            .filter(
                AgentModel.status == "ONLINE",
                AgentModel.last_heartbeat_at >= heartbeat_cutoff,
            )
            .order_by(AgentModel.id.asc())
            .all()
        )
        snapshot_states: list[dict] = []
        matched: list[tuple[ProcessCandidateSnapshotModel, ProcessCandidateModel]] = []
        fail_statuses: list[str] = []
        authoritative_seen = False
        for agent in agents:
            if agent_id is not None and agent.id != agent_id:
                continue
            snapshot = (
                session.query(ProcessCandidateSnapshotModel)
                .filter(ProcessCandidateSnapshotModel.agent_id == agent.id)
                .order_by(
                    ProcessCandidateSnapshotModel.received_at.desc(),
                    ProcessCandidateSnapshotModel.id.desc(),
                )
                .first()
            )
            if snapshot is None:
                snapshot_states.append({
                    "agent_id": agent.id,
                    "snapshot_id": None,
                    "state": "absent",
                    "authoritative": False,
                    "received_at": None,
                    "generation": None,
                })
                fail_statuses.append("UNAVAILABLE")
                continue
            fresh = timedelta(0) <= timestamp - _as_utc(snapshot.received_at) <= PROCESS_SNAPSHOT_MAX_AGE
            snapshot_states.append({
                "agent_id": agent.id,
                "snapshot_id": snapshot.id,
                "state": snapshot.state,
                "authoritative": bool(snapshot.authoritative),
                "fresh": fresh,
                "received_at": _as_utc(snapshot.received_at).isoformat(),
                "generation": snapshot.generation,
                "boot_id": snapshot.boot_id,
            })
            if snapshot.state == "truncated":
                fail_statuses.append("TRUNCATED")
                continue
            if not fresh:
                fail_statuses.append("STALE")
                continue
            if not snapshot.authoritative:
                fail_statuses.append("UNAVAILABLE")
                continue
            authoritative_seen = True
            rows = (
                session.query(ProcessCandidateModel)
                .filter(ProcessCandidateModel.snapshot_id == snapshot.id)
                .order_by(ProcessCandidateModel.id.asc())
                .all()
            )
            matched.extend(
                (snapshot, candidate)
                for candidate in rows
                if _candidate_matches_filter(candidate, service, environment)
            )

        discovery.snapshot_state_json = snapshot_states
        if not agents:
            discovery.status = "UNAVAILABLE"
        # A missing/old snapshot on one host must not hide securely attested
        # candidates from another host. Each returned binding is still tied to
        # one fresh authoritative snapshot; snapshot_state_json preserves the
        # incomplete-host warning for audit and UI disclosure.
        elif len(matched) == 1:
            discovery.status = "READY"
        elif len(matched) > 1:
            discovery.status = "AMBIGUOUS"
        elif "TRUNCATED" in fail_statuses:
            discovery.status = "TRUNCATED"
        elif "STALE" in fail_statuses:
            discovery.status = "STALE"
        elif fail_statuses:
            discovery.status = "UNAVAILABLE"
        elif authoritative_seen:
            discovery.status = "EMPTY"
        else:
            discovery.status = "UNAVAILABLE"

        if discovery.status in {"READY", "AMBIGUOUS"}:
            for snapshot, candidate in matched:
                binding = ProcessIdentityBinding(
                    agent_id=candidate.agent_id,
                    pid=candidate.pid,
                    boot_id=snapshot.boot_id,
                    process_start_ticks=candidate.process_start_ticks,
                    pid_namespace_inode=candidate.pid_namespace_inode,
                    namespace_pid=candidate.namespace_pid,
                    executable_identity=candidate.executable_identity,
                    process_snapshot_id=snapshot.id,
                    snapshot_generation=snapshot.generation,
                    snapshot_received_at=_as_utc(snapshot.received_at),
                )
                session.add(DropInsightTargetBindingModel(
                    id=f"binding_{uuid4().hex}",
                    discovery_id=discovery.id,
                    agent_id=candidate.agent_id,
                    process_snapshot_id=snapshot.id,
                    pid=candidate.pid,
                    process_binding_json=binding.to_dict(),
                    display_json={
                        "service": candidate.service_hint or None,
                        "instance": candidate.instance_hint or None,
                        "environment": candidate.instance_hint or None,
                        "process": candidate.comm or None,
                        "namespace_pid": candidate.namespace_pid,
                        "collector_capabilities": candidate.collector_capabilities or [],
                        "eligible": True,
                        "ineligible_reason": None,
                    },
                ))
        session.commit()
        return _render_target_discovery(session, discovery.id)
    finally:
        session.close()


def _auto_resolve_diagnosis_scope(diagnosis_id: str, query: str) -> None:
    """Bind one fresh, unambiguous real process target without demo presets."""

    # A heartbeat may replace the latest snapshot between discovery and
    # selection. Retry only that narrow race; every attempt still validates a
    # fresh immutable process identity before it can update the diagnosis.
    for attempt in range(3):
        discovery = discover_target_candidates(
            diagnosis_id,
            service=_auto_scope_service_filter(query),
        )
        if not discovery or discovery.get("status") not in {"READY", "AMBIGUOUS"}:
            return
        selected = _select_auto_scope_candidate(
            query,
            discovery,
            diagnosis_id=diagnosis_id,
        )
        if selected is None:
            return
        timestamp = now_utc()
        session = new_session()
        try:
            diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
            requested_range = dict((diagnosis.requested_time_range_json or {})) if diagnosis else {}
            existing_target = dict((diagnosis.target_json or {})) if diagnosis else {}
        finally:
            session.close()
        service = str(
            existing_target.get("service")
            or selected.get("service")
            or selected.get("process")
            or "discovered-service"
        ).strip()[:128]
        payload = ClarifyDiagnosisRequest(
            expected_version=discovery.get("diagnosis_version"),
            target=ClarificationTarget(
                service=service,
                environment=(
                    existing_target.get("environment") or _auto_scope_environment(selected)
                ),
                discovery_id=discovery["discovery_id"],
                binding_id=selected["binding_id"],
            ),
            time_range=(
                None
                if requested_range
                else _default_auto_scope_range(query, timestamp=timestamp)
            ),
        )
        try:
            clarify_diagnosis(diagnosis_id, payload, actor="AI_SCOPE_RESOLVER")
            return
        except _DiscoveryInvalidationError:
            if attempt == 2:
                raise


def resolve_diagnosis_scope_autonomously(diagnosis_id: str) -> bool:
    """Retry safe scope discovery for an autonomous session.

    This makes scope acquisition part of the server runtime instead of a
    browser-owned form. Retries are rate-limited by the latest persisted
    discovery so an unavailable Agent cannot create a hot loop.
    """

    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if (
            diagnosis is None
            or diagnosis.deleted_at is not None
            or diagnosis.mode != "AUTONOMOUS"
            or diagnosis.status != "NEEDS_CLARIFICATION"
        ):
            return False
        created_event = (
            session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.event_type == "diagnosis.created",
            )
            .order_by(DropInsightEventModel.sequence.asc())
            .first()
        )
        # Persist the caller's choice across the HTTP request and background
        # worker. Older events lack this field and retain their old behavior.
        if created_event is not None and (created_event.payload_json or {}).get("auto_scope") is False:
            return False
        retry_seconds = max(
            5,
            int(os.getenv("MINI_DROP_AUTO_SCOPE_RETRY_SEC", "15")),
        )
        latest = (
            session.query(DropInsightTargetDiscoveryModel)
            .filter(DropInsightTargetDiscoveryModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightTargetDiscoveryModel.created_at.desc())
            .first()
        )
        if latest is not None and (
            now_utc() - _as_utc(latest.created_at)
        ) < timedelta(seconds=retry_seconds):
            return False
        query = diagnosis.query
    finally:
        session.close()

    _auto_resolve_diagnosis_scope(diagnosis_id, query)
    verification = new_session()
    try:
        diagnosis = verification.get(DropInsightSessionModel, diagnosis_id)
        return bool(diagnosis and diagnosis.status != "NEEDS_CLARIFICATION")
    finally:
        verification.close()


def create_diagnosis(
    payload: CreateDiagnosisRequestV2,
    *,
    created_by: str = "system:internal",
    business_observation: dict | None = None,
) -> DropInsightSessionModel:
    target_json = payload.target.model_dump(mode="json")
    time_range_json = payload.time_range.model_dump(mode="json") if payload.time_range else {}
    questions = _scope_questions(target_json, time_range_json)

    status = "NEEDS_CLARIFICATION" if questions else "UNDERSTANDING"
    timestamp = now_utc()
    diagnosis_id = f"insight_{uuid4().hex}"
    model = DropInsightSessionModel(
        id=diagnosis_id,
        query=payload.query,
        target_json=target_json,
        time_range_json=time_range_json,
        requested_time_range_json=time_range_json,
        effective_time_range_json={},
        mode=payload.mode,
        skill_policy=payload.skill_policy,
        created_by=created_by.strip() or "system:internal",
        budget_json=payload.budget.model_dump(mode="json"),
        status=status,
        version=1,
        clarification_questions_json=questions,
        created_at=timestamp,
        updated_at=timestamp,
    )
    event = DropInsightEventModel(
        id=f"event_{uuid4().hex}",
        diagnosis_id=diagnosis_id,
        sequence=1,
        event_type="diagnosis.created",
        actor="USER",
        payload_json={
            "status": status,
            "skill_policy": payload.skill_policy,
            "created_by": created_by.strip() or "system:internal",
            "auto_scope": payload.auto_scope,
            **({"business_observation": business_observation} if business_observation else {}),
        },
        occurred_at=timestamp,
    )
    session = new_session()
    try:
        session.add(model)
        session.flush()
        session.add(event)
        _enqueue_diagnosis_event(session, event)
        session.commit()
        session.refresh(model)
    finally:
        session.close()

    if payload.auto_scope and questions:
        try:
            _auto_resolve_diagnosis_scope(diagnosis_id, payload.query)
        except Exception:
            logger.exception("automatic diagnosis scope resolution failed", extra={"diagnosis_id": diagnosis_id})

    refreshed_session = new_session()
    try:
        return refreshed_session.get(DropInsightSessionModel, diagnosis_id)
    finally:
        refreshed_session.close()


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _time_range_boundary_utc(value, timezone_name: str) -> datetime:
    """Parse one API boundary using the declared zone for offset-less values."""

    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("diagnosis time range boundary must be a datetime")
    if value.tzinfo is None:
        try:
            value = value.replace(tzinfo=ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown diagnosis timezone: {timezone_name}") from exc
    return value.astimezone(timezone.utc)


def _diagnostic_time_ranges_equivalent(submitted: dict, established: dict) -> bool:
    """Compare an immutable range by meaning instead of JSON formatting.

    Browsers commonly round ``datetime-local`` controls to a minute while the
    server persists seconds and microseconds.  A repeated confirmation of the
    same displayed minute is idempotent; moving either boundary to a different
    minute remains a real (and rejected) range change.
    """

    try:
        submitted_model = DiagnosticTimeRange.model_validate(submitted)
        established_model = DiagnosticTimeRange.model_validate(established)
        submitted_zone = submitted_model.timezone
        established_zone = established_model.timezone

        def same_boundary(submitted_value: datetime, established_value: datetime) -> bool:
            left = _time_range_boundary_utc(submitted_value, submitted_zone)
            right = _time_range_boundary_utc(established_value, established_zone)
            if left == right:
                return True
            # HTML minute controls erase seconds.  Only the submitted side is
            # allowed to request this tolerance, so arbitrary second-precision
            # edits are never silently accepted.
            if submitted_value.second == 0 and submitted_value.microsecond == 0:
                return left.replace(second=0, microsecond=0) == right.replace(
                    second=0, microsecond=0
                )
            return False

        return same_boundary(
            submitted_model.start, established_model.start
        ) and same_boundary(submitted_model.end, established_model.end)
    except (TypeError, ValueError):
        return False


def open_effective_time_range(
    diagnosis_id: str,
    *,
    opened_at: datetime | None = None,
) -> dict | None:
    session = new_session()
    try:
        diagnosis = _lock_diagnosis(session, diagnosis_id)
        if diagnosis is None:
            return None
        if diagnosis.mode != "REPRODUCTION":
            raise ValueError(
                "effective live time range is only available for controlled reproduction"
            )
        timestamp = opened_at or now_utc()
        opened_at_json = _utc_iso(timestamp)
        existing = diagnosis.effective_time_range_json or {}
        if existing:
            if (
                existing.get("state") == "OPEN"
                and existing.get("opened_at") == opened_at_json
            ):
                return diagnosis.to_dict()
            raise ValueError("effective live time range has already been opened")
        requested = (
            diagnosis.requested_time_range_json
            or diagnosis.time_range_json
            or {}
        )
        diagnosis.effective_time_range_json = {
            "state": "OPEN",
            "source": "controlled_live_collection",
            "opened_at": opened_at_json,
            "timezone": requested.get("timezone", "Asia/Shanghai"),
        }
        _append_event(
            session,
            diagnosis_id,
            "diagnosis.effective_time_range_opened",
            "SYSTEM",
            {"effective_time_range": diagnosis.effective_time_range_json},
            timestamp,
        )
        _cas_session_update(
            session,
            diagnosis,
            status=diagnosis.status,
            timestamp=timestamp,
        )
        session.commit()
        session.refresh(diagnosis)
        return diagnosis.to_dict()
    finally:
        session.close()


def finalize_effective_time_range(
    diagnosis_id: str,
    *,
    observed_start: datetime,
    observed_end: datetime,
) -> dict | None:
    session = new_session()
    try:
        diagnosis = _lock_diagnosis(session, diagnosis_id)
        if diagnosis is None:
            return None
        if diagnosis.mode != "REPRODUCTION":
            raise ValueError(
                "effective live time range is only available for controlled reproduction"
            )
        existing = diagnosis.effective_time_range_json or {}
        start_json = _utc_iso(observed_start)
        end_json = _utc_iso(observed_end)
        finalized = {
            "state": "FINALIZED",
            "source": "controlled_live_collection",
            "opened_at": existing.get("opened_at"),
            "start": start_json,
            "end": end_json,
            "timezone": existing.get("timezone", "Asia/Shanghai"),
        }
        if existing.get("state") == "FINALIZED":
            if existing == finalized:
                return diagnosis.to_dict()
            raise ValueError("effective live time range is already finalized")
        if (
            existing.get("state") != "OPEN"
            or existing.get("source") != "controlled_live_collection"
            or not existing.get("opened_at")
        ):
            raise ValueError("effective live time range must be opened before finalization")
        opened_at = _parse_datetime(existing["opened_at"])
        start = _parse_datetime(start_json)
        end = _parse_datetime(end_json)
        if opened_at is None or start is None or end is None:
            raise ValueError("effective live time range contains invalid timestamps")
        if start < opened_at:
            raise ValueError("observed live range cannot start before it was opened")
        if end <= start:
            raise ValueError("effective live time range end must be later than start")
        timestamp = now_utc()
        diagnosis.effective_time_range_json = finalized
        _append_event(
            session,
            diagnosis_id,
            "diagnosis.effective_time_range_finalized",
            "SYSTEM",
            {"effective_time_range": finalized},
            timestamp,
        )
        _cas_session_update(
            session,
            diagnosis,
            status=diagnosis.status,
            timestamp=timestamp,
        )
        session.commit()
        session.refresh(diagnosis)
        return diagnosis.to_dict()
    finally:
        session.close()


def get_diagnosis(diagnosis_id: str) -> DropInsightSessionModel | None:
    session = new_session()
    try:
        return session.get(DropInsightSessionModel, diagnosis_id)
    finally:
        session.close()


def list_diagnoses() -> list[DropInsightSessionModel]:
    session = new_session()
    try:
        return (
            session.query(DropInsightSessionModel)
            .filter(DropInsightSessionModel.deleted_at.is_(None))
            .order_by(DropInsightSessionModel.created_at.desc())
            .all()
        )
    finally:
        session.close()


_ACTIVE_AUTONOMOUS_DIAGNOSIS_STATUSES = {
    "NEEDS_CLARIFICATION",
    "UNDERSTANDING",
    "PLANNING",
    "HYPOTHESIZING",
    "COLLECTING_EVIDENCE",
}


def expire_stale_autonomous_diagnoses(
    *,
    timestamp: datetime | None = None,
) -> list[str]:
    """Cancel autonomous sessions that exceeded their wall-clock budget.

    Task-duration accounting alone cannot bound time spent waiting in the
    Agent queue. Without a session deadline, a Worker restart could revive
    days-old diagnoses and let them compete with a fresh incident after the
    original observation window had already closed.
    """

    checked_at = timestamp or now_utc()
    lookup = new_session()
    try:
        diagnosis_ids = [
            row[0]
            for row in (
                lookup.query(DropInsightSessionModel.id)
                .filter(
                    DropInsightSessionModel.mode == "AUTONOMOUS",
                    DropInsightSessionModel.deleted_at.is_(None),
                    DropInsightSessionModel.status.in_(
                        _ACTIVE_AUTONOMOUS_DIAGNOSIS_STATUSES
                    ),
                )
                .all()
            )
        ]
    finally:
        lookup.close()

    expired: list[str] = []
    repository = SqlRepository()
    for diagnosis_id in diagnosis_ids:
        session = new_session()
        try:
            diagnosis = _lock_diagnosis(session, diagnosis_id)
            if (
                diagnosis is None
                or diagnosis.mode != "AUTONOMOUS"
                or diagnosis.status not in _ACTIVE_AUTONOMOUS_DIAGNOSIS_STATUSES
            ):
                continue
            budget = dict(diagnosis.budget_json or {})
            max_duration_seconds = max(
                10,
                min(1800, int(budget.get("max_duration_seconds") or 300)),
            )
            age_seconds = (
                _as_utc(checked_at) - _as_utc(diagnosis.created_at)
            ).total_seconds()
            if age_seconds <= max_duration_seconds:
                continue

            reason = "诊断会话超过端到端时长预算，停止陈旧采集与后续规划"
            tool_calls = (
                session.query(DropInsightToolCallModel)
                .filter(DropInsightToolCallModel.diagnosis_id == diagnosis_id)
                .all()
            )
            for tool_call in tool_calls:
                task = (
                    session.get(TaskModel, tool_call.task_id)
                    if tool_call.task_id
                    else None
                )
                if task is not None and task.status in {
                    TaskStatus.PENDING.value,
                    TaskStatus.RUNNING.value,
                    TaskStatus.UPLOADING.value,
                    TaskStatus.ANALYZING.value,
                }:
                    repository._transition_task_in_session(
                        session,
                        task.id,
                        TaskStatus.CANCELLED,
                        reason,
                        Actor.SCHEDULE,
                        {
                            "error_code": "DIAGNOSIS_DEADLINE_EXCEEDED",
                            "diagnosis_id": diagnosis_id,
                        },
                    )
                if tool_call.status in {
                    "PROPOSED",
                    "PENDING_APPROVAL",
                    "APPROVED",
                    "TASK_CREATED",
                    "RUNNING",
                }:
                    tool_call.status = "CANCELLED"
                    tool_call.result_json = {
                        **dict(tool_call.result_json or {}),
                        "error": "diagnosis_deadline_exceeded",
                        "reason": reason,
                    }
                    tool_call.executed_at = tool_call.executed_at or checked_at
                    tool_call.terminal_processing_status = "REPORT_EFFECTS_DONE"
                    tool_call.terminal_processed_at = checked_at
                    _release_budget_reservation(
                        tool_call,
                        timestamp=checked_at,
                        reason="diagnosis_deadline_exceeded",
                    )

            _cas_session_update(
                session,
                diagnosis,
                status="CANCELLED",
                timestamp=checked_at,
            )
            _append_event(
                session,
                diagnosis_id,
                "diagnosis.expired",
                "SYSTEM",
                {
                    "status": "CANCELLED",
                    "reason": "wall_clock_budget_exhausted",
                    "max_duration_seconds": max_duration_seconds,
                    "age_seconds": int(age_seconds),
                },
                checked_at,
                effect_key=f"diagnosis:{diagnosis_id}:wall-clock-expired",
            )
            session.commit()
            expired.append(diagnosis_id)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    return expired


def delete_diagnosis(
    diagnosis_id: str,
    *,
    deleted_by: str = "web",
    reason: str = "用户在 AI 诊断会话历史中归档",
) -> DropInsightSessionModel | None:
    """软归档一个诊断会话：隐藏但保留证据与审计（同任务归档策略）。

    已删除的会话返回 None；重复删除幂等（按 ID 置删除时间戳即可）。
    """
    timestamp = now_utc()
    session = new_session()
    try:
        model = session.get(DropInsightSessionModel, diagnosis_id)
        if model is None:
            return None
        model.deleted_at = timestamp
        model.deleted_by = deleted_by
        model.delete_reason = reason
        model.updated_at = timestamp
        session.commit()
        session.refresh(model)
        return model
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def list_events(diagnosis_id: str) -> list[DropInsightEventModel]:
    session = new_session()
    try:
        return (
            session.query(DropInsightEventModel)
            .filter(DropInsightEventModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightEventModel.sequence.asc())
            .all()
        )
    finally:
        session.close()


def list_knowledge_retrievals(diagnosis_id: str) -> list[dict]:
    """Project only this diagnosis's persisted knowledge retrieval traces."""

    session = new_session()
    try:
        events = (
            session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.event_type == "planner.knowledge_retrieved",
            )
            .order_by(DropInsightEventModel.sequence.asc())
            .all()
        )
        result: list[dict] = []
        for event in events:
            payload = dict(event.payload_json or {})
            trace = dict(payload.get("retrieval_trace") or {})
            result.append(
                {
                    "event_id": event.id,
                    "diagnosis_id": event.diagnosis_id,
                    "sequence": event.sequence,
                    "phase": payload.get("phase"),
                    "round_index": payload.get("round_index"),
                    "retrieval_trace": trace,
                    "occurred_at": event.occurred_at,
                }
            )
        return result
    finally:
        session.close()


def _record_planner_knowledge_retrieval(
    diagnosis_id: str,
    *,
    query: str,
    category: str,
    phase: str,
    effect_key: str,
    user_correction: str = "",
    round_index: int | None = None,
    trace_override: dict | None = None,
) -> dict:
    """Retrieve first, then persist the non-Evidence planner input exactly once."""

    retrieval_query = "\n".join(
        value
        for value in (str(query or "").strip(), category, user_correction.strip())
        if value
    )
    trace = trace_override if trace_override is not None else build_retrieval_trace(retrieval_query)
    session = new_session()
    try:
        if session.get(DropInsightSessionModel, diagnosis_id) is None:
            return trace
        _append_event(
            session,
            diagnosis_id,
            "planner.knowledge_retrieved",
            "SYSTEM",
            {
                "phase": phase,
                "round_index": round_index,
                "retrieval_trace": trace,
                # Keep this explicit in the durable event so consumers cannot
                # silently relabel a knowledge match as incident Evidence.
                "knowledge_is_evidence": False,
            },
            now_utc(),
            effect_key=effect_key,
        )
        session.commit()
        return trace
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_hypothesis(
    diagnosis_id: str,
    payload: CreateHypothesisRequest,
    *,
    source: str = "USER",
    round_index: int = 1,
    parent_hypothesis_id: str | None = None,
    generation_reason: str = "",
    effect_key: str | None = None,
) -> DropInsightHypothesisModel | None:
    session = new_session()
    try:
        if effect_key:
            existing = (
                session.query(DropInsightHypothesisModel)
                .filter(
                    DropInsightHypothesisModel.diagnosis_id == diagnosis_id,
                    DropInsightHypothesisModel.effect_key == effect_key,
                )
                .first()
            )
            if existing is not None:
                return existing
        diagnosis = _lock_diagnosis(
            session, diagnosis_id, payload.expected_version
        )
        if diagnosis is None:
            return None
        if effect_key:
            existing = (
                session.query(DropInsightHypothesisModel)
                .filter(
                    DropInsightHypothesisModel.diagnosis_id == diagnosis_id,
                    DropInsightHypothesisModel.effect_key == effect_key,
                )
                .first()
            )
            if existing is not None:
                return existing
        timestamp = now_utc()
        model = DropInsightHypothesisModel(
            id=f"hyp_{uuid4().hex}",
            diagnosis_id=diagnosis_id,
            statement=payload.statement,
            expected_observations_json=payload.expected_observations,
            falsification_criteria_json=payload.falsification_criteria,
            status="OPEN",
            source=source,
            round_index=round_index,
            parent_hypothesis_id=parent_hypothesis_id,
            generation_reason=generation_reason,
            effect_key=effect_key,
            created_at=timestamp,
            updated_at=timestamp,
        )
        _append_event(
            session,
            diagnosis_id,
            "hypothesis.created",
            "SYSTEM",
            {
                "hypothesis_id": model.id,
                "source": source,
                "round_index": round_index,
                "parent_hypothesis_id": parent_hypothesis_id,
            },
            timestamp,
        )
        _cas_session_update(session, diagnosis, status="HYPOTHESIZING", timestamp=timestamp)
        session.add(model)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            if not effect_key:
                raise
            existing = (
                session.query(DropInsightHypothesisModel)
                .filter(
                    DropInsightHypothesisModel.diagnosis_id == diagnosis_id,
                    DropInsightHypothesisModel.effect_key == effect_key,
                )
                .first()
            )
            if existing is None:
                raise
            return existing
        session.refresh(model)
        return model
    finally:
        session.close()


def list_hypotheses(diagnosis_id: str) -> list[DropInsightHypothesisModel]:
    session = new_session()
    try:
        return (
            session.query(DropInsightHypothesisModel)
            .filter(DropInsightHypothesisModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightHypothesisModel.created_at.asc())
            .all()
        )
    finally:
        session.close()


def add_evidence(
    diagnosis_id: str,
    payload: AddEvidenceRequest,
) -> DropInsightEvidenceModel | None:
    session = new_session()
    try:
        diagnosis = _lock_diagnosis(
            session, diagnosis_id, payload.expected_version
        )
        if diagnosis is None:
            return None
        if payload.hypothesis_id:
            hypothesis = session.get(DropInsightHypothesisModel, payload.hypothesis_id)
            if hypothesis is None or hypothesis.diagnosis_id != diagnosis_id:
                raise ValueError("hypothesis does not belong to diagnosis")
        # This endpoint is intentionally restricted to untrusted contextual
        # material.  A caller cannot manufacture Task/Artifact provenance or
        # self-assert HIGH quality.  Traceable production evidence must use
        # import_task_evidence().
        timestamp = now_utc()
        target = diagnosis.target_json or {}
        requested_range = diagnosis.time_range_json or {}
        range_end = _parse_datetime(requested_range.get("end")) or timestamp
        range_start = (
            _parse_datetime(requested_range.get("start"))
            or range_end - timedelta(microseconds=1)
        )
        envelope = EvidenceEnvelope(
            evidence_id=payload.evidence_id,
            diagnosis_id=diagnosis_id,
            evidence_type=f"UNVERIFIED_EXTERNAL:{payload.evidence_type}",
            source={
                "tool_name": payload.source_label,
                "task_id": "",
                "task_attempt_id": "",
                "artifact_id": "",
                "artifact_sha256": "",
                "analysis_job_id": "",
                "analyzer_type": "",
                "analyzer_version": "unverified-external",
                "analyzer_output_schema_version": "",
                "observation_json_pointer": "/",
            },
            scope={
                "agent_id": target.get("agent_id") or "unverified",
                "service": target.get("service"),
                "host_id": target.get("host_id"),
                "instance_id": target.get("instance_id"),
                "container_id": target.get("container_id"),
                "pid": target.get("pid"),
            },
            time_range={
                "start": range_start,
                "end": range_end,
                "timezone": requested_range.get("timezone", "Asia/Shanghai"),
            },
            observation=payload.observation,
            quality={
                "level": "LOW",
                "sample_count": 0,
                "sample_count_known": False,
                "degraded": True,
                "target_match": False,
                "time_overlap": False,
                "schema_valid": False,
                "analyzer_validated": False,
                "minimum_samples": None,
            },
            limitations=[
                "UNVERIFIED_EXTERNAL：该信息未绑定平台 Task、TaskAttempt、Artifact 和 Analyzer Job",
                *payload.limitations,
            ],
        )
        classification = classify_evidence(envelope)
        model = DropInsightEvidenceModel(
            id=payload.evidence_id,
            diagnosis_id=diagnosis_id,
            hypothesis_id=payload.hypothesis_id,
            role="UNVERIFIED_EXTERNAL",
            envelope_json=envelope.model_dump(mode="json"),
            classification_json=classification,
            created_at=timestamp,
        )
        _append_event(
            session,
            diagnosis_id,
            "evidence.added",
            "ANALYZER",
            {
                "evidence_id": model.id,
                "role": model.role,
                "decision": classification["decision"],
                "trusted_provenance": False,
            },
            timestamp,
        )
        _cas_session_update(
            session, diagnosis, status="COLLECTING_EVIDENCE", timestamp=timestamp
        )
        session.add(model)
        session.commit()
        session.refresh(model)
        return model
    finally:
        session.close()


def list_evidence(diagnosis_id: str) -> list[DropInsightEvidenceModel]:
    session = new_session()
    try:
        return (
            session.query(DropInsightEvidenceModel)
            .filter(DropInsightEvidenceModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightEvidenceModel.created_at.asc())
            .all()
        )
    finally:
        session.close()


def _diagnosis_round_contract(
    session,
    diagnosis: DropInsightSessionModel,
    *,
    include_hypothesis_id: str | None = None,
    include_round_index: int | None = None,
) -> dict[str, object]:
    """Return the server-enforced minimum-round progress for real reports.

    Merely expanding several hypotheses does not satisfy the contract. A round
    counts only after a report has been persisted for a selected hypothesis,
    which means at least one real observation reached the Evidence Gate. LATS
    may backtrack to a sibling born in an older tree depth; its monotonic
    ``lats.node_selected.iteration`` remains the real execution round.
    ``include_hypothesis_id`` accounts for the report currently being built
    before it is flushed. ``include_round_index`` remains a legacy fallback for
    callers without a hypothesis identity.
    """

    budget = diagnosis.budget_json or {}
    maximum = max(1, int(budget.get("max_diagnosis_rounds", 6)))
    minimum = min(
        4,
        maximum,
        max(1, int(budget.get("min_diagnosis_rounds", 1))),
    )
    report_rows = (
        session.query(
            DropInsightHypothesisModel.id,
            DropInsightHypothesisModel.round_index,
        )
        .join(
            DropInsightReportModel,
            DropInsightReportModel.hypothesis_id
            == DropInsightHypothesisModel.id,
        )
        .filter(DropInsightReportModel.diagnosis_id == diagnosis.id)
        .distinct()
        .all()
    )
    birth_rounds = {
        str(hypothesis_id): max(1, int(round_index or 1))
        for hypothesis_id, round_index in report_rows
    }
    if include_hypothesis_id:
        birth_rounds.setdefault(
            include_hypothesis_id,
            max(1, int(include_round_index or 1)),
        )
    events = (
        session.query(DropInsightEventModel)
        .filter(
            DropInsightEventModel.diagnosis_id == diagnosis.id,
            DropInsightEventModel.event_type == "lats.node_selected",
        )
        .order_by(DropInsightEventModel.sequence.asc())
        .all()
    )
    report_hypothesis_ids = [str(row[0]) for row in report_rows]
    if include_hypothesis_id:
        report_hypothesis_ids.append(include_hypothesis_id)
    observed = report_execution_rounds(
        events,
        report_hypothesis_ids,
        birth_rounds,
    )
    if include_hypothesis_id is None and include_round_index is not None:
        observed.add(int(include_round_index))
    return {
        "minimum": minimum,
        "observed": len(observed),
        "round_indexes": sorted(observed),
        "satisfied": len(observed) >= minimum,
    }


def _report_round_contract_snapshot(diagnosis_id: str) -> dict[str, object]:
    """Read durable execution-round progress outside an existing transaction."""

    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if diagnosis is None:
            return {
                "minimum": 1,
                "observed": 0,
                "round_indexes": [],
                "satisfied": False,
            }
        return _diagnosis_round_contract(session, diagnosis)
    finally:
        session.close()


def generate_report(
    diagnosis_id: str,
    payload: GenerateReportRequest,
) -> DropInsightReportModel | None:
    session = new_session()
    try:
        diagnosis = _lock_diagnosis(session, diagnosis_id)
        if diagnosis is None:
            return None
        hypothesis = session.get(DropInsightHypothesisModel, payload.hypothesis_id)
        if hypothesis is None or hypothesis.diagnosis_id != diagnosis_id:
            raise ValueError("hypothesis does not belong to diagnosis")
        existing_report = (
            session.query(DropInsightReportModel)
            .filter(
                DropInsightReportModel.diagnosis_id == diagnosis_id,
                DropInsightReportModel.hypothesis_id == payload.hypothesis_id,
            )
            .first()
        )
        if existing_report is not None:
            report_id = existing_report.id
            session.expunge(existing_report)
            session.close()
            return _apply_report_effects(report_id)
        if (
            payload.expected_version is not None
            and diagnosis.version != payload.expected_version
        ):
            raise ValueError(
                "diagnosis version conflict: "
                f"expected={payload.expected_version}, "
                f"actual={diagnosis.version}"
            )

        rows = (
            session.query(DropInsightEvidenceModel)
            .filter(
                DropInsightEvidenceModel.diagnosis_id == diagnosis_id,
                DropInsightEvidenceModel.hypothesis_id == payload.hypothesis_id,
            )
            .all()
        )
        supporting = [
            EvidenceEnvelope.model_validate(row.envelope_json)
            for row in rows
            if row.role == "SUPPORT"
        ]
        counter = [
            EvidenceEnvelope.model_validate(row.envelope_json)
            for row in rows
            if row.role == "COUNTER"
        ]
        verification = verify_report_claims(
            [
                (row.role, EvidenceEnvelope.model_validate(row.envelope_json))
                for row in rows
            ],
            expected_observations=hypothesis.expected_observations_json or [],
            falsification_criteria=hypothesis.falsification_criteria_json or [],
        )
        coverage_ratio = verification["coverage_ratio"]
        confidence = calibrate_confidence(supporting, counter, coverage_ratio)
        support_refs = sorted({
            item["evidence_id"]
            for item in verification["claims"]
            if item["direction"] == "SUPPORT"
        })
        counter_refs = sorted({
            item["evidence_id"]
            for item in verification["claims"]
            if item["direction"] == "COUNTER"
        })
        limitations = sorted(
            {
                reason
                for item in supporting + counter
                for reason in classify_evidence(item)["reasons"]
            }
        )
        if not support_refs:
            limitations.append("没有通过证据门禁的支持证据，当前不能建立根因结论。")
        elif not verification["has_independent_counter_or_control"]:
            limitations.append(
                "缺少独立反证或对照证据；当前只是阶段性发现，尚不能确认因果关系或故障已修复。"
            )

        source_symbols = _extract_source_symbols(supporting + counter)
        verification["source_context"] = map_hot_functions(
            source_symbols,
            language_hint=_diagnosis_runtime_family(diagnosis),
        )

        conclusion = _derive_report_conclusion(
            hypothesis.statement,
            support_refs=support_refs,
            counter_refs=counter_refs,
            supporting=[
                item for item in supporting if item.evidence_id in support_refs
            ],
            verification_status=verification["status"],
        )
        assumptions = ["结论仅适用于当前诊断目标与时间窗口"]
        target_info = diagnosis.target_json or {}
        if target_info.get("trace_id"):
            assumptions.append(f"关联分布式链路 Trace ID: {target_info['trace_id']}")
            verification["trace_id"] = target_info["trace_id"]
            if target_info.get("span_id"):
                verification["span_id"] = target_info["span_id"]
        next_actions = _derive_next_actions(
            support_refs=support_refs,
            counter_refs=counter_refs,
        )
        if not _concrete_report_finding([item for item in supporting if item.evidence_id in support_refs]):
            next_actions = ["先定位具体瓶颈：在同一负载窗口采集目标进程资源变化与运行时调用栈，关联业务延迟；定位后再制定修复并复测。"]
        host_observations = [row.envelope_json.get("observation", {}).get("metadata", {}) for row in rows
            if row.envelope_json.get("observation", {}).get("metadata", {}).get("scope_semantics") == "HOST_BLOCK_DEVICE"]
        if host_observations:
            next_actions = ["将同窗口的目标进程读写计数、阻塞调用栈与主机 I/O 分布关联；队列积压还需比较生产速率、消费速率和队列长度。主机延迟不能直接归因为该进程。"]
            if not support_refs:
                conclusion = "尚未定位根因：本轮观察到宿主机块设备 I/O，但没有把这些请求归属到目标进程。缺少正常基线与业务关联，不能据此确认磁盘异常或队列积压原因。"
                limitations.append("主机 I/O 仅作背景观察，不是目标进程的支持证据或反证。故障修复状态需独立复测。")
        verification["remediation"] = generate_sre_remediation_advice(
            verification.get("claims", []),
            verification.get("status", ""),
            conclusion_text=conclusion,
        )
        round_contract = _diagnosis_round_contract(
            session,
            diagnosis,
            include_hypothesis_id=hypothesis.id,
            include_round_index=hypothesis.round_index,
        )

        timestamp = now_utc()
        report_identity = hashlib.sha256(
            f"{diagnosis_id}\0{payload.hypothesis_id}".encode("utf-8")
        ).hexdigest()[:32]
        report = DropInsightReportModel(
            id=f"report_{report_identity}",
            diagnosis_id=diagnosis_id,
            hypothesis_id=payload.hypothesis_id,
            conclusion=conclusion,
            confidence=round(confidence * CONFIDENCE_SCALE),
            evidence_refs_json=support_refs,
            counter_evidence_refs_json=counter_refs,
            assumptions_json=assumptions,
            limitations_json=limitations,
            next_actions_json=next_actions,
            claims_json=verification["claims"],
            verification_json=verification,
            effects_status="PENDING",
            effects_phase=None,
            effects_owner=None,
            effects_lease_expires_at=None,
            effects_fencing_token=0,
            effects_applied_at=None,
            created_at=timestamp,
        )
        if counter_refs and not support_refs:
            hypothesis.status = "COUNTER"
        elif confidence >= 0.6 and verification["status"] in {
            "VERIFIED", "PARTIAL_WITHOUT_COUNTER"
        }:
            hypothesis.status = "SUPPORTED"
        else:
            hypothesis.status = "INCONCLUSIVE"
        hypothesis.updated_at = timestamp
        # A report is a branch observation, not automatically the terminal
        # state of the whole Agent session.  Non-verified and partially
        # verified reports stay non-terminal while report effects synchronously
        # decide whether to widen/replan or truly stop the search.  This avoids
        # a visible INSUFFICIENT_EVIDENCE/COMPLETED flash that a browser poller
        # could mistake for the final result.
        next_status = (
            "COMPLETED"
            if (
                support_refs
                and confidence >= 0.6
                and verification["status"] == "VERIFIED"
                and round_contract["satisfied"]
            )
            else "COLLECTING_EVIDENCE"
        )
        _cas_session_update(session, diagnosis, status=next_status, timestamp=timestamp)
        _append_event(
            session,
            diagnosis_id,
            "report.generated",
            "SYSTEM",
            {
                "report_id": report.id,
                "confidence": confidence,
                "coverage_ratio": coverage_ratio,
                "evidence_refs": support_refs,
                "verification_status": verification["status"],
                "verified_claim_count": len(verification["claims"]),
                "generation_mode": "SERVER_EVIDENCE_RULES_V1",
                "minimum_diagnosis_rounds": round_contract["minimum"],
                "observed_diagnosis_rounds": round_contract["observed"],
                "minimum_rounds_satisfied": round_contract["satisfied"],
            },
            timestamp,
        )
        session.add(report)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing_report = (
                session.query(DropInsightReportModel)
                .filter(
                    DropInsightReportModel.diagnosis_id == diagnosis_id,
                    DropInsightReportModel.hypothesis_id == payload.hypothesis_id,
                )
                .first()
            )
            if existing_report is None:
                raise
            report_id = existing_report.id
        else:
            report_id = report.id
        session.close()
        return _apply_report_effects(report_id)
    finally:
        session.close()


def mark_evidence_collection_started(
    diagnosis_id: str,
    *,
    reason: str,
) -> DropInsightSessionModel:
    """Move a diagnosis into evidence collection through the normal CAS path.

    Importers that persist trusted evidence outside ``import-task`` use this
    small public boundary instead of mutating the session status directly.
    """
    session = new_session()
    try:
        diagnosis = _lock_diagnosis(session, diagnosis_id)
        if diagnosis is None:
            raise ValueError("diagnosis not found")
        timestamp = now_utc()
        _cas_session_update(
            session,
            diagnosis,
            status="COLLECTING_EVIDENCE",
            timestamp=timestamp,
        )
        _append_event(
            session,
            diagnosis_id,
            "evidence.collection_started",
            "SYSTEM",
            {"reason": reason},
            timestamp,
        )
        session.commit()
        session.refresh(diagnosis)
        return diagnosis
    finally:
        session.close()


def _claim_report_effects(
    report_id: str,
    owner: str,
) -> tuple[DropInsightReportModel | None, bool]:
    session = new_session()
    try:
        claimed_at = now_utc()
        updated = session.execute(
            update(DropInsightReportModel)
            .where(
                DropInsightReportModel.id == report_id,
                or_(
                    DropInsightReportModel.effects_status == "PENDING",
                    and_(
                        DropInsightReportModel.effects_status == "APPLYING",
                        or_(
                            DropInsightReportModel.effects_lease_expires_at.is_(None),
                            DropInsightReportModel.effects_lease_expires_at <= claimed_at,
                        ),
                    ),
                ),
            )
            .values(
                effects_status="APPLYING",
                effects_owner=owner,
                effects_lease_expires_at=claimed_at + _REPORT_EFFECT_LEASE,
                effects_fencing_token=(
                    DropInsightReportModel.effects_fencing_token + 1
                ),
            )
        ).rowcount
        session.commit()
        report = session.get(DropInsightReportModel, report_id)
        return report, bool(
            updated == 1
            and report is not None
            and report.effects_owner == owner
        )
    finally:
        session.close()


def _renew_report_effect_lease(
    report_id: str,
    owner: str,
    fencing_token: int,
) -> None:
    session = new_session()
    try:
        renewed_at = now_utc()
        updated = session.execute(
            update(DropInsightReportModel)
            .where(
                DropInsightReportModel.id == report_id,
                DropInsightReportModel.effects_status == "APPLYING",
                DropInsightReportModel.effects_owner == owner,
                DropInsightReportModel.effects_fencing_token == fencing_token,
                DropInsightReportModel.effects_lease_expires_at > renewed_at,
            )
            .values(
                effects_lease_expires_at=renewed_at + _REPORT_EFFECT_LEASE,
            )
        ).rowcount
        session.commit()
        if updated != 1:
            raise _StaleReportEffectAuthority(
                "stale Drop Insight report-effect authority"
            )
    finally:
        session.close()


def _start_report_effect_execution(
    report_id: str,
    owner: str,
    fencing_token: int,
) -> None:
    session = new_session()
    try:
        started_at = now_utc()
        updated = session.execute(
            update(DropInsightReportModel)
            .where(
                DropInsightReportModel.id == report_id,
                DropInsightReportModel.effects_status == "APPLYING",
                DropInsightReportModel.effects_owner == owner,
                DropInsightReportModel.effects_fencing_token == fencing_token,
                DropInsightReportModel.effects_lease_expires_at > started_at,
            )
            .values(
                effects_phase="EXECUTION_STARTED",
                effects_lease_expires_at=started_at + _REPORT_EFFECT_LEASE,
            )
        ).rowcount
        session.commit()
        if updated != 1:
            raise _StaleReportEffectAuthority(
                "stale Drop Insight report-effect authority"
            )
    finally:
        session.close()


def _complete_report_effects(
    report_id: str,
    owner: str,
    fencing_token: int,
) -> DropInsightReportModel:
    session = new_session()
    try:
        completed_at = now_utc()
        updated = session.execute(
            update(DropInsightReportModel)
            .where(
                DropInsightReportModel.id == report_id,
                DropInsightReportModel.effects_status == "APPLYING",
                DropInsightReportModel.effects_owner == owner,
                DropInsightReportModel.effects_fencing_token == fencing_token,
                DropInsightReportModel.effects_lease_expires_at > completed_at,
            )
            .values(
                effects_status="APPLIED",
                effects_phase="EFFECTS_COMPLETED",
                effects_owner=None,
                effects_lease_expires_at=None,
                effects_applied_at=func.coalesce(
                    DropInsightReportModel.effects_applied_at,
                    completed_at,
                ),
            )
        ).rowcount
        session.commit()
        if updated != 1:
            raise _StaleReportEffectAuthority(
                "stale Drop Insight report-effect authority"
            )
        report = session.get(DropInsightReportModel, report_id)
        if report is None:
            raise ValueError("Drop Insight report does not exist")
        return report
    finally:
        session.close()


def _release_report_effects(
    report_id: str,
    owner: str,
    fencing_token: int,
) -> None:
    session = new_session()
    try:
        session.execute(
            update(DropInsightReportModel)
            .where(
                DropInsightReportModel.id == report_id,
                DropInsightReportModel.effects_status == "APPLYING",
                DropInsightReportModel.effects_owner == owner,
                DropInsightReportModel.effects_fencing_token == fencing_token,
            )
            .values(
                effects_status="PENDING",
                effects_owner=None,
                effects_lease_expires_at=None,
            )
        )
        session.commit()
    finally:
        session.close()


def _apply_report_effects(
    report_id: str,
) -> DropInsightReportModel | None:
    owner = f"report-effects-{uuid4().hex}"
    report, claimed = _claim_report_effects(report_id, owner)
    if report is None or report.effects_status == "APPLIED":
        return report
    if not claimed:
        return report

    fencing_token = report.effects_fencing_token
    reconcile_only = report.effects_phase in {
        "EXECUTION_STARTED",
        "EFFECTS_COMPLETED",
    }
    try:
        if report.effects_phase != "EFFECTS_COMPLETED":
            _start_report_effect_execution(report_id, owner, fencing_token)
            token = _REPORT_EFFECT_RECONCILIATION.set(reconcile_only)
            try:
                diagnosis_id = report.diagnosis_id
                hypothesis_id = report.hypothesis_id
                verification_status = (report.verification_json or {}).get("status")
                has_support = bool(report.evidence_refs_json)
                has_counter = bool(report.counter_evidence_refs_json)
                round_session = new_session()
                try:
                    round_diagnosis = round_session.get(
                        DropInsightSessionModel,
                        diagnosis_id,
                    )
                    round_contract = (
                        _diagnosis_round_contract(
                            round_session,
                            round_diagnosis,
                        )
                        if round_diagnosis is not None
                        else {
                            "minimum": 1,
                            "observed": 0,
                            "satisfied": False,
                        }
                    )
                finally:
                    round_session.close()

                # LATS learns only from the immutable report produced by the
                # Evidence Gate.  The reflection is planning context, never a
                # substitute for incident Evidence.
                _record_lats_report_outcome(report_id)

                if has_counter and not has_support and hypothesis_id:
                    _replan_from_counter_evidence(
                        diagnosis_id,
                        hypothesis_id,
                        report_id,
                    )
                elif not has_support and hypothesis_id:
                    _replan_after_insufficient_evidence(
                        diagnosis_id,
                        hypothesis_id,
                        report_id,
                    )
                elif verification_status == "PARTIAL_WITHOUT_COUNTER" and hypothesis_id:
                    _replan_after_insufficient_evidence(
                        diagnosis_id,
                        hypothesis_id,
                        report_id,
                        continuation_reason=(
                            "已有支持证据，但缺少独立证据域的交叉验证"
                        ),
                    )
                elif (
                    verification_status == "VERIFIED"
                    and has_support
                    and report.confidence >= 600
                ):
                    if round_contract["satisfied"]:
                        _record_successful_route(diagnosis_id, report_id)
                    elif hypothesis_id:
                        _replan_after_insufficient_evidence(
                            diagnosis_id,
                            hypothesis_id,
                            report_id,
                            continuation_reason=(
                                "当前证据已达到支持门槛，但受控场景要求至少 "
                                f"{round_contract['minimum']} 轮真实诊断；当前仅完成 "
                                f"{round_contract['observed']} 轮，继续跨证据域交叉验证"
                            ),
                        )
                elif has_support and hypothesis_id:
                    _replan_after_insufficient_evidence(
                        diagnosis_id,
                        hypothesis_id,
                        report_id,
                        continuation_reason=(
                            "证据覆盖已通过结构校验，但综合置信度仍未达到结论门槛"
                        ),
                    )
            finally:
                _REPORT_EFFECT_RECONCILIATION.reset(token)
            _renew_report_effect_lease(report_id, owner, fencing_token)
        return _complete_report_effects(report_id, owner, fencing_token)
    except Exception:
        _release_report_effects(report_id, owner, fencing_token)
        raise


def list_reports(diagnosis_id: str) -> list[DropInsightReportModel]:
    session = new_session()
    try:
        return (
            session.query(DropInsightReportModel)
            .filter(DropInsightReportModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightReportModel.created_at.desc())
            .all()
        )
    finally:
        session.close()


def _extract_source_symbols(evidence_rows: list) -> list[str]:
    """Extract a bounded set of sampled function names from trusted evidence.

    This deliberately ignores free-form prose. Only symbol-shaped values under
    known profiling keys are considered, preventing a model or user message
    from turning arbitrary text into a source-tree search.
    """

    symbol_keys = {
        "function", "function_name", "symbol", "frame", "top_function",
        "hot_function", "hot_functions", "top_functions",
    }
    symbols: list[str] = []

    def walk(value, key: str = "", depth: int = 0) -> None:
        if depth > 7 or len(symbols) >= 20:
            return
        if isinstance(value, dict):
            for child_key, child in value.items():
                if str(child_key).lower() == "top_functions" and isinstance(child, list):
                    for row in child[:20]:
                        if isinstance(row, dict) and isinstance(row.get("name"), str):
                            candidate = row["name"].strip()
                            if 1 < len(candidate) <= 256 and candidate not in symbols:
                                symbols.append(candidate)
                walk(child, str(child_key).lower(), depth + 1)
        elif isinstance(value, list):
            for child in value[:100]:
                walk(child, key, depth + 1)
        elif key in symbol_keys and isinstance(value, str):
            candidate = value.strip()
            if 1 < len(candidate) <= 256 and candidate not in symbols:
                symbols.append(candidate)

    for row in evidence_rows:
        if hasattr(row, "envelope_json"):
            payload = row.envelope_json or {}
        elif hasattr(row, "model_dump"):
            payload = row.model_dump(mode="json")
        elif isinstance(row, dict):
            payload = row
        else:
            payload = {}
        walk(payload)
    return symbols


def list_feedback(diagnosis_id: str) -> list[DropInsightFeedbackModel]:
    session = new_session()
    try:
        return (
            session.query(DropInsightFeedbackModel)
            .filter(DropInsightFeedbackModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightFeedbackModel.created_at.desc())
            .all()
        )
    finally:
        session.close()


def _intervention_event_view(event, diagnosis=None) -> dict:
    payload = dict(event.payload_json or {})
    return {
        "intervention_id": payload.get("intervention_id"),
        "diagnosis_id": event.diagnosis_id,
        "sequence": event.sequence,
        "action": payload.get("action"),
        "message": payload.get("message"),
        "hypothesis_id": payload.get("hypothesis_id"),
        "revision_hypothesis_id": payload.get("revision_hypothesis_id"),
        "tool_call_id": payload.get("tool_call_id"),
        "round_index": payload.get("round_index"),
        "created_by": payload.get("created_by"),
        "created_at": event.occurred_at,
        "status": diagnosis.status if diagnosis is not None else None,
        "diagnosis_version": diagnosis.version if diagnosis is not None else None,
    }


def list_diagnosis_interventions(diagnosis_id: str) -> list[dict]:
    """Return durable user turns in conversational order."""

    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if diagnosis is None:
            return []
        rows = (
            session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.event_type == "diagnosis.intervention_submitted",
            )
            .order_by(DropInsightEventModel.sequence.asc())
            .all()
        )
        return [_intervention_event_view(row, diagnosis) for row in rows]
    finally:
        session.close()


def _intervention_effect_key(
    diagnosis_id: str,
    created_by: str,
    idempotency_key: str,
) -> str:
    digest = hashlib.sha256(
        f"{diagnosis_id}\0{created_by}\0{idempotency_key}".encode("utf-8")
    ).hexdigest()
    return f"intervention:{digest[:48]}"


def intervene_diagnosis(
    diagnosis_id: str,
    payload: InterveneDiagnosisRequest,
    *,
    created_by: str,
) -> dict | None:
    """Persist one user turn and continue the evidence loop safely.

    The message is context, not evidence.  It can deprioritize a branch and
    seed a new falsifiable hypothesis, but the selected probe still passes the
    regular binding, policy, capability and budget checks.
    """

    generated_id = f"intervention_{uuid4().hex}"
    effect_key = (
        _intervention_effect_key(
            diagnosis_id,
            created_by,
            payload.idempotency_key,
        )
        if payload.idempotency_key
        else f"intervention:{generated_id}"
    )
    event_id = None
    session = new_session()
    try:
        # Idempotency is checked before the optimistic version so a browser
        # retry with the original version returns the first user turn.
        existing = (
            session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.effect_key == effect_key,
            )
            .first()
        )
        if existing is not None:
            event_id = existing.id
        else:
            diagnosis = _lock_diagnosis(
                session,
                diagnosis_id,
                payload.expected_version,
            )
            if diagnosis is None:
                return None
            if diagnosis.deleted_at is not None:
                raise ValueError("diagnosis is archived")
            if diagnosis.status == "NEEDS_CLARIFICATION":
                raise ValueError(
                    "diagnosis scope must be resolved before user intervention"
                )
            timestamp = now_utc()
            _validated_target_binding(session, diagnosis, now=timestamp)
            parent = None
            if payload.hypothesis_id:
                parent = session.get(
                    DropInsightHypothesisModel,
                    payload.hypothesis_id,
                )
                if parent is None or parent.diagnosis_id != diagnosis_id:
                    raise ValueError("hypothesis does not belong to diagnosis")
            if parent is None:
                parent = (
                    session.query(DropInsightHypothesisModel)
                    .filter(DropInsightHypothesisModel.diagnosis_id == diagnosis_id)
                    .order_by(
                        DropInsightHypothesisModel.round_index.desc(),
                        DropInsightHypothesisModel.created_at.desc(),
                    )
                    .first()
                )
            current_round = (
                session.query(func.max(DropInsightHypothesisModel.round_index))
                .filter(DropInsightHypothesisModel.diagnosis_id == diagnosis_id)
                .scalar()
                or 0
            )
            round_index = int(current_round) + 1
            max_rounds = int(
                (diagnosis.budget_json or {}).get("max_diagnosis_rounds", 6)
            )
            if round_index > max_rounds:
                raise ValueError(
                    f"diagnosis round budget exhausted: {current_round}/{max_rounds}"
                )
            if parent is not None and payload.action in {
                "CHALLENGE_HYPOTHESIS",
                "CHANGE_DIRECTION",
            }:
                # Human direction changes are not scientific counter-evidence;
                # preserve that distinction in the explicit status.
                parent.status = "DEPRIORITIZED"
                parent.updated_at = timestamp
            intervention_id = generated_id
            # `_append_event` owns sequence allocation and outbox creation.
            _append_event(
                session,
                diagnosis_id,
                "diagnosis.intervention_submitted",
                "USER",
                {
                    "intervention_id": intervention_id,
                    "action": payload.action,
                    "message": payload.message.strip(),
                    "hypothesis_id": parent.id if parent is not None else None,
                    "revision_hypothesis_id": None,
                    "tool_call_id": None,
                    "round_index": round_index,
                    "created_by": created_by,
                    "status_before": diagnosis.status,
                },
                timestamp,
                effect_key=effect_key,
            )
            _cas_session_update(
                session,
                diagnosis,
                status="HYPOTHESIZING",
                timestamp=timestamp,
            )
            session.commit()
            stored_event = (
                session.query(DropInsightEventModel)
                .filter(
                    DropInsightEventModel.diagnosis_id == diagnosis_id,
                    DropInsightEventModel.effect_key == effect_key,
                )
                .one()
            )
            event_id = stored_event.id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    if event_id is None:
        return None
    return _apply_diagnosis_intervention(diagnosis_id, event_id)


def _apply_diagnosis_intervention(diagnosis_id: str, event_id: str) -> dict:
    """Idempotently project a persisted user turn into a new investigation round."""

    session = new_session()
    try:
        event = session.get(DropInsightEventModel, event_id)
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if (
            event is None
            or event.diagnosis_id != diagnosis_id
            or event.event_type != "diagnosis.intervention_submitted"
            or diagnosis is None
        ):
            raise ValueError("diagnosis intervention not found")
        event_payload = dict(event.payload_json or {})
        if event_payload.get("revision_hypothesis_id"):
            return _intervention_event_view(event, diagnosis)
        target = dict(diagnosis.target_json or {})
        previous = (
            session.query(DropInsightHypothesisModel)
            .filter(DropInsightHypothesisModel.diagnosis_id == diagnosis_id)
            .order_by(
                DropInsightHypothesisModel.round_index.asc(),
                DropInsightHypothesisModel.created_at.asc(),
            )
            .all()
        )
        parent = next(
            (
                item
                for item in previous
                if item.id == event_payload.get("hypothesis_id")
            ),
            None,
        )
    finally:
        session.close()

    message = str(event_payload.get("message") or "").strip()
    action = str(event_payload.get("action") or "ADD_CONTEXT")
    round_index = int(event_payload.get("round_index") or 1)
    prefix_by_action = {
        "ADD_CONTEXT": "用户补充上下文后待验证",
        "CHALLENGE_HYPOTHESIS": "用户质疑上一假设后待重新验证",
        "CHANGE_DIRECTION": "用户要求切换方向后待验证",
        "CONTINUE_INVESTIGATION": "证据不足后继续调查",
    }
    baseline = {
        "category": f"USER_{action}",
        "statement": f"{prefix_by_action.get(action, '用户干预后待验证')}：{message}",
        "expected": ["新一轮可信采集证据与用户补充的方向在同一目标和时间窗内一致"],
        "falsification": ["补充证据与该方向不一致或出现更强的替代解释"],
        "tool_name": _feedback_tool(message, parent),
    }
    binding = _current_target_binding(diagnosis)
    allowed_tools = _available_planner_tools(diagnosis, binding)
    if not allowed_tools:
        raise ValueError("bound Agent exposes no executable diagnostic collectors")
    attempted_tools = {item.tool_name for item in list_tool_calls(diagnosis_id)}
    skill_activation = _apply_active_planner_skill(
        diagnosis,
        diagnosis_id,
        baseline,
        target,
        round_index=round_index,
        phase="INTERVENTION_REPLAN",
        attempted_tools=attempted_tools,
        available_tools=allowed_tools,
        reuse_existing=(action != "CHANGE_DIRECTION"),
    )
    skill_tool = _skill_selected_tool(skill_activation, allowed_tools)
    if skill_tool is not None:
        baseline["tool_name"] = skill_tool
    retrieval_trace = _record_planner_knowledge_retrieval(
        diagnosis_id,
        query=diagnosis.query,
        category=f"USER_{action}",
        phase="INTERVENTION_REPLAN",
        effect_key=f"{event_payload['intervention_id']}:knowledge_retrieval",
        user_correction=message,
        round_index=round_index,
    )
    proposal = propose_hypothesis_plan(
        diagnosis_id=diagnosis_id,
        query=diagnosis.query,
        target=target,
        category=f"USER_{action}",
        rule_plan=baseline,
        prior_hypotheses=[
            {
                "statement": item.statement,
                "status": item.status,
                "round": item.round_index,
            }
            for item in previous
        ],
        user_correction=message,
        allowed_tools=allowed_tools,
        route_priors=_successful_tool_route_priors(),
        active_skill=skill_activation,
        retrieval_trace=retrieval_trace,
    )
    candidate = (proposal or {}).get("hypotheses", [{}])[0]
    statement = candidate.get("statement") or baseline["statement"]
    expected = candidate.get("expected_observations") or baseline["expected"]
    falsification = candidate.get("falsification_criteria") or baseline["falsification"]
    reason = (proposal or {}).get("reasoning_summary") or (
        "用户在页面中干预探索方向；保留旧分支审计，并开启新一轮证据收集。"
    )
    revision = create_hypothesis(
        diagnosis_id,
        CreateHypothesisRequest(
            statement=statement,
            expected_observations=expected,
            falsification_criteria=falsification,
        ),
        source=(
            "MODEL_INTERVENTION_REPLAN"
            if proposal
            else "USER_INTERVENTION_FALLBACK"
        ),
        round_index=round_index,
        parent_hypothesis_id=parent.id if parent is not None else None,
        generation_reason=reason,
        effect_key=f"{event_payload['intervention_id']}:hypothesis",
    )
    if revision is None:
        raise ValueError("diagnosis not found")
    proposed_tool = (proposal or {}).get("tool_name")
    fallback_tool = baseline["tool_name"]
    if fallback_tool not in allowed_tools:
        fallback_tool = (
            "collect_sys_metrics"
            if "collect_sys_metrics" in allowed_tools
            else allowed_tools[0]
        )
    tool_name = (
        skill_tool
        or (proposed_tool if proposed_tool in allowed_tools else fallback_tool)
    )
    tool_call = request_tool_call(
        diagnosis_id,
        CreateToolCallRequest(
            hypothesis_id=revision.id,
            tool_name=tool_name,
            arguments=_planner_tool_arguments(
                tool_name, target, query=diagnosis.query
            ),
        ),
        requested_by="user:intervention-replanner",
        effect_key=f"{event_payload['intervention_id']}:tool_call",
    )

    applied_effect = f"{event_payload['intervention_id']}:applied"
    session = new_session()
    try:
        event = (
            session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.id == event_id,
                DropInsightEventModel.diagnosis_id == diagnosis_id,
            )
            .with_for_update()
            .one()
        )
        updated_payload = dict(event.payload_json or {})
        updated_payload.update(
            {
                "revision_hypothesis_id": revision.id,
                "tool_call_id": tool_call.id if tool_call is not None else None,
                "selected_tool": tool_name,
                "skill_reuse": _skill_event_summary(skill_activation),
            }
        )
        event.payload_json = updated_payload
        _append_event(
            session,
            diagnosis_id,
            "diagnosis.intervention_applied",
            "SYSTEM",
            {
                "intervention_id": updated_payload["intervention_id"],
                "action": action,
                "hypothesis_id": updated_payload.get("hypothesis_id"),
                "revision_hypothesis_id": revision.id,
                "tool_call_id": tool_call.id if tool_call is not None else None,
                "round_index": round_index,
                "skill_reuse": _skill_event_summary(skill_activation),
            },
            now_utc(),
            effect_key=applied_effect,
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        if not _event_effect_exists(diagnosis_id, applied_effect):
            raise
    finally:
        session.close()

    session = new_session()
    try:
        event = session.get(DropInsightEventModel, event_id)
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if event is None or diagnosis is None:
            raise ValueError("diagnosis intervention not found")
        return _intervention_event_view(event, diagnosis)
    finally:
        session.close()


def submit_diagnosis_feedback(
    diagnosis_id: str,
    payload: SubmitDiagnosisFeedbackRequest,
    *,
    created_by: str,
) -> DropInsightFeedbackModel | None:
    """Persist human correction and start a new diagnostic round when needed."""
    session = new_session()
    try:
        diagnosis = _lock_diagnosis(session, diagnosis_id)
        if diagnosis is None:
            return None
        report = session.get(DropInsightReportModel, payload.report_id) if payload.report_id else (
            session.query(DropInsightReportModel)
            .filter(DropInsightReportModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightReportModel.created_at.desc())
            .first()
        )
        if report is not None and report.diagnosis_id != diagnosis_id:
            raise ValueError("report does not belong to diagnosis")
        hypothesis_id = payload.hypothesis_id or (report.hypothesis_id if report else None)
        parent = session.get(DropInsightHypothesisModel, hypothesis_id) if hypothesis_id else None
        if parent is not None and parent.diagnosis_id != diagnosis_id:
            raise ValueError("hypothesis does not belong to diagnosis")
        timestamp = now_utc()
        feedback = DropInsightFeedbackModel(
            id=f"feedback_{uuid4().hex}",
            diagnosis_id=diagnosis_id,
            report_id=report.id if report else None,
            hypothesis_id=parent.id if parent else None,
            feedback_label=payload.feedback_label,
            predicted_conclusion=report.conclusion if report else "",
            corrected_cause=(payload.corrected_cause or "").strip() or None,
            feedback_note=(payload.feedback_note or "").strip() or None,
            requested_replan=bool(
                payload.request_replan and payload.feedback_label in {"partial", "wrong"}
            ),
            created_by=created_by,
            created_at=timestamp,
        )
        if parent is not None:
            parent.status = (
                "SUPPORTED" if payload.feedback_label == "correct"
                else "COUNTER" if payload.feedback_label == "wrong"
                else "INCONCLUSIVE"
            )
            parent.updated_at = timestamp
        session.add(feedback)
        _append_event(
            session, diagnosis_id, "feedback.submitted", "USER",
            {
                "feedback_id": feedback.id,
                "label": payload.feedback_label,
                "report_id": feedback.report_id,
                "hypothesis_id": feedback.hypothesis_id,
                "request_replan": feedback.requested_replan,
            }, timestamp,
        )
        _cas_session_update(
            session,
            diagnosis,
            status="HYPOTHESIZING" if feedback.requested_replan else diagnosis.status,
            timestamp=timestamp,
        )
        session.commit()
        session.refresh(feedback)
    finally:
        session.close()

    if feedback.requested_replan:
        revision = _replan_from_feedback(diagnosis_id, feedback)
        session = new_session()
        try:
            persisted = session.get(DropInsightFeedbackModel, feedback.id)
            if persisted is not None:
                persisted.revision_hypothesis_id = revision.id if revision else None
                session.commit()
                session.refresh(persisted)
                feedback = persisted
        finally:
            session.close()
    # 已发布技能的真实效果由人工反馈闭环监控。连续错误会触发自动隔离，
    # 但不会删除原始诊断、证据或旧版本。
    from .skill_evolution import record_activation_outcome

    record_activation_outcome(diagnosis_id, feedback.feedback_label)
    return feedback


def _replan_from_feedback(
    diagnosis_id: str,
    feedback: DropInsightFeedbackModel,
) -> DropInsightHypothesisModel | None:
    diagnosis = get_diagnosis(diagnosis_id)
    if diagnosis is None:
        return None
    target = diagnosis.target_json or {}
    binding = _current_target_binding(diagnosis)
    allowed_tools = _available_planner_tools(diagnosis, binding)
    if not allowed_tools:
        raise ValueError("bound Agent exposes no executable diagnostic collectors")
    previous = list_hypotheses(diagnosis_id)
    parent = next((item for item in previous if item.id == feedback.hypothesis_id), None)
    round_index = max([item.round_index or 1 for item in previous] or [1]) + 1
    correction = feedback.corrected_cause or feedback.feedback_note or "用户认为上一轮结论不完整"
    fallback_tool = _feedback_tool(correction, parent)
    if fallback_tool not in allowed_tools:
        fallback_tool = (
            "collect_sys_metrics"
            if "collect_sys_metrics" in allowed_tools
            else allowed_tools[0]
        )
    baseline = {
        "category": "HUMAN_CORRECTION",
        "statement": correction,
        "expected": ["新采集证据与用户纠正的原因在同一目标和时间窗内一致"],
        "falsification": ["补充证据与该纠正原因不一致或出现更强反证"],
        "tool_name": fallback_tool,
    }
    attempted_tools = {item.tool_name for item in list_tool_calls(diagnosis_id)}
    skill_activation = _apply_active_planner_skill(
        diagnosis,
        diagnosis_id,
        baseline,
        target,
        round_index=round_index,
        phase="FEEDBACK_REPLAN",
        attempted_tools=attempted_tools,
        available_tools=allowed_tools,
        reuse_existing=(str(feedback.feedback_label or "").lower() != "wrong"),
    )
    skill_tool = _skill_selected_tool(skill_activation, allowed_tools)
    if skill_tool is not None:
        baseline["tool_name"] = skill_tool
        fallback_tool = skill_tool
    model_attempted = True
    retrieval_trace = _record_planner_knowledge_retrieval(
        diagnosis_id,
        query=diagnosis.query,
        category="HUMAN_CORRECTION",
        phase="FEEDBACK_REPLAN",
        effect_key=f"feedback:{feedback.id}:knowledge_retrieval",
        user_correction=correction,
        round_index=round_index,
    )
    proposal = propose_hypothesis_plan(
        diagnosis_id=diagnosis_id,
        query=diagnosis.query,
        target=target,
        category="HUMAN_CORRECTION",
        rule_plan=baseline,
        prior_hypotheses=[
            {"statement": item.statement, "status": item.status, "round": item.round_index}
            for item in previous
        ],
        user_correction=correction,
        allowed_tools=allowed_tools,
        route_priors=_successful_tool_route_priors(),
        active_skill=skill_activation,
        retrieval_trace=retrieval_trace,
    )
    candidate = (proposal or {}).get("hypotheses", [{}])[0]
    statement = candidate.get("statement") or f"用户纠正后待验证：{correction}"
    expected = candidate.get("expected_observations") or baseline["expected"]
    falsification = candidate.get("falsification_criteria") or baseline["falsification"]
    source = "MODEL_REPLAN" if proposal else "USER_GUIDED_FALLBACK"
    reason = (proposal or {}).get("reasoning_summary") or "根据用户反馈开启新一轮取证，保留上一轮结果用于审计。"
    revision = create_hypothesis(
        diagnosis_id,
        CreateHypothesisRequest(
            statement=statement,
            expected_observations=expected,
            falsification_criteria=falsification,
        ),
        source=source,
        round_index=round_index,
        parent_hypothesis_id=parent.id if parent else None,
        generation_reason=reason,
    )
    if revision is None:
        return revision
    proposed_tool = (proposal or {}).get("tool_name")
    tool_name = (
        skill_tool
        or (proposed_tool if proposed_tool in allowed_tools else fallback_tool)
    )
    request_tool_call(
        diagnosis_id,
        CreateToolCallRequest(
            hypothesis_id=revision.id,
            tool_name=tool_name,
            arguments=_planner_tool_arguments(
                tool_name, target, query=diagnosis.query
            ),
        ),
        requested_by="system:adaptive-replanner",
    )
    return revision


def _feedback_tool(correction: str, parent: DropInsightHypothesisModel | None) -> str:
    text = correction.lower()
    if any(token in text for token in _DATABASE_QUERY_TOKENS):
        return "collect_database_diagnostics"
    if _query_mentions_go_runtime(text):
        return "collect_go_profile"
    if any(token in text for token in ("jvm", "gc", "java", "垃圾回收")):
        return "start_jvm_profile"
    if any(token in text for token in ("内存", "memory", "rss", "oom")):
        return "collect_memory_profile"
    if any(token in text for token in ("io", "磁盘", "写入", "读取")):
        return "start_ebpf_io_profile"
    if any(token in text for token in ("python", "gil", "协程")):
        return "start_pyspy_profile"
    if any(token in text for token in ("cpu", "热点", "函数", "调用栈")):
        return "start_perf_profile"
    if parent and any(token in parent.statement.lower() for token in ("cpu", "热点")):
        return "collect_sys_metrics"
    return "collect_sys_metrics"


def _validated_target_binding(session, diagnosis, *, now: datetime | None = None) -> ProcessIdentityBinding:
    target = diagnosis.target_json or {}
    raw_binding = target.get("process_binding")
    try:
        binding = ProcessIdentityBinding.from_mapping(raw_binding)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("diagnosis has no complete process binding authority") from exc
    if target.get("agent_id") != binding.agent_id or target.get("pid") != binding.pid:
        raise ValueError("diagnosis target compatibility fields disagree with process binding")
    if not SqlRepository()._validate_process_binding_in_session(
        session,
        binding,
        agent_id=binding.agent_id,
        target_pid=binding.pid,
        now=now or now_utc(),
    ):
        raise ValueError("diagnosis process binding is absent, stale, or mismatched")
    return binding


def _current_target_binding(diagnosis) -> ProcessIdentityBinding:
    session = new_session()
    try:
        persisted = session.get(DropInsightSessionModel, diagnosis.id)
        if persisted is None:
            raise ValueError("diagnosis not found")
        return _validated_target_binding(session, persisted)
    finally:
        session.close()


def _jvm_profile_event(query: str) -> str:
    """Use the first affirmative symptom, not a later counterexample keyword."""
    patterns = (
        ("alloc", r"(?<![a-z0-9_])gc(?![a-z0-9_])|垃圾回收|分配|allocation"),
        ("lock", r"锁竞争|锁等待|锁路径|reentrantlock|mutex|lock contention"),
        ("wall", r"下游|等待|响应慢|延迟|latency"),
    )
    # Keep a whole Chinese sentence together: '寻找 GC、I/O 或计算反证'
    # describes alternative explanations, not the intended allocation probe.
    for sentence in re.split(r"[。；;!?！？\n]", query.casefold()):
        sentence = re.split(r"排除|反证|不是|并非|rule out|exclude|without", sentence, maxsplit=1)[0]
        matches = [(match.start(), index, event)
                   for index, (event, pattern) in enumerate(patterns)
                   if (match := re.search(pattern, sentence)) is not None]
        if matches:
            return min(matches)[2]
        if re.search(r"(?<![a-z0-9_])cpu(?![a-z0-9_])|计算热点", sentence):
            return "cpu"
    return "cpu"


def _planner_tool_arguments(
    tool_name: str,
    target: dict,
    *,
    query: str = "",
) -> dict:
    raw_binding = target.get("process_binding")
    try:
        binding = ProcessIdentityBinding.from_mapping(raw_binding)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("planner requires a complete process binding") from exc
    if target.get("agent_id") != binding.agent_id or target.get("pid") != binding.pid:
        raise ValueError("planner target disagrees with process binding")
    arguments = {
        "agent_id": binding.agent_id,
        "pid": binding.pid,
        "duration_seconds": 15,
    }
    if tool_name in {
        "start_perf_profile",
        "start_pyspy_profile",
        "start_jvm_profile",
        "collect_go_profile",
        "start_continuous_profile",
    }:
        arguments["sample_rate"] = 99
    if tool_name == "start_continuous_profile":
        # A counter-evidence pivot uses this probe as an independent repeat,
        # not as another single snapshot. Thirty seconds gives the native
        # collector multiple bounded windows while remaining inside the
        # public tool contract and the live-diagnosis budget.
        arguments["duration_seconds"] = 30
    if tool_name == "start_jvm_profile":
        arguments["event"] = _jvm_profile_event(query)
    return arguments


_CATEGORY_TOOL_PREFERENCE = {
    "DATABASE_LOCK": ["collect_database_diagnostics", "collect_sys_metrics"],
    "LOAD_SATURATION": ["collect_sys_metrics", "start_perf_profile"],
    "NETWORK_DEGRADATION": ["collect_sys_metrics"],
    "JVM_GC": ["start_jvm_profile", "collect_memory_profile", "collect_sys_metrics"],
    "DOWNSTREAM_DEPENDENCY": ["collect_sys_metrics"],
    "QUEUE_CONGESTION": ["collect_sys_metrics"],
    "CONTAINER_RESOURCE_LIMIT": ["collect_sys_metrics"],
    "NOISY_NEIGHBOR": ["collect_sys_metrics"],
    "IO_LATENCY": ["start_ebpf_io_profile", "collect_sys_metrics"],
    "PYTHON_RUNTIME": ["start_pyspy_profile", "start_perf_profile", "collect_sys_metrics"],
    "MEMORY_PRESSURE": ["collect_memory_profile", "collect_sys_metrics"],
    "FD_LEAK": ["collect_sys_metrics"],
    "LOCK_CONTENTION": [
        "start_jvm_profile",
        "start_perf_profile",
        "collect_sys_metrics",
    ],
    "GO_RUNTIME": ["collect_go_profile", "collect_sys_metrics"],
    "CPU_HOTSPOT": ["start_perf_profile", "start_continuous_profile", "collect_sys_metrics"],
}

# Each registered probe represents a genuinely different observation domain.
# These are hypotheses to test, not incident facts.  They provide an honest,
# deterministic expansion when a model is unavailable, repeats an old branch,
# or emits several candidates that all point at the same probe.
_TOOL_EXPLORATION_DIRECTIONS: dict[str, dict[str, object]] = {
    "collect_sys_metrics": {
        "evidence_domain": "SYSTEM_BASELINE",
        "statement": "异常可能来自主机资源基线或共享资源争抢，而非单一运行时路径",
        "expected": ["CPU、内存、磁盘 I/O 或网络指标至少一项与故障时间窗同步异常"],
        "falsification": ["系统基线在故障时间窗内保持平稳，且与异常没有相关性"],
    },
    "start_perf_profile": {
        "evidence_domain": "NATIVE_CPU_STACK",
        "statement": "目标进程可能存在原生调用栈热点、锁竞争或系统调用开销",
        "expected": ["CPU 样本稳定集中在可归属的调用路径或等待路径"],
        "falsification": ["调用栈样本分散，未出现稳定热点或等待路径"],
    },
    "start_continuous_profile": {
        "evidence_domain": "CONTINUOUS_CPU_STACK",
        "statement": "异常可能是短时漂移热点，单次采样窗口没有覆盖",
        "expected": ["连续采样的至少一个窗口捕获稳定热点及其时间变化"],
        "falsification": ["多个连续窗口均未捕获稳定热点或明显漂移"],
    },
    "start_ebpf_io_profile": {
        "evidence_domain": "KERNEL_IO",
        "statement": "异常可能来自块设备延迟或 I/O 队列，而非用户态计算热点",
        "expected": ["eBPF 观测到与故障同窗的 I/O 延迟、队列或阻塞分布异常"],
        "falsification": ["块设备延迟与 I/O 队列平稳，且没有同窗阻塞"],
    },
    "start_pyspy_profile": {
        "evidence_domain": "PYTHON_RUNTIME",
        "statement": "Python 运行时可能存在 GIL 竞争、用户态热点或同步阻塞路径",
        "expected": ["py-spy 样本集中在可归属的 Python 函数或等待路径"],
        "falsification": ["Python 栈样本分散，且没有稳定热点或阻塞路径"],
    },
    "start_jvm_profile": {
        "evidence_domain": "JVM_RUNTIME",
        "statement": "JVM 可能存在业务热点、GC 压力或锁竞争",
        "expected": ["JVM 采样出现稳定业务热点、GC 活动或锁等待路径"],
        "falsification": ["JVM 业务栈、GC 与锁等待均保持平稳"],
    },
    "collect_memory_profile": {
        "evidence_domain": "PROCESS_MEMORY",
        "statement": "目标进程可能存在 RSS/PSS 增长、换页或缓存压力",
        "expected": ["RSS、PSS、swap 或内存映射变化与故障时间窗一致"],
        "falsification": ["目标进程内存足迹稳定，且没有换页或映射异常"],
    },
    "collect_go_profile": {
        "evidence_domain": "GO_RUNTIME",
        "statement": "Go 运行时可能存在热点函数、goroutine 阻塞或调度开销",
        "expected": ["pprof 样本出现稳定热点、goroutine 阻塞或调度路径"],
        "falsification": ["Go 运行时采样平稳，且没有稳定热点或阻塞路径"],
    },
    "collect_database_diagnostics": {
        "evidence_domain": "DATABASE_WAIT",
        "statement": "服务延迟可能来自数据库锁等待、长事务或连接排队",
        "expected": ["数据库只读诊断发现与故障同窗的锁、事务或连接等待"],
        "falsification": ["数据库锁、事务与连接等待在故障时间窗内均正常"],
    },
}


def _autonomous_round_limit(budget: dict | None) -> int:
    """Keep live autonomous exploration bounded to four rounds by default."""

    configured = max(1, int((budget or {}).get("max_diagnosis_rounds", 6)))
    return min(configured, 4)


def _deterministic_exploration_candidates(
    allowed_tools: list[str],
    *,
    round_index: int,
    reason: str,
    prior_hypotheses: list[DropInsightHypothesisModel] | None = None,
) -> list[dict]:
    """Create fresh Chinese hypotheses across distinct observable domains."""

    known_keys = {
        stable_candidate_key(item.statement)
        for item in (prior_hypotheses or [])
    }
    candidates: list[dict] = []
    seen_domains: set[str] = set()
    for tool_name in dict.fromkeys(allowed_tools):
        direction = _TOOL_EXPLORATION_DIRECTIONS.get(tool_name)
        if direction is None:
            continue
        domain = str(direction["evidence_domain"])
        if domain in seen_domains:
            continue
        statement = f"第 {round_index} 轮：{direction['statement']}"
        if stable_candidate_key(statement) in known_keys:
            continue
        seen_domains.add(domain)
        candidates.append(
            {
                "statement": statement,
                "expected": list(direction["expected"]),
                "falsification": list(direction["falsification"]),
                "recommended_tool": tool_name,
                "evidence_domain": domain,
                "reason": reason,
            }
        )
    return candidates


def _merge_replan_candidates(
    model_candidates: list[dict],
    deterministic_candidates: list[dict],
    *,
    top_k: int,
    prior_hypotheses: list[DropInsightHypothesisModel],
) -> list[dict]:
    """Prefer a model proposal, then force breadth across observable domains."""

    known_keys = {stable_candidate_key(item.statement) for item in prior_hypotheses}
    seen_keys: set[str] = set()
    merged: list[dict] = []

    def add(candidate: dict) -> None:
        key = stable_candidate_key(str(candidate.get("statement") or ""))
        if not candidate.get("statement") or key in known_keys or key in seen_keys:
            return
        seen_keys.add(key)
        merged.append(candidate)

    if model_candidates:
        add(model_candidates[0])
    first_model_tool = (
        str(model_candidates[0].get("recommended_tool") or "")
        if model_candidates
        else ""
    )
    for candidate in deterministic_candidates:
        if candidate.get("recommended_tool") != first_model_tool:
            add(candidate)
            if len(merged) >= max(1, top_k):
                return merged
    for candidate in model_candidates[1:]:
        add(candidate)
        if len(merged) >= max(1, top_k):
            return merged
    for candidate in deterministic_candidates:
        add(candidate)
        if len(merged) >= max(1, top_k):
            break
    return merged

_TASK_KIND_NAMES = {item["name"] for item in TASK_KINDS}


_RUNTIME_SPECIFIC_TOOLS = {
    "start_pyspy_profile": "PYTHON",
    "start_jvm_profile": "JAVA",
    "collect_go_profile": "GO",
}


def _runtime_family_from_identity(identity: str) -> str | None:
    value = str(identity or "").casefold().removesuffix(" (deleted)").rsplit("/", 1)[-1]
    if "go-hotspot" in value or "golang" in value:
        return "GO"
    if value.startswith("python") or value == "uwsgi":
        return "PYTHON"
    if value == "java" or value.startswith(("java-hotspot", "jvm-hotspot")):
        return "JAVA"
    if "cpp" in value or "c++" in value:
        return "CPP"
    return None


def _diagnosis_runtime_family(diagnosis) -> str | None:
    """Infer a bound process runtime only to remove incompatible profilers."""

    target = diagnosis.target_json or {}
    binding = target.get("process_binding")
    binding = binding if isinstance(binding, dict) else {}
    # The server-issued process binding is authoritative.  A display/service
    # label may be stale or contain a different runtime name (for example a Go
    # binary behind a service called ``python-api``), so inspect it only after
    # the bound executable.
    bound_family = _runtime_family_from_identity(binding.get("executable_identity", ""))
    if binding.get("executable_identity"):
        # Unknown bound executables must not inherit a runtime from prose or
        # service names. JVM attach can deliver SIGQUIT to a non-JVM target.
        return bound_family
    labelled_family = _runtime_family_from_identity(
        " ".join(str(value or "") for value in (target.get("service"), target.get("process")))
    )
    if labelled_family is not None:
        return labelled_family
    query = str(getattr(diagnosis, "query", "") or "")
    if _query_mentions_go_runtime(query):
        return "GO"
    return None


def _runtime_compatible_tools(diagnosis, tools: list[str]) -> list[str]:
    runtime_family = _diagnosis_runtime_family(diagnosis)
    return [
        tool_name
        for tool_name in tools
        if _RUNTIME_SPECIFIC_TOOLS.get(tool_name) is None
        or _RUNTIME_SPECIFIC_TOOLS[tool_name] == runtime_family
    ]


def _runtime_tool_is_compatible(diagnosis, tool_name: str) -> bool:
    runtime_family = _diagnosis_runtime_family(diagnosis)
    required_family = _RUNTIME_SPECIFIC_TOOLS.get(tool_name)
    return (
        required_family is None
        or runtime_family == required_family
    )


def _runtime_preferred_tools(diagnosis, tools: list[str]) -> list[str]:
    """Keep an eligible runtime profiler in the bounded replan frontier.

    Registry ordering must not spend the entire four-round budget on generic
    perf probes before trying JVM/Go/Python evidence. This is only a prior;
    the capability/policy filters and model/LATS selection still apply.
    """
    family = _diagnosis_runtime_family(diagnosis)
    return sorted(tools, key=lambda name: 0 if (
        family is not None and _RUNTIME_SPECIFIC_TOOLS.get(name) == family
    ) else 1)


def _query_mentions_go_runtime(query: str) -> bool:
    query = query.casefold()
    return any(
        token in query
        for token in (
            "go pprof",
            "golang",
            "goroutine",
            "go 服务",
            "go-hotspot",
            "go runtime",
        )
    )


def _primary_intent_query(query: str) -> str:
    """Return clauses that describe the observed symptom, not counter-checks.

    Fault reports often keep the symptom and the requested exclusions in one
    Chinese sentence, separated only by commas. Feeding that whole sentence
    to the deterministic router lets a phrase such as ``排除 I/O`` override a
    source-hotspot symptom. Keep positive clauses and drop explicit
    falsification/control clauses before category routing. ``不排除`` is a
    positive candidate statement and therefore remains eligible.
    """

    normalized = str(query or "").casefold()
    if not normalized.strip():
        return normalized
    counter_markers = (
        "排除",
        "反证",
        "不能单独解释",
        "不要凭",
        "rule out",
        "exclude",
        "counter evidence",
        "counter-evidence",
    )
    primary_clauses = []
    for clause in re.split(r"[。！？!?\n,，；;]", normalized):
        clause = clause.strip()
        if not clause:
            continue
        is_counter_clause = any(marker in clause for marker in counter_markers)
        if is_counter_clause and "不排除" not in clause:
            continue
        primary_clauses.append(clause)
    return " ".join(primary_clauses) or normalized


def _should_route_downstream_dependency(
    intent_query: str,
    full_query: str,
) -> bool:
    """Infer a downstream cause without letting counter-checks steal routing.

    Operators often put the observed symptom in the first sentence and the
    suspected dependency in the requested investigation steps. A generic
    latency symptom therefore needs that later context. If the first sentence
    already names a concrete domain such as I/O, memory, GC or lock contention,
    however, a later request to *exclude* downstream waiting must not override
    it.
    """

    query = str(full_query or "").casefold()
    symptom_clause = re.split(r"[。！？!?\n]", query, maxsplit=1)[0].strip()
    downstream_markers = (
        "下游",
        "依赖服务",
        "rpc",
        "upstream",
        "downstream",
    )
    generic_latency_markers = (
        "端到端延迟",
        "请求延迟",
        "响应延迟",
        "响应变慢",
        "请求变慢",
        "latency",
    )
    explicit_domain_markers = (
        "入口负载",
        "到达率",
        "请求被拒绝",
        "load saturation",
        "丢包",
        "网络",
        "重传",
        "network",
        "队列",
        "积压",
        "backlog",
        "consumer lag",
        "噪声邻居",
        "同宿主机",
        "资源争抢",
        "文件描述符",
        "fd 泄漏",
        "锁竞争",
        "futex",
        "mutex",
        "自旋锁",
        "i/o",
        "磁盘",
        "写入",
        "读取",
        "filechannel",
        "fdatasync",
        "内存",
        "memory",
        "rss",
        "pss",
        "堆外",
        "offheap",
        "full gc",
        "垃圾回收",
        "gc 压力",
        "计算热点",
        "热点函数",
        "source hotspot",
        "cpu 持续",
        "cpu 升高",
        "cpu 异常",
        "cpu 飙",
    )
    if any(marker in symptom_clause for marker in explicit_domain_markers):
        return False
    if any(marker in symptom_clause for marker in downstream_markers):
        return True
    if not any(marker in symptom_clause for marker in generic_latency_markers):
        return False
    return any(marker in query for marker in downstream_markers)


def _lock_profile_tool(diagnosis) -> str:
    """Choose the first runtime-aware lock probe after identity is bound.

    Native perf remains a later deep probe, but instrumented system metrics are
    the cheaper and more reliable first observation for the C++ demo. Java can
    start with async-profiler because its lock event is runtime-specific.
    """

    return (
        "start_jvm_profile"
        if _diagnosis_runtime_family(diagnosis) == "JAVA"
        else "collect_sys_metrics"
    )


def _available_planner_tools(diagnosis, binding: ProcessIdentityBinding) -> list[str]:
    """Return executable tools advertised by the bound native Agent."""

    session = new_session()
    try:
        agent = session.get(AgentModel, binding.agent_id)
        capabilities = set(agent.capabilities or []) if agent is not None else set()
    finally:
        session.close()
    available = []
    for tool in TOOLS:
        collector = TOOL_TO_COLLECTOR.get(tool["name"])
        if collector is None or collector not in _TASK_KIND_NAMES:
            continue
        required = set(tool.get("required_capabilities") or [])
        if required.issubset(capabilities):
            available.append(tool["name"])
    return _runtime_preferred_tools(diagnosis, _runtime_compatible_tools(diagnosis, available))


def _category_allowed_tools(category: str, available: list[str]) -> list[str]:
    preferred = _CATEGORY_TOOL_PREFERENCE.get(category, ["collect_sys_metrics"])
    return [tool_name for tool_name in preferred if tool_name in available]


def _counter_evidence_pivot_from_envelopes(
    evidence_rows: list[dict],
    allowed_tools: list[str],
) -> dict | None:
    """Turn a trusted counter observation into an auditable next direction.

    This is deliberately narrow: it does not infer a root cause from prose.
    It only promotes analyzer-produced predicate fields that already passed
    the Evidence Gate. A single perf profile that falsifies a lock/kernel
    branch by exposing a dominant user-space hotspot is independently repeated
    with bounded continuous profiling before it can support a conclusion.
    """

    if "start_continuous_profile" not in allowed_tools:
        return None
    for row in evidence_rows:
        envelope = row.get("envelope") if isinstance(row, dict) else None
        if not isinstance(envelope, dict):
            continue
        observation = envelope.get("observation")
        metadata = observation.get("metadata") if isinstance(observation, dict) else None
        predicate = metadata.get("hypothesis_predicate") if isinstance(metadata, dict) else None
        if not isinstance(predicate, dict) or str(predicate.get("outcome") or "").upper() != "COUNTER":
            continue
        metrics = predicate.get("metrics")
        metrics = metrics if isinstance(metrics, dict) else {}
        dominant_function = str(metrics.get("dominant_function") or "").strip()
        try:
            dominant_percent = float(metrics.get("dominant_percent") or 0.0)
        except (TypeError, ValueError):
            dominant_percent = 0.0
        reason = str(predicate.get("reason") or "").casefold()
        exposes_user_hotspot = (
            dominant_function
            and dominant_percent >= 60.0
            and any(
                marker in reason
                for marker in (
                    "user-space hotspot",
                    "userspace hotspot",
                    "non-lock user-space hotspot",
                    "用户态热点",
                )
            )
        )
        if not exposes_user_hotspot:
            continue
        return {
            "category": "COUNTER_EVIDENCE_PIVOT",
            "tool_name": "start_continuous_profile",
            "evidence_domain": "CONTINUOUS_CPU_STACK",
            "statement": (
                "反证暴露了可归属的用户态热点；需通过连续多窗口采样验证热点是否稳定"
            ),
            "expected": [
                f"多个连续窗口重复出现用户态热点 {dominant_function}",
                "热点在连续窗口中保持主导且可归属到目标进程",
            ],
            "falsification": [
                "连续窗口未重复出现该热点，或主要样本转为锁、内核等待或其他路径",
            ],
            "reason": (
                f"上一轮可信反证观测到 {dominant_function} 占比 "
                f"{dominant_percent:.1f}%，因此优先独立复验，而不是盲目枚举无关证据域。"
            ),
            "trigger_evidence_id": row.get("evidence_id"),
            "trigger_predicate": predicate,
            "prior_probability": 1.0,
            "estimated_value": 0.95,
        }
    return None


def _counter_evidence_pivot(report_id: str, allowed_tools: list[str]) -> dict | None:
    """Load only the immutable counter Evidence cited by one report."""

    session = new_session()
    try:
        report = session.get(DropInsightReportModel, report_id)
        evidence_ids = list(report.counter_evidence_refs_json or []) if report else []
        if not evidence_ids:
            return None
        rows = (
            session.query(DropInsightEvidenceModel)
            .filter(DropInsightEvidenceModel.id.in_(evidence_ids))
            .all()
        )
        by_id = {row.id: row for row in rows}
        ordered = [
            {
                "evidence_id": evidence_id,
                "envelope": by_id[evidence_id].envelope_json or {},
            }
            for evidence_id in evidence_ids
            if evidence_id in by_id
        ]
    finally:
        session.close()
    return _counter_evidence_pivot_from_envelopes(ordered, allowed_tools)


def _replan_from_counter_evidence(
    diagnosis_id: str,
    parent_hypothesis_id: str,
    report_id: str,
) -> DropInsightHypothesisModel | None:
    """Open a bounded new round when trusted evidence falsifies the primary hypothesis."""
    diagnosis = get_diagnosis(diagnosis_id)
    if diagnosis is None or getattr(diagnosis, "status", None) in {
        "COMPLETED",
        "INSUFFICIENT_EVIDENCE",
    }:
        return None
    if _stop_replanning_at_deadline(diagnosis, report_id):
        return None
    previous = list_hypotheses(diagnosis_id)
    parent = next((item for item in previous if item.id == parent_hypothesis_id), None)
    if parent is None:
        return None
    tree_depth = (parent.round_index or 1) + 1
    round_contract = _report_round_contract_snapshot(diagnosis_id)
    round_index = max(
        [
            int(item)
            for item in (round_contract.get("round_indexes") or [])
        ]
        or [int(parent.round_index or 1)]
    ) + 1
    max_rounds = _autonomous_round_limit(diagnosis.budget_json or {})
    if round_index > max_rounds:
        _record_lats_termination(
            diagnosis_id,
            reason="BUDGET_EXHAUSTED",
            detail="可信反证后需要继续扩展，但已达到最多四轮的自动探索边界。",
            effect_key=f"report:{report_id}:lats:round-budget-terminated",
        )
        return None
    target = diagnosis.target_json or {}
    binding = _current_target_binding(diagnosis)
    available_tools = _available_planner_tools(diagnosis, binding)
    attempted = {item.tool_name for item in list_tool_calls(diagnosis_id)}
    existing_call = _tool_call_by_effect_key(
        diagnosis_id,
        f"report:{report_id}:counter:tool_call",
    )
    allowed_tools = [
        item
        for item in available_tools
        if item not in attempted
        or (existing_call is not None and item == existing_call.tool_name)
    ]
    if not allowed_tools:
        _record_lats_termination(
            diagnosis_id,
            reason="NO_ELIGIBLE_CHILD",
            detail="反证后没有未尝试且满足 Agent 能力、策略和预算的替代证据域。",
            effect_key=f"report:{report_id}:lats:no-tool-terminated",
        )
        return None
    evidence_pivot = _counter_evidence_pivot(report_id, allowed_tools)
    tool_name = (
        existing_call.tool_name
        if existing_call is not None
        else str((evidence_pivot or {}).get("tool_name") or allowed_tools[0])
    )
    baseline_direction = evidence_pivot or _TOOL_EXPLORATION_DIRECTIONS.get(tool_name, {})
    baseline = {
        "category": "COUNTER_EVIDENCE_REPLAN",
        "statement": (
            f"第 {round_index} 轮："
            + str(
                baseline_direction.get("statement")
                or "可信反证已推翻上一轮假设，需验证新的候选原因"
            )
        ),
        "expected": list(
            baseline_direction.get("expected")
            or ["新的独立证据能区分上一轮未覆盖的候选原因"]
        ),
        "falsification": list(
            baseline_direction.get("falsification")
            or ["该证据域保持平稳，无法支持新的候选原因"]
        ),
        "tool_name": tool_name,
    }
    # Trusted counter-evidence invalidates the old Skill branch as a route
    # prior.  Re-run retrieval while excluding that activation so a different
    # published Skill (or the rule fallback) can take over this round.
    skill_activation = _apply_active_planner_skill(
        diagnosis,
        diagnosis_id,
        baseline,
        target,
        round_index=round_index,
        phase="COUNTER_EVIDENCE_REPLAN",
        attempted_tools=attempted,
        available_tools=allowed_tools,
        reuse_existing=False,
    )
    skill_tool = _skill_selected_tool(skill_activation, allowed_tools)
    # The trusted observation has higher authority than a retrieved route
    # prior. Skill activation is still persisted and visible in the tree, but
    # it cannot steer away from evidence that exposes a better next probe.
    if skill_tool is not None and evidence_pivot is None:
        baseline["tool_name"] = skill_tool
        tool_name = skill_tool
    proposal = None
    if not _REPORT_EFFECT_RECONCILIATION.get():
        retrieval_trace = _record_planner_knowledge_retrieval(
            diagnosis_id,
            query=diagnosis.query,
            category="COUNTER_EVIDENCE_REPLAN",
            phase="COUNTER_EVIDENCE_REPLAN",
            effect_key=f"report:{report_id}:counter:knowledge_retrieval",
            user_correction="可信反证已推翻上一轮主假设",
            round_index=round_index,
        )
        proposal = propose_hypothesis_plan(
            diagnosis_id=diagnosis_id,
            query=diagnosis.query,
            target=target,
            category="COUNTER_EVIDENCE_REPLAN",
            rule_plan=baseline,
            prior_hypotheses=[item.to_dict() for item in previous],
            evidence_summary=[
                {
                    "result": "counter_evidence",
                    "reflection": _latest_lats_reflection(
                        diagnosis_id, parent_hypothesis_id
                    ),
                    "reflection_is_evidence": False,
                }
            ],
            user_correction="可信反证已推翻上一轮主假设",
            allowed_tools=allowed_tools,
            route_priors=_successful_tool_route_priors(),
            active_skill=skill_activation,
            retrieval_trace=retrieval_trace,
        )
    reason = (proposal or {}).get("reasoning_summary") or (
        "可信反证推翻上一轮主假设，反思结果已注入下一轮候选扩展。"
    )
    model_assisted = bool(proposal) and (
        proposal.get("language_normalization") != "SERVER_RULE_FALLBACK"
    )
    model_candidates = []
    if proposal:
        model_candidates = [
            {
                "statement": item["statement"],
                "expected_observations": item["expected_observations"],
                "falsification_criteria": item["falsification_criteria"],
                "reason": item.get("rationale") or reason,
                "prior_probability": item.get("prior_probability"),
                "estimated_value": item.get("estimated_value"),
                "recommended_tool": proposal.get("tool_name") or tool_name,
            }
            for item in proposal.get("hypotheses") or []
        ]
    deterministic_candidates = _deterministic_exploration_candidates(
        allowed_tools,
        round_index=round_index,
        reason=reason,
        prior_hypotheses=previous,
    )
    pivot_candidates = []
    if evidence_pivot is not None:
        pivot_candidates.append(
            {
                "statement": baseline["statement"],
                "expected_observations": baseline["expected"],
                "falsification_criteria": baseline["falsification"],
                "reason": evidence_pivot["reason"],
                "prior_probability": evidence_pivot["prior_probability"],
                "estimated_value": evidence_pivot["estimated_value"],
                "recommended_tool": evidence_pivot["tool_name"],
                "evidence_domain": evidence_pivot["evidence_domain"],
            }
        )
    raw_candidates = _merge_replan_candidates(
        [*pivot_candidates, *model_candidates],
        deterministic_candidates,
        top_k=LATSConfig.from_budget(diagnosis.budget_json or {}).top_k,
        prior_hypotheses=previous,
    )
    # An empty set here means only that no *new* semantic candidate survived
    # de-duplication. A durable unvisited sibling can still exist and must be
    # considered by the global LATS frontier before declaring exhaustion.
    revision, selected_tool, selection = _create_and_select_lats_round(
        diagnosis_id,
        raw_candidates,
        source="MODEL_REPLAN" if model_assisted else "COUNTER_EVIDENCE_RULE",
        round_index=tree_depth,
        execution_round_index=round_index,
        parent_hypothesis_id=parent.id,
        generation_reason=reason,
        default_tool=(
            str(evidence_pivot["tool_name"])
            if evidence_pivot is not None
            else skill_tool or (proposal or {}).get("tool_name") or tool_name
        ),
        allowed_tools=allowed_tools,
        phase="COUNTER_EVIDENCE_EXPANSION",
        effect_prefix=f"report:{report_id}:counter",
        preferred_candidate_key=(
            stable_candidate_key(baseline["statement"])
            if evidence_pivot is not None
            else None
        ),
    )
    if revision is not None:
        selected_tool = (
            existing_call.tool_name
            if existing_call is not None
            else (
                str(evidence_pivot["tool_name"])
                if evidence_pivot is not None
                else skill_tool or selected_tool or tool_name
            )
        )
        call = request_tool_call(
            diagnosis_id,
            CreateToolCallRequest(
                hypothesis_id=revision.id,
                tool_name=selected_tool,
                arguments=_planner_tool_arguments(
                    selected_tool, target, query=diagnosis.query
                ),
            ),
            requested_by="system:counter-evidence-replanner",
            effect_key=f"report:{report_id}:counter:tool_call",
        )
        if call is not None:
            _record_lats_action_dispatched(
                diagnosis_id,
                revision.id,
                call,
                effect_prefix=f"report:{report_id}:counter:lats",
            )
            session = new_session()
            try:
                timestamp = now_utc()
                created = _append_event(
                    session,
                    diagnosis_id,
                    "planner.counter_replanned",
                    "SYSTEM",
                    {
                        "round_index": round_index,
                        "previous_hypothesis_id": parent.id,
                        "hypothesis_id": revision.id,
                        "tool_name": call.tool_name,
                        "planner_kind": (
                            "MODEL_ASSISTED"
                            if model_assisted
                            else "DETERMINISTIC_FALLBACK"
                        ),
                        "reason": revision.generation_reason,
                        "requires_approval": (
                            call.policy_decision == "REQUIRE_APPROVAL"
                        ),
                        "lats_selection": selection,
                        "skill_reuse": _skill_event_summary(skill_activation),
                        "evidence_pivot": evidence_pivot,
                    },
                    timestamp,
                    effect_key=f"report:{report_id}:counter:event",
                )
                if created:
                    persisted = _lock_diagnosis(session, diagnosis_id)
                    if persisted is not None and persisted.status not in {
                        "COMPLETED",
                        "INSUFFICIENT_EVIDENCE",
                    }:
                        _cas_session_update(
                            session,
                            persisted,
                            status="HYPOTHESIZING",
                            timestamp=timestamp,
                        )
                session.commit()
            except IntegrityError:
                session.rollback()
                if not _event_effect_exists(
                    diagnosis_id,
                    f"report:{report_id}:counter:event",
                ):
                    raise
            finally:
                session.close()
    return revision


def _successful_tool_route_priors(limit: int = 20) -> list[dict]:
    """从已验证报告提取成功工具路线，作为弱先验而非硬编码答案。"""
    session = new_session()
    try:
        rows = (
            session.query(DropInsightReportModel)
            .filter(DropInsightReportModel.verification_json.isnot(None))
            .order_by(DropInsightReportModel.created_at.desc())
            .limit(limit)
            .all()
        )
        priors = []
        for report in rows:
            if (report.verification_json or {}).get("status") != "VERIFIED":
                continue
            calls = (
                session.query(DropInsightToolCallModel)
                .filter(DropInsightToolCallModel.diagnosis_id == report.diagnosis_id)
                .order_by(DropInsightToolCallModel.created_at.asc())
                .all()
            )
            route = [item.tool_name for item in calls if item.status == "COMPLETED"]
            if route:
                priors.append({"route": route, "verified": True})
        return priors
    finally:
        session.close()


def _stop_replanning_at_deadline(diagnosis, report_id: str) -> bool:
    check = probe_deadline_check(diagnosis, "start_perf_profile", {"duration_seconds": 15})
    if check["result"] == "PASS":
        return False
    _record_lats_termination(
        diagnosis.id, reason="BUDGET_EXHAUSTED",
        detail="剩余会话时间不足以完成下一轮采集及报告，保留现有证据和未验证结论。",
        effect_key=f"report:{report_id}:lats:wall-clock-terminated",
    )
    return True


def _replan_after_insufficient_evidence(
    diagnosis_id: str,
    parent_hypothesis_id: str,
    report_id: str,
    *,
    continuation_reason: str = "上一证据域不足以建立结论",
) -> DropInsightHypothesisModel | None:
    """证据不足或仅部分支持时自动换证据域继续交叉验证。"""
    diagnosis = get_diagnosis(diagnosis_id)
    if diagnosis is None or getattr(diagnosis, "status", None) in {
        "COMPLETED",
        "INSUFFICIENT_EVIDENCE",
    }:
        return None
    if _stop_replanning_at_deadline(diagnosis, report_id):
        return None
    target = diagnosis.target_json or {}
    binding = _current_target_binding(diagnosis)
    session = new_session()
    try:
        agent = session.get(AgentModel, binding.agent_id)
        if agent is None or agent.status != "ONLINE":
            _record_lats_termination(
                diagnosis_id,
                reason="AGENT_UNAVAILABLE",
                detail="目标采集 Agent 当前不在线，无法继续执行真实探针。",
                effect_key=f"report:{report_id}:lats:agent-unavailable",
            )
            return None
        capabilities = set(agent.capabilities or [])
    finally:
        session.close()
    previous = list_hypotheses(diagnosis_id)
    parent = next((item for item in previous if item.id == parent_hypothesis_id), None)
    if parent is None:
        return None
    tree_depth = (parent.round_index or 1) + 1
    round_contract = _report_round_contract_snapshot(diagnosis_id)
    round_index = max(
        [
            int(item)
            for item in (round_contract.get("round_indexes") or [])
        ]
        or [int(parent.round_index or 1)]
    ) + 1
    max_rounds = _autonomous_round_limit(diagnosis.budget_json or {})
    if round_index > max_rounds:
        _record_lats_termination(
            diagnosis_id,
            reason="BUDGET_EXHAUSTED",
            detail="证据仍需交叉验证，但已达到最多四轮的自动探索边界。",
            effect_key=f"report:{report_id}:lats:round-budget-terminated",
        )
        return None
    attempted = {item.tool_name for item in list_tool_calls(diagnosis_id)}
    all_tools = _available_planner_tools(diagnosis, binding)
    existing_call = _tool_call_by_effect_key(
        diagnosis_id,
        f"report:{report_id}:insufficient:tool_call",
    )
    remaining = [
        item for item in all_tools
        if (
            item not in attempted
            or (existing_call is not None and item == existing_call.tool_name)
        )
        and TOOL_TO_COLLECTOR[item] in capabilities
    ]
    if not remaining:
        _record_lats_termination(
            diagnosis_id,
            reason="NO_ELIGIBLE_CHILD",
            detail="没有未尝试且满足 Agent 能力、策略与预算门禁的真实探针。",
            effect_key=f"report:{report_id}:lats:no-frontier-terminated",
        )
        return None
    fallback_tool = (
        existing_call.tool_name if existing_call is not None else remaining[0]
    )
    baseline_direction = _TOOL_EXPLORATION_DIRECTIONS.get(fallback_tool, {})
    baseline = {
        "category": "INSUFFICIENT_EVIDENCE_REPLAN",
        "statement": (
            f"第 {round_index} 轮："
            + str(
                baseline_direction.get("statement")
                or "当前证据覆盖不足，需切换证据域验证新的候选原因"
            )
        ),
        "expected": list(
            baseline_direction.get("expected")
            or ["新的独立采集结果能够支持或推翻至少一个候选假设"]
        ),
        "falsification": list(
            baseline_direction.get("falsification")
            or ["补充证据仍无区分力，但采集失败不视为反证"]
        ),
        "tool_name": fallback_tool,
    }
    # Insufficient evidence does not refute a Skill.  Continue the persisted
    # route and ask it for the next still-eligible, not-yet-attempted probe.
    skill_activation = _apply_active_planner_skill(
        diagnosis,
        diagnosis_id,
        baseline,
        target,
        round_index=round_index,
        phase="INSUFFICIENT_EVIDENCE_REPLAN",
        attempted_tools=attempted,
        available_tools=remaining,
        reuse_existing=True,
    )
    skill_tool = _skill_selected_tool(skill_activation, remaining)
    if skill_tool is not None:
        baseline["tool_name"] = skill_tool
        fallback_tool = skill_tool
    proposal = None
    if not _REPORT_EFFECT_RECONCILIATION.get():
        retrieval_trace = _record_planner_knowledge_retrieval(
            diagnosis_id,
            query=diagnosis.query,
            category="INSUFFICIENT_EVIDENCE_REPLAN",
            phase="INSUFFICIENT_EVIDENCE_REPLAN",
            effect_key=f"report:{report_id}:insufficient:knowledge_retrieval",
            user_correction=continuation_reason,
            round_index=round_index,
        )
        proposal = propose_hypothesis_plan(
            diagnosis_id=diagnosis_id,
            query=diagnosis.query,
            target=target,
            category="INSUFFICIENT_EVIDENCE_REPLAN",
            rule_plan=baseline,
            prior_hypotheses=[item.to_dict() for item in previous],
            evidence_summary=[{
                "result": "insufficient_evidence",
                "attempted_tools": sorted(attempted),
                "continuation_reason": continuation_reason,
                "reflection": _latest_lats_reflection(
                    diagnosis_id, parent_hypothesis_id
                ),
                "reflection_is_evidence": False,
            }],
            user_correction=continuation_reason,
            allowed_tools=remaining,
            route_priors=_successful_tool_route_priors(),
            active_skill=skill_activation,
            retrieval_trace=retrieval_trace,
        )
    reason = (proposal or {}).get("reasoning_summary") or (
        f"{continuation_reason}，按剩余注册工具和历史成功路线切换取证方向。"
    )
    model_assisted = bool(proposal) and (
        proposal.get("language_normalization") != "SERVER_RULE_FALLBACK"
    )
    model_candidates = []
    if proposal:
        model_candidates = [
            {
                "statement": item["statement"],
                "expected_observations": item["expected_observations"],
                "falsification_criteria": item["falsification_criteria"],
                "reason": item.get("rationale") or reason,
                "prior_probability": item.get("prior_probability"),
                "estimated_value": item.get("estimated_value"),
                "recommended_tool": proposal.get("tool_name") or fallback_tool,
            }
            for item in proposal.get("hypotheses") or []
        ]
    deterministic_candidates = _deterministic_exploration_candidates(
        remaining,
        round_index=round_index,
        reason=reason,
        prior_hypotheses=previous,
    )
    raw_candidates = _merge_replan_candidates(
        model_candidates,
        deterministic_candidates,
        top_k=LATSConfig.from_budget(diagnosis.budget_json or {}).top_k,
        prior_hypotheses=previous,
    )
    preferred_skill_candidate_key = None
    if skill_tool is not None:
        skill_direction = _TOOL_EXPLORATION_DIRECTIONS.get(skill_tool)
        if skill_direction is not None:
            skill_candidate = {
                "statement": f"第 {round_index} 轮：{skill_direction['statement']}",
                "expected": list(skill_direction["expected"]),
                "falsification": list(skill_direction["falsification"]),
                "recommended_tool": skill_tool,
                "evidence_domain": skill_direction["evidence_domain"],
                "reason": (
                    f"{reason}；当前复用 Skill 的下一步需要验证"
                    f"{skill_direction['evidence_domain']} 证据域。"
                ),
            }
            preferred_skill_candidate_key = stable_candidate_key(
                skill_candidate["statement"]
            )
            # Keep the Skill-directed hypothesis inside the bounded Top-K
            # expansion even when a model proposes several plausible siblings.
            # Existing semantic duplicates are reconciled by
            # _create_and_select_lats_round and remain auditable tree nodes.
            raw_candidates = [
                skill_candidate,
                *[
                    item
                    for item in raw_candidates
                    if stable_candidate_key(str(item.get("statement") or ""))
                    != preferred_skill_candidate_key
                ],
            ]
    # Semantic de-duplication may remove every newly proposed candidate while
    # a previously persisted, unvisited sibling remains on the LATS frontier.
    # The selection helper performs the authoritative exhaustion check.
    revision, selected_tool, selection = _create_and_select_lats_round(
        diagnosis_id,
        raw_candidates,
        source="MODEL_REPLAN" if model_assisted else "AUTONOMOUS_RULE_FALLBACK",
        round_index=tree_depth,
        execution_round_index=round_index,
        parent_hypothesis_id=parent.id,
        generation_reason=reason,
        default_tool=skill_tool or (proposal or {}).get("tool_name") or fallback_tool,
        allowed_tools=remaining,
        phase="INSUFFICIENT_EVIDENCE_EXPANSION",
        effect_prefix=f"report:{report_id}:insufficient",
        preferred_candidate_key=preferred_skill_candidate_key,
    )
    if revision is None:
        return None
    selected_tool = (
        existing_call.tool_name
        if existing_call is not None
        else skill_tool or selected_tool or fallback_tool
    )
    call = request_tool_call(
        diagnosis_id,
        CreateToolCallRequest(
            hypothesis_id=revision.id,
            tool_name=selected_tool,
            arguments=_planner_tool_arguments(
                selected_tool, target, query=diagnosis.query
            ),
        ),
        requested_by="system:insufficient-evidence-replanner",
        effect_key=f"report:{report_id}:insufficient:tool_call",
    )
    if call is None:
        return revision
    _record_lats_action_dispatched(
        diagnosis_id,
        revision.id,
        call,
        effect_prefix=f"report:{report_id}:insufficient:lats",
    )
    session = new_session()
    try:
        timestamp = now_utc()
        created = _append_event(
            session,
            diagnosis_id,
            "planner.insufficient_replanned",
            "SYSTEM",
            {
                "round_index": round_index,
                "previous_hypothesis_id": parent.id,
                "hypothesis_id": revision.id,
                "tool_name": call.tool_name,
                "planner_kind": (
                    "MODEL_ASSISTED" if model_assisted else "DETERMINISTIC_FALLBACK"
                ),
                "reason": revision.generation_reason,
                "requires_approval": call.policy_decision == "REQUIRE_APPROVAL",
                "lats_selection": selection,
                "skill_reuse": _skill_event_summary(skill_activation),
            },
            timestamp,
            effect_key=f"report:{report_id}:insufficient:event",
        )
        if created:
            persisted = _lock_diagnosis(session, diagnosis_id)
            if persisted is not None and persisted.status not in {
                "COMPLETED",
                "INSUFFICIENT_EVIDENCE",
            }:
                _cas_session_update(
                    session,
                    persisted,
                    status="HYPOTHESIZING",
                    timestamp=timestamp,
                )
        session.commit()
    except IntegrityError:
        session.rollback()
        if not _event_effect_exists(
            diagnosis_id,
            f"report:{report_id}:insufficient:event",
        ):
            raise
    finally:
        session.close()
    return revision


def _tool_call_by_effect_key(
    diagnosis_id: str,
    effect_key: str,
) -> DropInsightToolCallModel | None:
    session = new_session()
    try:
        return (
            session.query(DropInsightToolCallModel)
            .filter(
                DropInsightToolCallModel.diagnosis_id == diagnosis_id,
                DropInsightToolCallModel.effect_key == effect_key,
            )
            .first()
        )
    finally:
        session.close()


def _event_effect_exists(diagnosis_id: str, effect_key: str) -> bool:
    session = new_session()
    try:
        return (
            session.query(DropInsightEventModel.id)
            .filter(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.effect_key == effect_key,
            )
            .first()
            is not None
        )
    finally:
        session.close()


def _record_lats_expansion_and_selection(
    diagnosis_id: str,
    candidates: list[dict],
    *,
    phase: str,
    round_index: int,
    tree_depth: int | None = None,
    parent_hypothesis_id: str | None,
    effect_prefix: str,
) -> dict | None:
    """Persist one bounded expansion and PUCT decision atomically.

    ``candidates`` must already contain server-created hypothesis ids.  This
    helper has no tool execution authority; request_tool_call() still applies
    binding, capability, policy and resource-budget gates afterwards.
    """

    session = new_session()
    try:
        diagnosis = _lock_diagnosis(session, diagnosis_id)
        if diagnosis is None:
            return None
        existing = (
            session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.effect_key == f"{effect_prefix}:selected",
            )
            .first()
        )
        if existing is not None:
            return dict(existing.payload_json or {})

        config = LATSConfig.from_budget(diagnosis.budget_json or {})
        strategy = (diagnosis.budget_json or {}).get("investigation_strategy", "LATS")
        semantics = execution_semantics(diagnosis.mode)
        if strategy == "REACT":
            semantics = {**semantics, "execution_mode": "BOUNDED_REACT", "search_strategy": "REACT"}
        timestamp = now_utc()
        _append_event(
            session,
            diagnosis_id,
            "lats.search_started",
            "SYSTEM",
            {
                "algorithm": (
                    "REACT" if strategy == "REACT" else (
                        "LATS-UCT" if config.selection_policy == "UCT" else "LATS-PUCT-EXTENSION"
                    )
                ),
                "algorithm_version": "drop-insight-lats-v1",
                "semantics": semantics,
                "config": {
                    "investigation_strategy": strategy,
                    "top_k": config.top_k,
                    "exploration_constant": config.exploration_constant,
                    "max_iterations": config.max_iterations,
                    "max_tool_calls": config.max_tool_calls,
                    "selection_policy": "REACT" if strategy == "REACT" else config.selection_policy,
                    "value_lambda": config.value_lambda,
                    "value_formula": "lambda*LM(s)+(1-lambda)*SC(s) when server SC exists",
                    "self_consistency_source": "SERVER_INDEPENDENT_SAMPLES_OR_NULL",
                },
                "safety_authority": (
                    "opaque Agent/PID binding + registered tool allowlist + "
                    "resource budget + Evidence Gate"
                ),
                "model_has_shell_access": False,
            },
            timestamp,
            effect_key=f"diagnosis:{diagnosis_id}:lats:started",
        )
        prior_events = (
            session.query(DropInsightEventModel)
            .filter(DropInsightEventModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightEventModel.sequence.asc())
            .all()
        )
        state = replay_search_events(prior_events)
        metrics = state.get("node_metrics") or {}
        parent_node_id = (
            f"hypothesis:{parent_hypothesis_id}" if parent_hypothesis_id else None
        )
        parent_visits = int((metrics.get(parent_node_id) or {}).get("visits") or 0)
        iteration = int(state.get("iteration") or 0) + 1
        durable_candidates = []
        for candidate in candidates:
            item = dict(candidate)
            if item.get("expansion_origin") == "EXISTING_UNVISITED_FRONTIER":
                item.setdefault("depth", 0)
                item.setdefault("parent_node_id", None)
            else:
                item["depth"] = max(0, int(round_index) - 1)
                item["parent_node_id"] = parent_node_id
            durable_candidates.append(item)
        if parent_node_id is None:
            parent_visits = sum(
                int((metrics.get(item["node_id"]) or {}).get("visits") or 0)
                for item in durable_candidates
            )
        _append_event(
            session,
            diagnosis_id,
            "lats.candidates_expanded",
            "AGENT",
            {
                "phase": phase,
                "iteration": iteration,
                "round_index": round_index,
                "tree_depth": tree_depth or round_index,
                "parent_node_id": parent_node_id,
                "candidate_count": len(durable_candidates),
                "candidates": durable_candidates,
                "progressive_widening": semantics["execution_mode"] == "BUDGETED_LATS",
            },
            timestamp,
            effect_key=f"{effect_prefix}:expanded",
        )
        _append_event(
            session,
            diagnosis_id,
            "lats.candidates_evaluated",
            "AGENT",
            {
                "phase": phase,
                "iteration": iteration,
                "evaluations": [
                    {
                        "node_id": item["node_id"],
                        "initial_value": item.get("initial_value"),
                        "lm_value": item.get("lm_value"),
                        "self_consistency": item.get("self_consistency"),
                        "heuristic_value": item.get("heuristic_value"),
                        "value_lambda": item.get("value_lambda"),
                        "value_formula": item.get("value_formula"),
                        "value_source": item.get("value_source"),
                    }
                    for item in durable_candidates
                ],
                "self_consistency_source": "SERVER_INDEPENDENT_SAMPLES_OR_NULL",
                "evidence_gate_is_separate": True,
            },
            timestamp,
            effect_key=f"{effect_prefix}:evaluated",
        )
        if strategy == "REACT":
            from server.app.agent_runtime.investigation_strategy import select_react_candidate
            selection = select_react_candidate(durable_candidates, metrics)
        else:
            selection = select_puct_candidate(
                durable_candidates, metrics, parent_visits=parent_visits,
                exploration_constant=config.exploration_constant,
                selection_policy=config.selection_policy,
            )
        if selection is None:
            return None
        payload = {
            **selection,
            "phase": phase,
            "iteration": iteration,
            "round_index": round_index,
            "tree_depth": tree_depth or round_index,
            "parent_node_id": parent_node_id,
            "selection_policy": "REACT" if strategy == "REACT" else config.selection_policy,
            "candidate_count": len(durable_candidates),
            "frontier_policy": "GLOBAL_ELIGIBLE_LEAF_PROGRESSIVE_WIDENING",
        }
        _append_event(
            session,
            diagnosis_id,
            "lats.node_selected",
            "AGENT",
            payload,
            timestamp,
            effect_key=f"{effect_prefix}:selected",
        )
        session.commit()
        return payload
    except IntegrityError:
        session.rollback()
        existing = (
            session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.effect_key == f"{effect_prefix}:selected",
            )
            .first()
        )
        if existing is None:
            raise
        return dict(existing.payload_json or {})
    finally:
        session.close()


def _create_and_select_lats_round(
    diagnosis_id: str,
    raw_candidates: list[dict],
    *,
    source: str,
    round_index: int,
    execution_round_index: int | None = None,
    parent_hypothesis_id: str,
    generation_reason: str,
    default_tool: str,
    allowed_tools: list[str],
    phase: str,
    effect_prefix: str,
    preferred_candidate_key: str | None = None,
) -> tuple[DropInsightHypothesisModel | None, str | None, dict | None]:
    """Expand top-k siblings, merge the unvisited frontier and run PUCT.

    The merge is important: an observation can make an older sibling more
    attractive than a newly generated child.  That is the LATS backtracking
    step.  We still never replay a live side effect; the selected branch gets
    exactly one newly policy-checked real tool observation.
    """

    diagnosis = get_diagnosis(diagnosis_id)
    if diagnosis is None or getattr(diagnosis, "status", None) in {
        "COMPLETED",
        "INSUFFICIENT_EVIDENCE",
    }:
        return None, None, None
    config = LATSConfig.from_budget(diagnosis.budget_json or {})
    budget_session = new_session()
    try:
        tool_calls_used = (
            budget_session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.diagnosis_id == diagnosis_id)
            .count()
        )
        iterations_used = (
            budget_session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.event_type == "lats.node_selected",
            )
            .count()
        )
    finally:
        budget_session.close()
    if (
        tool_calls_used >= config.max_tool_calls
        or iterations_used >= config.max_iterations
    ):
        _record_lats_termination(
            diagnosis_id,
            reason="BUDGET_EXHAUSTED",
            detail="LATS 迭代或真实工具调用预算已经耗尽。",
            effect_key=f"{effect_prefix}:lats:budget-terminated",
        )
        return None, None, None
    prepared = prepare_candidates(
        [*raw_candidates, _UNKNOWN_HYPOTHESIS],
        top_k=config.top_k,
        default_tool=default_tool,
        value_lambda=config.value_lambda,
    )
    existing_hypotheses = list_hypotheses(diagnosis_id)
    known_candidate_keys = {
        stable_candidate_key(item.statement): item for item in existing_hypotheses
    }
    created: list[dict] = []
    for index, candidate in enumerate(prepared):
        # Round labels and OTHER/UNKNOWN spelling variants must not create a
        # second durable node for the same causal idea. Existing unvisited
        # nodes are merged into the global frontier below.
        if candidate["candidate_key"] in known_candidate_keys:
            continue
        effect_key = (
            f"{effect_prefix}:hypothesis"
            if index == 0
            else f"{effect_prefix}:hypothesis:{index}"
        )
        row = create_hypothesis(
            diagnosis_id,
            CreateHypothesisRequest(
                statement=candidate["statement"],
                expected_observations=candidate["expected_observations"],
                falsification_criteria=candidate["falsification_criteria"],
            ),
            source=(
                "SYSTEM_FALLBACK"
                if candidate.get("is_open_world_sentinel")
                else source
            ),
            round_index=round_index,
            parent_hypothesis_id=parent_hypothesis_id,
            generation_reason=candidate.get("reason") or generation_reason,
            effect_key=effect_key,
        )
        if row is not None:
            known_candidate_keys[candidate["candidate_key"]] = row
            created.append(
                {
                    **candidate,
                    "node_id": f"hypothesis:{row.id}",
                    "hypothesis_id": row.id,
                    "expansion_origin": "NEW_CHILD",
                }
            )

    # Merge every durable, still-unobserved sibling into this selection.  Its
    # prior/value comes from the earlier expansion event, so restart does not
    # alter the decision surface.
    session = new_session()
    try:
        hypothesis_rows = (
            session.query(DropInsightHypothesisModel)
            .filter(DropInsightHypothesisModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightHypothesisModel.created_at.asc())
            .all()
        )
        calls = (
            session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.diagnosis_id == diagnosis_id)
            .all()
        )
        events = (
            session.query(DropInsightEventModel)
            .filter(DropInsightEventModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightEventModel.sequence.asc())
            .all()
        )
    finally:
        session.close()
    attempted_ids = {row.hypothesis_id for row in calls if row.hypothesis_id}
    folded = replay_search_events(events)
    historical_candidates = folded.get("candidates") or {}
    created_ids = {row["hypothesis_id"] for row in created}
    existing_frontier: list[dict] = []
    for rank, row in enumerate(hypothesis_rows):
        if row.id in attempted_ids or row.id in created_ids or row.id == parent_hypothesis_id:
            continue
        if str(row.status or "OPEN").upper() in {
            "COUNTER", "REFUTED", "FALSIFIED", "DISPROVED", "REJECTED"
        }:
            continue
        node_id = f"hypothesis:{row.id}"
        prior = dict(historical_candidates.get(node_id) or {})
        # A durable sibling keeps the probe that was selected when the
        # hypothesis was created. If that probe has already run or is no
        # longer available, the sibling is not executable in this round. Do
        # not silently replace it with the round's fallback tool: that would
        # evaluate one evidence domain (for example I/O) with an unrelated
        # collector (for example py-spy) and turn strong evidence neutral.
        recommended_tool = str(prior.get("recommended_tool") or "").strip()
        if recommended_tool not in allowed_tools:
            continue
        existing_frontier.append(
            {
                **prior,
                "candidate_key": prior.get("candidate_key") or f"persisted:{row.id}",
                "statement": row.statement,
                "node_id": node_id,
                "hypothesis_id": row.id,
                "prior": float(prior.get("prior") or 0.05),
                "initial_value": float(prior.get("initial_value") or 0.2),
                "value_source": prior.get("value_source") or "PERSISTED_FALLBACK",
                "prior_source": prior.get("prior_source") or "PERSISTED_FALLBACK",
                "rank": int(prior.get("rank", config.top_k + rank)),
                "recommended_tool": recommended_tool,
                "expansion_origin": "EXISTING_UNVISITED_FRONTIER",
            }
        )
    # Canonical UCT gives every unvisited child the same +infinity bonus.  Use
    # durable creation order as the deterministic tie break, so an older
    # sibling is genuinely revisited before widening again.
    frontier = order_progressive_frontier(existing_frontier, created)
    if preferred_candidate_key:
        # Canonical UCT gives every unvisited node the same infinite
        # exploration term. When trusted counter evidence exposes a concrete
        # alternative, use that candidate as the deterministic tie break. The
        # branch still dispatches a fresh real probe; prior evidence is not
        # reused as support.
        preferred = [
            row for row in frontier
            if row.get("candidate_key") == preferred_candidate_key
        ]
        if preferred:
            frontier = preferred + [
                row for row in frontier
                if row.get("candidate_key") != preferred_candidate_key
            ]
            for rank, row in enumerate(frontier):
                row["rank"] = rank
    if not frontier:
        _record_lats_termination(
            diagnosis_id,
            reason="COVERAGE_EXHAUSTED",
            detail="候选原因与可观测证据域均已覆盖，没有尚未访问的安全分支。",
            effect_key=f"{effect_prefix}:lats:frontier-terminated",
        )
        return None, None, None
    prior_total = sum(max(0.0, float(row.get("prior") or 0.0)) for row in frontier)
    if prior_total <= 0:
        prior_total = float(len(frontier))
        for row in frontier:
            row["prior"] = 1.0
    for row in frontier:
        row["prior"] = round(float(row.get("prior") or 0.0) / prior_total, 6)

    selection = _record_lats_expansion_and_selection(
        diagnosis_id,
        frontier,
        phase=phase,
        round_index=execution_round_index or round_index,
        tree_depth=round_index,
        parent_hypothesis_id=parent_hypothesis_id,
        effect_prefix=f"{effect_prefix}:lats",
    )
    selected_id = str((selection or {}).get("node_id") or "").removeprefix(
        "hypothesis:"
    )
    selected = next((row for row in hypothesis_rows if row.id == selected_id), None)
    if selected is None:
        selected = next(
            (
                row for row in list_hypotheses(diagnosis_id)
                if row.id == selected_id
            ),
            None,
        )
    selected_candidate = next(
        (row for row in frontier if row.get("hypothesis_id") == selected_id),
        {},
    )
    selected_tool = selected_candidate.get("recommended_tool") or default_tool
    if selected_tool not in allowed_tools:
        selected_tool = default_tool if default_tool in allowed_tools else (
            allowed_tools[0] if allowed_tools else None
        )
    return selected, selected_tool, selection


def _latest_lats_reflection(
    diagnosis_id: str,
    hypothesis_id: str,
) -> dict | None:
    session = new_session()
    try:
        rows = (
            session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.event_type == "lats.reflection_recorded",
            )
            .order_by(DropInsightEventModel.sequence.desc())
            .all()
        )
        node_id = f"hypothesis:{hypothesis_id}"
        for row in rows:
            payload = row.payload_json or {}
            if payload.get("node_id") == node_id:
                return {
                    "decision": payload.get("decision"),
                    "summary": payload.get("summary"),
                    "reflection_is_evidence": False,
                }
        return None
    finally:
        session.close()


def _record_lats_action_dispatched(
    diagnosis_id: str,
    hypothesis_id: str,
    tool_call: DropInsightToolCallModel,
    *,
    effect_prefix: str,
) -> None:
    """Persist proposal/approval separately from a genuinely dispatched action."""

    session = new_session()
    try:
        diagnosis = _lock_diagnosis(session, diagnosis_id)
        if diagnosis is None:
            return
        if not _lats_search_started(session, diagnosis_id):
            return
        semantics = execution_semantics(diagnosis.mode)
        timestamp = now_utc()
        durable_prefix = f"tool_call:{tool_call.id}:lats"
        common = {
            "node_id": f"hypothesis:{hypothesis_id}",
            "tool_call_id": tool_call.id,
            "task_id": tool_call.task_id,
            "tool_name": tool_call.tool_name,
            "policy_decision": tool_call.policy_decision,
            "rollout_semantics": semantics["rollout_semantics"],
            "equivalent_sibling_rollback": semantics[
                "strict_environment_reversibility"
            ],
        }
        _append_event(
            session,
            diagnosis_id,
            "lats.action_proposed",
            "AGENT",
            {
                **common,
                "status": tool_call.status,
                "proposal_only": not bool(tool_call.task_id),
            },
            timestamp,
            effect_key=f"{durable_prefix}:proposed",
        )
        if tool_call.status == "PENDING_APPROVAL":
            _append_event(
                session,
                diagnosis_id,
                "lats.awaiting_approval",
                "POLICY",
                {**common, "status": tool_call.status},
                timestamp,
                effect_key=f"{durable_prefix}:awaiting-approval",
            )
            session.commit()
            return
        if tool_call.status in {"DENIED", "REJECTED"}:
            _append_event(
                session,
                diagnosis_id,
                "lats.action_blocked",
                "POLICY",
                {**common, "status": tool_call.status},
                timestamp,
                effect_key=f"{durable_prefix}:blocked",
            )
            rows = (
                session.query(DropInsightHypothesisModel)
                .filter(DropInsightHypothesisModel.diagnosis_id == diagnosis_id)
                .all()
            )
            parent_map = {row.id: row.parent_hypothesis_id for row in rows}
            path = hypothesis_path(hypothesis_id, parent_map)
            outcome = reward_from_outcome(
                verification_status=None,
                confidence=0,
                support_count=0,
                counter_count=0,
                tool_status=tool_call.status,
            )
            reflection = reflection_from_outcome(outcome)
            _append_event(
                session,
                diagnosis_id,
                "lats.observation_recorded",
                "POLICY",
                {
                    **common,
                    "observation_id": f"policy:{tool_call.id}",
                    "observation_source": "POLICY_GATE",
                    "summary": "当前动作被策略或人工门禁拒绝，未执行真实采集。",
                    "evidence_ids": [],
                    "external_observation": False,
                    "failure_is_counter_evidence": False,
                },
                timestamp,
                effect_key=f"{durable_prefix}:policy-observation",
            )
            _append_event(
                session,
                diagnosis_id,
                "lats.reflection_recorded",
                "AGENT",
                {
                    "node_id": common["node_id"],
                    "tool_call_id": tool_call.id,
                    "decision": reflection["decision"],
                    "summary": reflection["summary"],
                    "grounded_in_observation": f"policy:{tool_call.id}",
                    "reflection_is_evidence": False,
                },
                timestamp,
                effect_key=f"{durable_prefix}:policy-reflection",
            )
            _append_event(
                session,
                diagnosis_id,
                "lats.backpropagated",
                "SYSTEM",
                {
                    "node_id": common["node_id"],
                    "tool_call_id": tool_call.id,
                    "path_node_ids": path,
                    **outcome,
                },
                timestamp,
                effect_key=f"{durable_prefix}:policy-backpropagation",
            )
            session.commit()
            try:
                _replan_after_insufficient_evidence(
                    diagnosis_id,
                    hypothesis_id,
                    f"policy_{tool_call.id}",
                    continuation_reason=(
                        "上一动作被策略或人工门禁拒绝，需切换到允许的证据域"
                    ),
                )
            except Exception:
                # The durable gate outcome is already committed. A transient
                # replanning failure must not roll back the user's decision.
                logger.exception(
                    "policy-blocked LATS replanning failed",
                    extra={"diagnosis_id": diagnosis_id, "tool_call_id": tool_call.id},
                )
            return
        if not tool_call.task_id or tool_call.status not in {
            "TASK_CREATED", "RUNNING", "COMPLETED"
        }:
            session.commit()
            return
        _append_event(
            session,
            diagnosis_id,
            "lats.simulation_started",
            "SYSTEM",
            {
                **common,
                "simulation_kind": "REAL_TOOL_ENVIRONMENT_STEP",
                "note": (
                    "线上分支只执行一次真实工具动作；未选择兄弟没有观测、"
                    "没有奖励，也不宣称可回滚到等价环境。"
                ),
            },
            timestamp,
            effect_key=f"{durable_prefix}:simulation",
        )
        _append_event(
            session,
            diagnosis_id,
            "lats.action_dispatched",
            "SYSTEM",
            common,
            timestamp,
            effect_key=f"{durable_prefix}:action",
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        if not _event_effect_exists(
            diagnosis_id, f"tool_call:{tool_call.id}:lats:proposed"
        ):
            raise
    finally:
        session.close()


def _record_lats_tool_failure(
    diagnosis_id: str,
    hypothesis_id: str,
    tool_call_id: str,
    task_id: str,
    status: str,
    reason: str | None,
) -> None:
    """Treat collector failure as an environment observation, never counter-evidence."""

    session = new_session()
    try:
        diagnosis = _lock_diagnosis(session, diagnosis_id)
        if diagnosis is None:
            return
        if not _lats_search_started(session, diagnosis_id):
            return
        rows = (
            session.query(DropInsightHypothesisModel)
            .filter(DropInsightHypothesisModel.diagnosis_id == diagnosis_id)
            .all()
        )
        parent_map = {row.id: row.parent_hypothesis_id for row in rows}
        path = hypothesis_path(hypothesis_id, parent_map)
        node_id = f"hypothesis:{hypothesis_id}"
        outcome = reward_from_outcome(
            verification_status=None,
            confidence=0.0,
            support_count=0,
            counter_count=0,
            tool_status=status,
        )
        reflection = reflection_from_outcome(outcome)
        timestamp = now_utc()
        _append_event(
            session,
            diagnosis_id,
            "lats.observation_recorded",
            "ENVIRONMENT",
            {
                "node_id": node_id,
                "observation_id": f"tool_call:{tool_call_id}:failure",
                "observation_source": "REAL_TOOL_TERMINAL_STATUS",
                "summary": f"真实探针状态={status}；{reason or '未返回可用产物'}",
                "tool_call_ids": [tool_call_id],
                "task_ids": [task_id],
                "evidence_ids": [],
                "external_observation": True,
                "cached_observation": True,
                "failure_is_counter_evidence": False,
                "rollout_semantics": execution_semantics(diagnosis.mode)[
                    "rollout_semantics"
                ],
            },
            timestamp,
            effect_key=f"tool_call:{tool_call_id}:lats:observation",
        )
        _append_event(
            session,
            diagnosis_id,
            "lats.reflection_recorded",
            "AGENT",
            {
                "node_id": node_id,
                "decision": reflection["decision"],
                "summary": reflection["summary"],
                "grounded_in_observation": f"tool_call:{tool_call_id}:failure",
                "reflection_is_evidence": False,
            },
            timestamp,
            effect_key=f"tool_call:{tool_call_id}:lats:reflection",
        )
        _append_event(
            session,
            diagnosis_id,
            "lats.backpropagated",
            "SYSTEM",
            {
                "node_id": node_id,
                "tool_call_id": tool_call_id,
                "path_node_ids": path,
                **outcome,
            },
            timestamp,
            effect_key=f"tool_call:{tool_call_id}:lats:backpropagation",
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        if not _event_effect_exists(
            diagnosis_id, f"tool_call:{tool_call_id}:lats:backpropagation"
        ):
            raise
    finally:
        session.close()


def _record_lats_report_outcome(report_id: str) -> dict | None:
    """Persist real observation, reflection and reward backpropagation once."""

    session = new_session()
    diagnosis_for_error = ""
    try:
        report = session.get(DropInsightReportModel, report_id)
        if report is None or not report.hypothesis_id:
            return None
        diagnosis = _lock_diagnosis(session, report.diagnosis_id)
        diagnosis_for_error = report.diagnosis_id
        if diagnosis is None:
            return None
        if not _lats_search_started(session, report.diagnosis_id):
            return None
        hypothesis = session.get(DropInsightHypothesisModel, report.hypothesis_id)
        if hypothesis is None:
            return None
        evidence_rows = (
            session.query(DropInsightEvidenceModel)
            .filter(
                DropInsightEvidenceModel.diagnosis_id == report.diagnosis_id,
                DropInsightEvidenceModel.hypothesis_id == report.hypothesis_id,
            )
            .all()
        )
        calls = (
            session.query(DropInsightToolCallModel)
            .filter(
                DropInsightToolCallModel.diagnosis_id == report.diagnosis_id,
                DropInsightToolCallModel.hypothesis_id == report.hypothesis_id,
            )
            .all()
        )
        support_count = len(report.evidence_refs_json or [])
        counter_count = len(report.counter_evidence_refs_json or [])
        rejected_count = sum(
            str((row.classification_json or {}).get("decision") or "").upper().startswith("REJECT")
            for row in evidence_rows
        )
        verification_status = (report.verification_json or {}).get("status")
        outcome = reward_from_outcome(
            verification_status=verification_status,
            confidence=report.confidence,
            support_count=support_count,
            counter_count=counter_count,
            rejected_count=rejected_count,
            tool_status=(calls[-1].status if calls else None),
        )
        reflection = reflection_from_outcome(outcome)
        round_contract = _diagnosis_round_contract(session, diagnosis)
        verified_before_minimum = bool(
            verification_status == "VERIFIED"
            and support_count > 0
            and report.confidence >= 600
            and not round_contract["satisfied"]
        )
        if verified_before_minimum:
            reflection = {
                "decision": "EXPAND_FOR_CROSS_VALIDATION",
                "summary": (
                    "当前分支已有可信支持，但受控场景至少需要 "
                    f"{round_contract['minimum']} 轮真实诊断；当前完成 "
                    f"{round_contract['observed']} 轮，继续跨证据域交叉验证。"
                ),
            }
        parent_rows = (
            session.query(DropInsightHypothesisModel)
            .filter(DropInsightHypothesisModel.diagnosis_id == report.diagnosis_id)
            .all()
        )
        parent_map = {row.id: row.parent_hypothesis_id for row in parent_rows}
        path = hypothesis_path(report.hypothesis_id, parent_map)
        node_id = f"hypothesis:{report.hypothesis_id}"
        semantics = execution_semantics(diagnosis.mode)
        timestamp = now_utc()
        _append_event(
            session,
            report.diagnosis_id,
            "lats.observation_recorded",
            "ENVIRONMENT",
            {
                "node_id": node_id,
                "observation_id": f"report:{report.id}",
                "observation_source": "REAL_TOOL_EVIDENCE_GATE",
                "summary": (
                    f"真实采集完成：支持 {support_count} 条、反证 {counter_count} 条、"
                    f"质量拒绝 {rejected_count} 条；门禁={verification_status or 'UNKNOWN'}。"
                ),
                "tool_call_ids": [row.id for row in calls],
                "task_ids": [row.task_id for row in calls if row.task_id],
                "evidence_ids": [row.id for row in evidence_rows],
                "report_id": report.id,
                "external_observation": True,
                "cached_observation": True,
                "rollout_semantics": semantics["rollout_semantics"],
                "equivalent_sibling_rollback": semantics[
                    "strict_environment_reversibility"
                ],
            },
            timestamp,
            effect_key=f"report:{report.id}:lats:observation",
        )
        _append_event(
            session,
            report.diagnosis_id,
            "lats.reflection_recorded",
            "AGENT",
            {
                "node_id": node_id,
                "report_id": report.id,
                "decision": reflection["decision"],
                "summary": reflection["summary"],
                "grounded_in_observation": f"report:{report.id}",
                "reflection_is_evidence": False,
            },
            timestamp,
            effect_key=f"report:{report.id}:lats:reflection",
        )
        _append_event(
            session,
            report.diagnosis_id,
            "lats.backpropagated",
            "SYSTEM",
            {
                "node_id": node_id,
                "report_id": report.id,
                "path_node_ids": path,
                **outcome,
            },
            timestamp,
            effect_key=f"report:{report.id}:lats:backpropagation",
        )
        if outcome["outcome"] == "FALSIFIED":
            _append_event(
                session,
                report.diagnosis_id,
                "lats.node_pruned",
                "SYSTEM",
                {
                    "node_id": node_id,
                    "report_id": report.id,
                    "reason": "可信反证推翻当前假设；采集失败不触发剪枝。",
                },
                timestamp,
                effect_key=f"report:{report.id}:lats:pruned",
            )
        if (
            verification_status == "VERIFIED"
            and support_count > 0
            and report.confidence >= 600
            and round_contract["satisfied"]
        ):
            _append_event(
                session,
                report.diagnosis_id,
                "lats.search_terminated",
                "SYSTEM",
                {
                    "reason": "VERIFIED",
                    "detail": reflection["summary"],
                    "report_id": report.id,
                    "confidence": report.confidence / 1000,
                    "best_path_node_ids": path,
                },
                timestamp,
                effect_key=f"report:{report.id}:lats:terminated",
            )
        session.commit()
        return {**outcome, "reflection": reflection, "path_node_ids": path}
    except IntegrityError:
        session.rollback()
        if not _event_effect_exists(
            diagnosis_for_error,
            f"report:{report_id}:lats:backpropagation",
        ):
            raise
        return None
    finally:
        session.close()


def _best_accepted_supported_report(session, diagnosis_id: str):
    candidates = (
        session.query(DropInsightReportModel)
        .filter(
            DropInsightReportModel.diagnosis_id == diagnosis_id,
            DropInsightReportModel.confidence >= 600,
        )
        .all()
    )

    def rank(report) -> tuple:
        verification = report.verification_json or {}
        status = verification.get("status")
        if status not in {"VERIFIED", "PARTIAL_WITHOUT_COUNTER"}:
            return (-1, -1, -1.0, "", "")
        return (
            1 if status == "VERIFIED" else 0,
            int(report.confidence or 0),
            float(verification.get("coverage_ratio") or 0.0),
            report.created_at.isoformat() if report.created_at else "",
            report.id,
        )

    accepted = [
        report
        for report in candidates
        if (report.evidence_refs_json or []) and rank(report)[0] >= 0
    ]
    return max(accepted, key=rank, default=None)


def _finalize_diagnosis_in_session(
    session,
    diagnosis,
    *,
    reason: str,
    detail: str,
    effect_key: str,
) -> dict | None:
    """Stage one truthful terminal state in the caller's locked transaction.

    A failed leaf must not overwrite an earlier well-supported branch.  When
    LATS has no safe work left, the best accepted support report becomes the
    final answer with its recorded limitations; otherwise the whole diagnosis
    is finally marked as insufficient.  Active calls guard against a stale
    retry terminating a search that is still collecting evidence.
    """
    diagnosis_id = diagnosis.id
    if diagnosis.status in {"COMPLETED", "INSUFFICIENT_EVIDENCE"}:
        return {
            "finalized": False,
            "status": diagnosis.status,
            "reason": "ALREADY_TERMINAL",
        }
    active_calls = (
        session.query(DropInsightToolCallModel.id)
        .filter(
            DropInsightToolCallModel.diagnosis_id == diagnosis_id,
            DropInsightToolCallModel.status.in_({
                "PENDING_APPROVAL",
                "APPROVED",
                "TASK_CREATED",
                "RUNNING",
            }),
        )
        .count()
    )
    if active_calls:
        return {
            "finalized": False,
            "status": diagnosis.status,
            "reason": "ACTIVE_TOOL_CALLS",
        }

    best_report = _best_accepted_supported_report(session, diagnosis_id)
    round_contract = _diagnosis_round_contract(session, diagnosis)
    best_report_before_round_gate = best_report
    if not round_contract["satisfied"]:
        # A server-owned demo may require several independent observations.
        # Search exhaustion before that gate is an honest insufficient result,
        # never a successful diagnosis based on the first plausible profile.
        best_report = None
    timestamp = now_utc()
    finalization_digest = hashlib.sha256(effect_key.encode("utf-8")).hexdigest()[:32]
    finalization_effect_key = f"lats-finalize:{finalization_digest}"

    if best_report is not None:
        # COMPLETED is intentionally reachable only from evidence collection.
        # Both state changes stay in this uncommitted transaction, so clients
        # observe exactly one terminal state.
        if diagnosis.status != "COLLECTING_EVIDENCE":
            if diagnosis.status not in {"PLANNING", "HYPOTHESIZING"}:
                return {
                    "finalized": False,
                    "status": diagnosis.status,
                    "reason": "NON_SEARCH_STATE",
                }
            _cas_session_update(
                session,
                diagnosis,
                status="COLLECTING_EVIDENCE",
                timestamp=timestamp,
            )
        _cas_session_update(
            session,
            diagnosis,
            status="COMPLETED",
            timestamp=timestamp,
        )
        verification = best_report.verification_json or {}
        _append_event(
            session,
            diagnosis_id,
            "diagnosis.completed_from_supported_report",
            "SYSTEM",
            {
                "report_id": best_report.id,
                "confidence": best_report.confidence / 1000,
                "verification_status": verification.get("status"),
                "coverage_ratio": verification.get("coverage_ratio"),
                "termination_reason": reason,
                "termination_detail": detail,
                "completed_with_limitations": (
                    verification.get("status") != "VERIFIED"
                ),
                "limitations_preserved": True,
            },
            timestamp,
            effect_key=finalization_effect_key,
        )
        final_status = "COMPLETED"
    else:
        if diagnosis.status not in {
            "PLANNING",
            "HYPOTHESIZING",
            "COLLECTING_EVIDENCE",
        }:
            return {
                "finalized": False,
                "status": diagnosis.status,
                "reason": "NON_SEARCH_STATE",
            }
        _cas_session_update(
            session,
            diagnosis,
            status="INSUFFICIENT_EVIDENCE",
            timestamp=timestamp,
        )
        _append_event(
            session,
            diagnosis_id,
            "diagnosis.insufficient_evidence_finalized",
            "SYSTEM",
            {
                "termination_reason": reason,
                "termination_detail": detail,
                "accepted_support_report": False,
                "best_supported_report_id": (
                    best_report_before_round_gate.id
                    if best_report_before_round_gate is not None
                    else None
                ),
                "minimum_diagnosis_rounds": round_contract["minimum"],
                "observed_diagnosis_rounds": round_contract["observed"],
                "minimum_rounds_satisfied": round_contract["satisfied"],
            },
            timestamp,
            effect_key=finalization_effect_key,
        )
        final_status = "INSUFFICIENT_EVIDENCE"

    return {
        "finalized": True,
        "status": final_status,
        "best_report_id": best_report.id if best_report is not None else None,
    }


def _record_lats_termination(
    diagnosis_id: str,
    *,
    reason: str,
    detail: str,
    effect_key: str,
) -> None:
    session = new_session()
    try:
        diagnosis = _lock_diagnosis(session, diagnosis_id)
        if diagnosis is None:
            return
        finalization = _finalize_diagnosis_in_session(
            session,
            diagnosis,
            reason=reason,
            detail=detail,
            effect_key=effect_key,
        )
        if finalization and finalization.get("finalized") and _lats_search_started(
            session, diagnosis_id
        ):
            _append_event(
                session,
                diagnosis_id,
                "lats.search_terminated",
                "SYSTEM",
                {
                    "reason": reason,
                    "detail": detail,
                    "final_status": finalization.get("status"),
                    "best_report_id": finalization.get("best_report_id"),
                },
                now_utc(),
                effect_key=effect_key,
            )
        session.commit()
    except IntegrityError:
        session.rollback()
        persisted = get_diagnosis(diagnosis_id)
        if persisted is None or persisted.status not in {
            "COMPLETED",
            "INSUFFICIENT_EVIDENCE",
        }:
            raise
    finally:
        session.close()


def _lats_search_started(session, diagnosis_id: str) -> bool:
    return (
        session.query(DropInsightEventModel.id)
        .filter(
            DropInsightEventModel.diagnosis_id == diagnosis_id,
            DropInsightEventModel.event_type == "lats.search_started",
        )
        .first()
        is not None
    )


def _record_successful_route(diagnosis_id: str, report_id: str) -> None:
    effect_key = f"report:{report_id}:route:event"
    session = new_session()
    try:
        diagnosis = _lock_diagnosis(session, diagnosis_id)
        if diagnosis is None:
            return
        calls = (
            session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightToolCallModel.created_at.asc())
            .all()
        )
        route = [item.tool_name for item in calls if item.status == "COMPLETED"]
        if not route:
            return
        _append_event(
            session,
            diagnosis_id,
            "diagnosis.route_learned",
            "SYSTEM",
            {
                "report_id": report_id,
                "tool_route": route,
                "learning_scope": "verified_route_prior",
                "note": "仅提升后续路线排序，不自动新增工具或绕过策略。",
            },
            now_utc(),
            effect_key=effect_key,
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        if not _event_effect_exists(diagnosis_id, effect_key):
            raise
    finally:
        session.close()


def preview_tool_call(
    diagnosis_id: str,
    payload: PreviewToolCallRequest,
) -> dict | None:
    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if diagnosis is None:
            return None
        try:
            binding = _validated_target_binding(session, diagnosis)
            binding_authoritative = True
        except ValueError:
            binding = None
            binding_authoritative = False
        allowed_agent_ids = frozenset({binding.agent_id}) if binding else frozenset()
        agent_id = payload.arguments.get("agent_id")
        agent = session.get(AgentModel, agent_id) if isinstance(agent_id, str) else None
        capabilities = frozenset(agent.capabilities or []) if agent is not None else frozenset()
        budget = diagnosis.budget_json or {}
        used_tool_calls = (
            session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.diagnosis_id == diagnosis_id)
            .count()
        )
        context = PolicyContext(
            allowed_agent_ids=allowed_agent_ids,
            agent_capabilities=capabilities,
            max_risk_level=budget.get("max_risk_level", "R0"),
            used_tool_calls=used_tool_calls,
            max_tool_calls=budget.get("max_tool_calls", 12),
            allowed_pid=binding.pid if binding else None,
            binding_authoritative=binding_authoritative,
            session_pre_authorized=diagnosis.mode == "AUTONOMOUS",
        )
        if not _runtime_tool_is_compatible(diagnosis, payload.tool_name):
            decision = {
                "decision": "DENY",
                "checks": [{"name": "RUNTIME_COMPATIBILITY", "result": "FAIL"}],
                "reason": "所选运行时探针与服务端绑定的目标进程类型不兼容",
            }
        else:
            decision = evaluate_tool_call(payload.tool_name, payload.arguments, context)
        _append_event(
            session,
            diagnosis_id,
            "tool_call.policy_evaluated",
            "POLICY",
            {
                "tool_name": payload.tool_name,
                "decision": decision["decision"],
                "checks": decision["checks"],
            },
            now_utc(),
        )
        session.commit()
        return decision
    finally:
        session.close()


def request_tool_call(
    diagnosis_id: str,
    payload: CreateToolCallRequest,
    *,
    requested_by: str = "system:internal",
    effect_key: str | None = None,
) -> DropInsightToolCallModel | None:
    session = new_session()
    model = None
    try:
        if effect_key:
            existing = (
                session.query(DropInsightToolCallModel)
                .filter(
                    DropInsightToolCallModel.diagnosis_id == diagnosis_id,
                    DropInsightToolCallModel.effect_key == effect_key,
                )
                .first()
            )
            if existing is not None:
                model = existing
            else:
                diagnosis = _lock_diagnosis(
                    session, diagnosis_id, payload.expected_version
                )
                if diagnosis is None:
                    return None
                existing = (
                    session.query(DropInsightToolCallModel)
                    .filter(
                        DropInsightToolCallModel.diagnosis_id == diagnosis_id,
                        DropInsightToolCallModel.effect_key == effect_key,
                    )
                    .first()
                )
                if existing is not None:
                    model = existing
        else:
            diagnosis = _lock_diagnosis(
                session, diagnosis_id, payload.expected_version
            )
            if diagnosis is None:
                return None

        if model is None:
            if payload.hypothesis_id:
                hypothesis = session.get(
                    DropInsightHypothesisModel,
                    payload.hypothesis_id,
                )
                if hypothesis is None or hypothesis.diagnosis_id != diagnosis_id:
                    raise ValueError("hypothesis does not belong to diagnosis")

            decision = _evaluate_persisted_tool_policy(
                session,
                diagnosis,
                payload.tool_name,
                payload.arguments,
            )
            reservation = decision.pop("reservation", None)
            status_by_decision = {
                "DENY": "DENIED",
                "REQUIRE_APPROVAL": "PENDING_APPROVAL",
                "ALLOW": "APPROVED",
            }
            timestamp = now_utc()
            model = DropInsightToolCallModel(
                id=f"toolcall_{uuid4().hex}",
                diagnosis_id=diagnosis_id,
                hypothesis_id=payload.hypothesis_id,
                tool_name=payload.tool_name,
                arguments_json=payload.arguments,
                policy_decision=decision["decision"],
                policy_checks_json=decision["checks"],
                policy_reason=decision["reason"],
                status=status_by_decision[decision["decision"]],
                budget_reservation_json=reservation or {},
                budget_reservation_status="RESERVED" if reservation else "NONE",
                effect_key=effect_key,
                requested_by=requested_by,
                created_at=timestamp,
                decided_at=timestamp if decision["decision"] == "DENY" else None,
            )
            session.add(model)
            _append_event(
                session,
                diagnosis_id,
                "tool_call.requested",
                "PLANNER",
                {
                    "tool_call_id": model.id,
                    "tool_name": model.tool_name,
                    "policy_decision": model.policy_decision,
                    "status": model.status,
                },
                timestamp,
            )
            _cas_session_update(
                session,
                diagnosis,
                status=(
                    "PLANNING"
                    if diagnosis.status == "UNDERSTANDING"
                    else diagnosis.status
                ),
                timestamp=timestamp,
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                if not effect_key:
                    raise
                model = (
                    session.query(DropInsightToolCallModel)
                    .filter(
                        DropInsightToolCallModel.diagnosis_id == diagnosis_id,
                        DropInsightToolCallModel.effect_key == effect_key,
                    )
                    .first()
                )
                if model is None:
                    raise
            else:
                session.refresh(model)
    finally:
        session.close()

    if model is not None and model.status == "APPROVED":
        return _execute_approved_tool_call(model.id)
    return model


def decide_tool_call(
    diagnosis_id: str,
    tool_call_id: str,
    payload: DecideToolCallRequest,
    *,
    decided_by: str = "system:internal",
) -> DropInsightToolCallModel | None:
    session = new_session()
    try:
        model = session.get(DropInsightToolCallModel, tool_call_id)
        if model is None or model.diagnosis_id != diagnosis_id:
            return None
        if model.status != "PENDING_APPROVAL":
            raise ValueError(f"tool call is not awaiting approval: {model.status}")
        timestamp = now_utc()
        if payload.approved:
            diagnosis = (
                session.query(DropInsightSessionModel)
                .filter(DropInsightSessionModel.id == diagnosis_id)
                .with_for_update()
                .first()
            )
            if diagnosis is None:
                return None
            binding = _validated_target_binding(session, diagnosis, now=timestamp)
            arguments = model.arguments_json or {}
            if arguments.get("agent_id") != binding.agent_id:
                raise ValueError("tool call Agent disagrees with process binding")
            if arguments.get("pid") is not None and arguments.get("pid") != binding.pid:
                raise ValueError("tool call PID disagrees with process binding")
        model.approved_by = decided_by
        model.approval_reason = payload.reason
        model.decided_at = timestamp
        model.status = "APPROVED" if payload.approved else "REJECTED"
        if not payload.approved:
            _release_budget_reservation(model, timestamp=timestamp, reason="approval_rejected")
        _append_event(
            session,
            diagnosis_id,
            "tool_call.approval_decided",
            "USER",
            {
                "tool_call_id": model.id,
                "approved": payload.approved,
                "decided_by": decided_by,
            },
            timestamp,
        )
        session.commit()
        session.refresh(model)
    finally:
        session.close()

    if payload.approved:
        executed = _execute_approved_tool_call(tool_call_id)
        if executed is not None and executed.hypothesis_id:
            _record_lats_action_dispatched(
                diagnosis_id,
                executed.hypothesis_id,
                executed,
                effect_prefix=f"tool_call:{executed.id}:lats",
            )
        return executed
    if model is not None and model.hypothesis_id:
        _record_lats_action_dispatched(
            diagnosis_id,
            model.hypothesis_id,
            model,
            effect_prefix=f"tool_call:{model.id}:lats",
        )
    return model


def update_tool_call_arguments(
    diagnosis_id: str,
    tool_call_id: str,
    *,
    arguments: dict,
    updated_by: str = "user",
) -> DropInsightToolCallModel | None:
    """修改待审批工具调用的参数；修改后重新做策略与预算校验。

    仅 ``PENDING_APPROVAL`` 状态可修改。若新参数被策略 DENY，则标记为
    DENIED 并释放预算预留；否则保持待审批，等待用户显式批准。
    """
    session = new_session()
    try:
        model = session.get(DropInsightToolCallModel, tool_call_id)
        if model is None or model.diagnosis_id != diagnosis_id:
            return None
        if model.status != "PENDING_APPROVAL":
            raise ValueError(
                f"只有待审批的工具调用可以修改参数（当前状态 {model.status}）"
            )
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if diagnosis is None:
            return None
        timestamp = now_utc()
        model.arguments_json = arguments
        decision = _evaluate_persisted_tool_policy(
            session, diagnosis, model.tool_name, arguments
        )
        reservation = decision.pop("reservation", None)
        model.policy_decision = decision["decision"]
        model.policy_checks_json = decision["checks"]
        model.policy_reason = decision["reason"]
        if decision["decision"] == "DENY":
            model.status = "DENIED"
            _release_budget_reservation(
                model, timestamp=timestamp, reason="arguments_rejected"
            )
        else:
            model.budget_reservation_json = reservation or {}
            model.budget_reservation_status = "RESERVED" if reservation else "NONE"
        _append_event(
            session,
            diagnosis_id,
            "tool_call.arguments_updated",
            updated_by,
            {
                "tool_call_id": model.id,
                "tool_name": model.tool_name,
                "policy_decision": model.policy_decision,
            },
            timestamp,
        )
        session.commit()
        session.refresh(model)
        return model
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def list_tool_calls(diagnosis_id: str) -> list[DropInsightToolCallModel]:
    session = new_session()
    try:
        return (
            session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightToolCallModel.created_at.asc())
            .all()
        )
    finally:
        session.close()


def maintain_drop_insight_sessions(
    *,
    timestamp: datetime | None = None,
    approval_timeout_sec: int | None = None,
    limit: int = 100,
) -> dict[str, list[str]]:
    """Finalize supported diagnoses and expire abandoned approval cards."""

    now = timestamp or now_utc()
    configured_timeout = approval_timeout_sec
    if configured_timeout is None:
        try:
            configured_timeout = int(
                os.getenv("MINI_DROP_APPROVAL_TIMEOUT_SEC", "1800")
            )
        except ValueError:
            configured_timeout = 1800
    timeout_sec = min(max(int(configured_timeout), 60), 86_400)
    bounded_limit = max(1, min(int(limit), 1000))
    completed: list[str] = []
    expired: list[str] = []

    session = new_session()
    try:
        # Only a fully VERIFIED report is terminal while search effects are
        # still running. PARTIAL_WITHOUT_COUNTER must continue to another
        # evidence domain; a search-exhaustion finalizer may later complete the
        # session with that best partial report and its explicit limitations.
        candidate_reports = (
            session.query(DropInsightReportModel)
            .filter(DropInsightReportModel.confidence >= 600)
            .order_by(DropInsightReportModel.created_at.desc())
            .limit(bounded_limit * 3)
            .all()
        )
        seen: set[str] = set()
        for report in candidate_reports:
            if report.diagnosis_id in seen or not (report.evidence_refs_json or []):
                continue
            if (report.verification_json or {}).get("status") != "VERIFIED":
                continue
            seen.add(report.diagnosis_id)
            diagnosis = _lock_diagnosis(session, report.diagnosis_id)
            if diagnosis is None or diagnosis.status != "COLLECTING_EVIDENCE":
                continue
            round_contract = _diagnosis_round_contract(session, diagnosis)
            if not round_contract["satisfied"]:
                continue
            _cas_session_update(session, diagnosis, status="COMPLETED", timestamp=now)
            _append_event(
                session,
                diagnosis.id,
                "diagnosis.completed_from_supported_report",
                "SYSTEM",
                {
                    "report_id": report.id,
                    "confidence": report.confidence / 1000,
                    "verification_status": (report.verification_json or {}).get("status"),
                },
                now,
            )
            completed.append(diagnosis.id)

        cutoff = now - timedelta(seconds=timeout_sec)
        pending = (
            session.query(DropInsightToolCallModel)
            .filter(
                DropInsightToolCallModel.status == "PENDING_APPROVAL",
                DropInsightToolCallModel.created_at <= cutoff,
            )
            .order_by(DropInsightToolCallModel.created_at.asc())
            .limit(bounded_limit)
            .with_for_update()
            .all()
        )
        for tool_call in pending:
            tool_call.status = "REJECTED"
            tool_call.approved_by = "system:maintenance"
            tool_call.approval_reason = "审批窗口已过期，系统自动释放资源预留"
            tool_call.decided_at = now
            tool_call.result_json = {
                "reason": "approval_expired",
                "timeout_seconds": timeout_sec,
            }
            _release_budget_reservation(
                tool_call,
                timestamp=now,
                reason="approval_expired",
            )
            _append_event(
                session,
                tool_call.diagnosis_id,
                "tool_call.approval_expired",
                "SYSTEM",
                {
                    "tool_call_id": tool_call.id,
                    "timeout_seconds": timeout_sec,
                },
                now,
            )
            diagnosis = _lock_diagnosis(session, tool_call.diagnosis_id)
            if diagnosis is not None and diagnosis.status != "COMPLETED":
                active_count = (
                    session.query(DropInsightToolCallModel)
                    .filter(
                        DropInsightToolCallModel.diagnosis_id == diagnosis.id,
                        DropInsightToolCallModel.id != tool_call.id,
                        DropInsightToolCallModel.status.in_({
                            "PENDING_APPROVAL", "APPROVED", "TASK_CREATED", "RUNNING"
                        }),
                    )
                    .count()
                )
                if active_count == 0 and diagnosis.status in {
                    "PLANNING", "HYPOTHESIZING", "COLLECTING_EVIDENCE"
                }:
                    termination_key = (
                        f"tool_call:{tool_call.id}:approval-expired-finalization"
                    )
                    finalization = _finalize_diagnosis_in_session(
                        session,
                        diagnosis,
                        reason="APPROVAL_EXPIRED",
                        detail="最后一个待审批动作已超时，当前没有继续运行的取证任务。",
                        effect_key=termination_key,
                    )
                    if finalization and finalization.get("finalized"):
                        if _lats_search_started(session, diagnosis.id):
                            _append_event(
                                session,
                                diagnosis.id,
                                "lats.search_terminated",
                                "SYSTEM",
                                {
                                    "reason": "APPROVAL_EXPIRED",
                                    "detail": (
                                        "最后一个待审批动作已超时，"
                                        "当前没有继续运行的取证任务。"
                                    ),
                                    "final_status": finalization.get("status"),
                                    "best_report_id": finalization.get(
                                        "best_report_id"
                                    ),
                                },
                                now,
                                effect_key=f"{termination_key}:lats-terminated",
                            )
                        if (
                            finalization.get("status") == "COMPLETED"
                            and diagnosis.id not in completed
                        ):
                            completed.append(diagnosis.id)
            expired.append(tool_call.id)
        session.commit()
        return {"completed_diagnoses": completed, "expired_approvals": expired}
    finally:
        session.close()


def get_budget_usage(diagnosis_id: str) -> dict | None:
    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if diagnosis is None:
            return None
        calls = (
            session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.diagnosis_id == diagnosis_id)
            .all()
        )
        task_ids = [item.task_id for item in calls if item.task_id]
        tasks = (
            session.query(TaskModel).filter(TaskModel.id.in_(task_ids)).all()
            if task_ids else []
        )
        artifact_bytes = 0
        if task_ids:
            artifact_bytes = sum(
                item.size_bytes or 0
                for item in session.query(ArtifactModel)
                .filter(ArtifactModel.task_id.in_(task_ids))
                .all()
            )
        limits = diagnosis.budget_json or {}
        used = {
            "tool_calls": len(calls),
            "task_duration_seconds": sum(item.duration_sec or 0 for item in tasks),
            "artifact_bytes": artifact_bytes,
            "hosts": len(
                {
                    (item.arguments_json or {}).get("agent_id")
                    for item in calls
                    if (item.arguments_json or {}).get("agent_id")
                }
            ),
        }
        reserved_calls = [
            item for item in calls
            if item.budget_reservation_status == "RESERVED"
        ]
        reserved = {
            "duration_seconds": sum(
                int((item.budget_reservation_json or {}).get("duration_seconds") or 0)
                for item in reserved_calls
            ),
            "artifact_bytes": sum(
                int((item.budget_reservation_json or {}).get("artifact_bytes") or 0)
                for item in reserved_calls
            ),
            "concurrent_tasks": len(reserved_calls),
            "hosts": len({
                (item.budget_reservation_json or {}).get("agent_id")
                for item in reserved_calls
                if (item.budget_reservation_json or {}).get("agent_id")
            }),
        }
        return {
            "limits": limits,
            "used": used,
            "reserved": reserved,
            "remaining": {
                "tool_calls": max(0, limits.get("max_tool_calls", 12) - used["tool_calls"]),
                "duration_seconds": max(
                    0,
                    limits.get("max_duration_seconds", 300) - used["task_duration_seconds"],
                ),
                "artifact_bytes": max(
                    0,
                    limits.get("max_artifact_bytes", 524_288_000) - used["artifact_bytes"],
                ),
                "hosts": max(0, limits.get("max_hosts", 5) - used["hosts"]),
            },
        }
    finally:
        session.close()


def advance_diagnosis(diagnosis_id: str) -> dict | None:
    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if diagnosis is None:
            return None
        calls = (
            session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightToolCallModel.created_at.asc())
            .all()
        )
        snapshots = [
            {
                "tool_call_id": item.id,
                "hypothesis_id": item.hypothesis_id,
                "task_id": item.task_id,
            }
            for item in calls
        ]
    finally:
        session.close()

    actions = []
    for snapshot in snapshots:
        if not snapshot["task_id"]:
            continue

        task_status = None
        session = new_session()
        try:
            diagnosis = _lock_diagnosis(session, diagnosis_id)
            tool_call = (
                session.query(DropInsightToolCallModel)
                .filter(
                    DropInsightToolCallModel.id == snapshot["tool_call_id"],
                    DropInsightToolCallModel.diagnosis_id == diagnosis_id,
                )
                .with_for_update()
                .first()
            )
            task = session.get(TaskModel, snapshot["task_id"])
            if diagnosis is None or task is None or tool_call is None:
                continue
            if tool_call.terminal_processing_status == "REPORT_EFFECTS_DONE":
                continue

            task_status = task.status
            if task_status in {"PENDING", "RUNNING", "UPLOADING", "ANALYZING"}:
                next_status = "RUNNING" if task_status != "PENDING" else "TASK_CREATED"
                if tool_call.status != next_status:
                    tool_call.status = next_status
                    _append_event(
                        session,
                        diagnosis_id,
                        "tool_call.progress",
                        "SYSTEM",
                        {
                            "tool_call_id": tool_call.id,
                            "task_id": task.id,
                            "task_status": task_status,
                            "status": next_status,
                        },
                        now_utc(),
                        effect_key=f"tool_call:{tool_call.id}:progress:{task_status}",
                    )
                    session.commit()
                actions.append({
                    "tool_call_id": tool_call.id,
                    "task_id": task.id,
                    "action": "WAIT",
                    "task_status": task_status,
                })
                continue

            if task_status in {"FAILED", "CANCELLED"}:
                timestamp = now_utc()
                failed_hypothesis_id = tool_call.hypothesis_id
                failed_tool_call_id = tool_call.id
                failed_task_id = task.id
                failed_status_reason = task.status_reason
                if tool_call.terminal_processing_status == "NONE":
                    tool_call.status = task_status
                    tool_call.result_json = {
                        "task_status": task_status,
                        "reason": failed_status_reason,
                    }
                    _release_budget_reservation(
                        tool_call,
                        timestamp=timestamp,
                        reason=f"task_{task_status.lower()}",
                    )
                    _append_event(
                        session,
                        diagnosis_id,
                        "tool_call.task_terminal",
                        "SYSTEM",
                        {
                            "tool_call_id": tool_call.id,
                            "task_id": task.id,
                            "task_status": task_status,
                        },
                        timestamp,
                    )
                    if diagnosis.status not in {
                        "COMPLETED",
                        "INSUFFICIENT_EVIDENCE",
                    }:
                        _cas_session_update(
                            session,
                            diagnosis,
                            status="COLLECTING_EVIDENCE",
                            timestamp=timestamp,
                        )
                    # Commit the terminal observation first. If reflection or
                    # replanning crashes, the next orchestrator pass retries
                    # effects instead of losing the branch forever.
                    tool_call.terminal_processing_status = "TERMINAL_RECORDED"
                    session.commit()
                # Do not keep the diagnosis/tool-call FOR UPDATE transaction
                # open while replanning. Replanning uses independent sessions
                # (and may call the model/checkpointer); retaining this session
                # can make those sessions wait on our own row lock until the
                # public gRPC deadline expires.
                session.close()
                if failed_hypothesis_id:
                    _record_lats_tool_failure(
                        diagnosis_id,
                        failed_hypothesis_id,
                        failed_tool_call_id,
                        failed_task_id,
                        task_status,
                        failed_status_reason,
                    )
                    _replan_after_insufficient_evidence(
                        diagnosis_id,
                        failed_hypothesis_id,
                        f"tool_failure_{failed_tool_call_id}",
                    )
                completion_session = new_session()
                try:
                    completed_call = (
                        completion_session.query(DropInsightToolCallModel)
                        .filter(
                            DropInsightToolCallModel.id == failed_tool_call_id,
                            DropInsightToolCallModel.diagnosis_id == diagnosis_id,
                        )
                        .with_for_update()
                        .first()
                    )
                    if completed_call is not None:
                        completed_call.terminal_processing_status = "REPORT_EFFECTS_DONE"
                        completed_call.terminal_processed_at = now_utc()
                        completion_session.commit()
                finally:
                    completion_session.close()
                actions.append({
                    "tool_call_id": failed_tool_call_id,
                    "task_id": failed_task_id,
                    "action": task_status,
                })
                continue

            if task_status != "DONE":
                continue
            if tool_call.terminal_processing_status == "NONE":
                timestamp = now_utc()
                tool_call.status = "COMPLETED"
                tool_call.result_json = {
                    "task_status": "DONE",
                    "task_id": task.id,
                }
                _settle_budget_reservation(
                    session,
                    tool_call,
                    task,
                    timestamp=timestamp,
                )
                _append_event(
                    session,
                    diagnosis_id,
                    "tool_call.task_terminal",
                    "SYSTEM",
                    {
                        "tool_call_id": tool_call.id,
                        "task_id": task.id,
                        "task_status": "DONE",
                    },
                    timestamp,
                )
                tool_call.terminal_processing_status = "TERMINAL_RECORDED"
                session.commit()
        finally:
            session.close()

        if task_status != "DONE" or not snapshot["hypothesis_id"]:
            continue

        imported = import_task_evidence(
            diagnosis_id,
            ImportTaskEvidenceRequest(
                task_id=snapshot["task_id"],
                hypothesis_id=snapshot["hypothesis_id"],
            ),
            terminal_tool_call_id=snapshot["tool_call_id"],
        )
        session = new_session()
        try:
            hypothesis_exists = session.get(
                DropInsightHypothesisModel,
                snapshot["hypothesis_id"],
            ) is not None
        finally:
            session.close()
        report = None
        if hypothesis_exists:
            # A diagnosis round may dispatch more than one independent probe.
            # Do not freeze an immutable report until every sibling probe has
            # imported its Analyzer-validated evidence. A sibling task can be
            # DONE while its evidence is still waiting for this orchestrator
            # loop, so checking only Task.status is not sufficient.
            session = new_session()
            try:
                sibling_calls = (
                    session.query(DropInsightToolCallModel)
                    .filter(
                        DropInsightToolCallModel.diagnosis_id == diagnosis_id,
                        DropInsightToolCallModel.hypothesis_id == snapshot["hypothesis_id"],
                        DropInsightToolCallModel.task_id.is_not(None),
                    )
                    .all()
                )
                pending_siblings = [
                    item
                    for item in sibling_calls
                    if item.terminal_processing_status
                    not in {"EVIDENCE_IMPORTED", "REPORT_EFFECTS_DONE"}
                ]
            finally:
                session.close()
            if not pending_siblings:
                report = generate_report(
                    diagnosis_id,
                    GenerateReportRequest(
                        hypothesis_id=snapshot["hypothesis_id"],
                    ),
                )
            else:
                actions.append({
                    "tool_call_id": snapshot["tool_call_id"],
                    "task_id": snapshot["task_id"],
                    "action": "WAIT_FOR_ROUND_EVIDENCE",
                    "pending_task_count": len(pending_siblings),
                })

        session = new_session()
        try:
            _lock_diagnosis(session, diagnosis_id)
            tool_call = (
                session.query(DropInsightToolCallModel)
                .filter(
                    DropInsightToolCallModel.id == snapshot["tool_call_id"],
                    DropInsightToolCallModel.diagnosis_id == diagnosis_id,
                )
                .with_for_update()
                .first()
            )
            if (
                tool_call is not None
                and tool_call.terminal_processing_status == "EVIDENCE_IMPORTED"
                and report is not None
                and report.effects_status == "APPLIED"
            ):
                timestamp = now_utc()
                tool_call.terminal_processing_status = "REPORT_EFFECTS_DONE"
                tool_call.terminal_processed_at = timestamp
                session.commit()
                actions.append({
                    "tool_call_id": snapshot["tool_call_id"],
                    "task_id": snapshot["task_id"],
                    "action": "EVIDENCE_IMPORTED",
                    "evidence_refs": [item.id for item in imported or []],
                    "report_id": report.id if report is not None else None,
                })
        finally:
            session.close()

    if snapshots:
        _score_candidate_hypotheses(diagnosis_id)

    return {
        "diagnosis_id": diagnosis_id,
        "actions": actions,
        "budget": get_budget_usage(diagnosis_id),
    }


def _evaluate_persisted_tool_policy(session, diagnosis, tool_name: str, arguments: dict) -> dict:
    try:
        binding = _validated_target_binding(session, diagnosis)
        binding_authoritative = True
    except ValueError:
        binding = None
        binding_authoritative = False
    allowed_agent_ids = frozenset({binding.agent_id}) if binding else frozenset()
    agent_id = arguments.get("agent_id")
    agent = session.get(AgentModel, agent_id) if isinstance(agent_id, str) else None
    capabilities = frozenset(agent.capabilities or []) if agent is not None else frozenset()
    budget = diagnosis.budget_json or {}
    used_tool_calls = (
        session.query(DropInsightToolCallModel)
        .filter(DropInsightToolCallModel.diagnosis_id == diagnosis.id)
        .count()
    )
    if not _runtime_tool_is_compatible(diagnosis, tool_name):
        return {
            "decision": "DENY",
            "checks": [{"name": "RUNTIME_COMPATIBILITY", "result": "FAIL"}],
            "reason": "所选运行时探针与服务端绑定的目标进程类型不兼容",
        }
    decision = evaluate_tool_call(
        tool_name,
        arguments,
        PolicyContext(
            allowed_agent_ids=allowed_agent_ids,
            agent_capabilities=capabilities,
            max_risk_level=budget.get("max_risk_level", "R0"),
            used_tool_calls=used_tool_calls,
            max_tool_calls=budget.get("max_tool_calls", 12),
            allowed_pid=binding.pid if binding else None,
            binding_authoritative=binding_authoritative,
            session_pre_authorized=diagnosis.mode == "AUTONOMOUS",
        ),
    )
    if decision["decision"] == "DENY":
        return decision
    reservation = _evaluate_resource_budget(
        session, diagnosis, tool_name=tool_name, arguments=arguments
    )
    decision["checks"].extend(reservation["checks"])
    if not reservation["allowed"]:
        return {
            "decision": "DENY",
            "checks": decision["checks"],
            "reason": reservation["reason"],
        }
    decision["reservation"] = reservation["reservation"]
    return decision


_ESTIMATED_ARTIFACT_BYTES = {
    "get_agent_status": 0,
    "collect_sys_metrics": 2 * 1024 * 1024,
    "start_perf_profile": 64 * 1024 * 1024,
    "start_ebpf_io_profile": 16 * 1024 * 1024,
    "start_pyspy_profile": 16 * 1024 * 1024,
    "collect_database_diagnostics": 2 * 1024 * 1024,
    "start_jvm_profile": 64 * 1024 * 1024,
    "collect_memory_profile": 8 * 1024 * 1024,
    "collect_go_profile": 32 * 1024 * 1024,
    "start_continuous_profile": 128 * 1024 * 1024,
}


def _evaluate_resource_budget(
    session,
    diagnosis,
    *,
    tool_name: str,
    arguments: dict,
) -> dict:
    """Reserve bounded resources using persisted ToolCalls under session lock."""

    limits = diagnosis.budget_json or {}
    calls = (
        session.query(DropInsightToolCallModel)
        .filter(DropInsightToolCallModel.diagnosis_id == diagnosis.id)
        .all()
    )
    reserved_calls = [
        item for item in calls
        if item.budget_reservation_status == "RESERVED"
    ]
    settled_calls = [
        item for item in calls
        if item.budget_reservation_status == "SETTLED"
    ]
    requested = _resource_reservation(tool_name, arguments)
    duration = sum(
        int((item.budget_reservation_json or {}).get("duration_seconds") or 0)
        for item in reserved_calls
    ) + sum(
        int((item.budget_settlement_json or {}).get("duration_seconds") or 0)
        for item in settled_calls
    ) + requested["duration_seconds"]
    hosts = {
        payload.get("agent_id")
        for item in reserved_calls + settled_calls
        for payload in [
            item.budget_reservation_json or {}
            if item.budget_reservation_status == "RESERVED"
            else item.budget_settlement_json or {}
        ]
        if payload.get("agent_id")
    }
    if requested["agent_id"]:
        hosts.add(requested["agent_id"])
    concurrent = len(reserved_calls) + 1
    artifact_bytes = sum(
        int((item.budget_reservation_json or {}).get("artifact_bytes") or 0)
        for item in reserved_calls
    ) + sum(
        int((item.budget_settlement_json or {}).get("artifact_bytes") or 0)
        for item in settled_calls
    ) + requested["artifact_bytes"]

    checks = []
    values = (
        ("BUDGET_DURATION", duration, limits.get("max_duration_seconds", 300)),
        ("BUDGET_CONCURRENCY", concurrent, limits.get("max_concurrent_tasks", 3)),
        ("BUDGET_HOSTS", len(hosts), limits.get("max_hosts", 5)),
        ("BUDGET_ARTIFACT_BYTES", artifact_bytes, limits.get("max_artifact_bytes", 524_288_000)),
    )
    wall_clock = probe_deadline_check(diagnosis, tool_name, arguments)
    checks.append(wall_clock)
    failed = ["BUDGET_WALL_CLOCK insufficient finalization reserve"] if wall_clock["result"] == "FAIL" else []
    for name, reserved, limit in values:
        passed = reserved <= limit
        checks.append({
            "name": name,
            "result": "PASS" if passed else "FAIL",
            "reserved": reserved,
            "limit": limit,
        })
        if not passed:
            failed.append(f"{name} {reserved}>{limit}")
    return {
        "allowed": not failed,
        "checks": checks,
        "reason": "资源预算预留失败: " + ", ".join(failed) if failed else "资源预算已原子预留",
        "reservation": requested,
    }


def _resource_reservation(tool_name: str, arguments: dict) -> dict:
    return {
        "duration_seconds": int(arguments.get("duration_seconds") or 0),
        "artifact_bytes": _ESTIMATED_ARTIFACT_BYTES.get(tool_name, 0),
        "agent_id": arguments.get("agent_id"),
        "concurrent_tasks": 1,
    }


def _release_budget_reservation(model, *, timestamp, reason: str) -> None:
    if model.budget_reservation_status != "RESERVED":
        return
    model.budget_settlement_json = {
        "duration_seconds": 0,
        "artifact_bytes": 0,
        "released_at": timestamp.isoformat(),
        "reason": reason,
    }
    model.budget_reservation_status = "RELEASED"


def _settle_budget_reservation(session, model, task, *, timestamp) -> None:
    if model.budget_reservation_status != "RESERVED":
        return
    artifact_bytes = sum(
        int(item.size_bytes or 0)
        for item in session.query(ArtifactModel)
        .filter(ArtifactModel.task_id == task.id)
        .all()
    )
    model.budget_settlement_json = {
        "duration_seconds": int(task.duration_sec or 0),
        "artifact_bytes": artifact_bytes,
        "agent_id": task.agent_id,
        "settled_at": timestamp.isoformat(),
    }
    model.budget_reservation_status = "SETTLED"


def _is_task_identity_conflict(error: IntegrityError) -> bool:
    constraint_name = getattr(
        getattr(error.orig, "diag", None),
        "constraint_name",
        None,
    )
    if constraint_name:
        return constraint_name in {
            "ix_tasks_diagnosis_step_id",
            "tasks_diagnosis_step_id_key",
            "uq_tasks_diagnosis_step_id",
        }
    return (
        "unique constraint failed: tasks.diagnosis_step_id"
        in str(error.orig).lower()
    )


def _binding_request(binding: ProcessIdentityBinding) -> ProcessIdentityBindingRequest:
    return ProcessIdentityBindingRequest(**binding.to_dict())


def _canonical_binding(value) -> dict | None:
    try:
        return ProcessIdentityBinding.from_mapping(value).to_dict()
    except (KeyError, TypeError, ValueError):
        return None


def _task_matches_request(
    session,
    task: TaskModel,
    request: CreateTaskRequest,
    *,
    now: datetime | None = None,
) -> bool:
    expected = request.model_dump(mode="json")
    requested_binding = _canonical_binding(expected.get("process_binding"))
    task_request = task.request_params or {}
    persisted_request_binding = _canonical_binding(task_request.get("process_binding"))
    persisted_binding = _canonical_binding(task.process_binding_json)
    if requested_binding is None:
        return False
    binding = ProcessIdentityBinding.from_mapping(requested_binding)
    return (
        task.diagnosis_step_id == expected["options"]["diagnosis_step_id"]
        and task.agent_id == expected["agent_id"] == binding.agent_id
        and task.target_pid == expected["target_pid"] == binding.pid
        and task.collector_type == expected["collector_type"]
        and task.sample_rate == expected["sample_rate"]
        and task.duration_sec == expected["duration_sec"]
        and task_request == expected
        and persisted_request_binding == requested_binding
        and persisted_binding == requested_binding
        and task.process_snapshot_id == binding.process_snapshot_id
        and SqlRepository()._validate_process_binding_in_session(
            session,
            binding,
            agent_id=task.agent_id,
            target_pid=task.target_pid,
            now=now or now_utc(),
        )
    )


_TASK_KIND_BY_NAME = {item["name"]: item for item in TASK_KINDS}


def _task_upload_object_keys(
    task_id: str,
    attempt_id: str,
    collector_type: str,
) -> list[str]:
    kind = _TASK_KIND_BY_NAME.get(collector_type)
    if kind is None:
        raise ValueError(f"unknown TaskKind: {collector_type}")
    prefix = f"tasks/{task_id}/attempts/{attempt_id}/"
    return [
        prefix + filename
        if filename == "manifest.json"
        else prefix + "raw/" + filename
        for filename in kind["artifact_filenames"]
    ]


def _issue_task_upload_authorizations(
    session,
    task: TaskModel,
    *,
    timestamp: datetime,
) -> None:
    ttl_seconds = min(
        3600,
        max(
            int(os.getenv("MINI_DROP_UPLOAD_AUTH_TTL_SEC", "1800")),
            int(task.duration_sec or 0) + 300,
        ),
    )
    attempt_id = f"attempt_{uuid4().hex}"
    expires_at = timestamp + timedelta(seconds=ttl_seconds)
    bucket = os.getenv("MINIO_BUCKET", "mini-drop")
    session.query(TaskUploadAuthorizationModel).filter(
        TaskUploadAuthorizationModel.task_id == task.id
    ).delete(synchronize_session=False)
    for object_key in _task_upload_object_keys(
        task.id, attempt_id, task.collector_type
    ):
        session.add(
            TaskUploadAuthorizationModel(
                task_id=task.id,
                task_attempt_id=attempt_id,
                object_key=object_key,
                put_url=presigned_put_url(
                    bucket,
                    object_key,
                    expires=ttl_seconds,
                ),
                expires_at=expires_at,
                created_at=timestamp,
            )
        )


def _execute_approved_tool_call(tool_call_id: str) -> DropInsightToolCallModel:
    session = new_session()
    try:
        model = (
            session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.id == tool_call_id)
            .with_for_update()
            .first()
        )
        if model is None:
            raise ValueError("tool call not found")
        if model.status not in {"APPROVED", "TASK_CREATED", "COMPLETED"}:
            raise ValueError(f"tool call is not executable: {model.status}")
        if model.status in {"TASK_CREATED", "COMPLETED"}:
            return model
        arguments = model.arguments_json or {}
        timestamp = now_utc()
        diagnosis = (
            session.query(DropInsightSessionModel)
            .filter(DropInsightSessionModel.id == model.diagnosis_id)
            .with_for_update()
            .first()
        )
        if diagnosis is None:
            raise ValueError("diagnosis not found")
        deadline_check = probe_deadline_check(diagnosis, model.tool_name, arguments, now=timestamp)
        if deadline_check["result"] == "FAIL":
            model.status = "FAILED"
            model.result_json = {"error": "wall_clock_budget_exhausted", "check": deadline_check}
            model.executed_at = timestamp
            _release_budget_reservation(model, timestamp=timestamp, reason="wall_clock_budget_exhausted")
            _append_event(session, model.diagnosis_id, "tool_call.failed", "POLICY",
                          {"tool_call_id": model.id, "tool_name": model.tool_name,
                           "reason": "wall_clock_budget_exhausted", "check": deadline_check}, timestamp)
            session.commit()
            session.refresh(model)
            return model
        binding = _validated_target_binding(session, diagnosis, now=timestamp)
        if arguments.get("agent_id") != binding.agent_id:
            raise ValueError("tool call Agent disagrees with process binding")
        if arguments.get("pid") is not None and arguments.get("pid") != binding.pid:
            raise ValueError("tool call PID disagrees with process binding")
        if not _runtime_tool_is_compatible(diagnosis, model.tool_name):
            model.status = "FAILED"
            model.result_json = {
                "error": "runtime_incompatible_tool",
                "reason": "所选运行时探针与服务端绑定的目标进程类型不兼容",
            }
            model.executed_at = timestamp
            _release_budget_reservation(
                model,
                timestamp=timestamp,
                reason="runtime_incompatible_tool",
            )
            _append_event(
                session,
                model.diagnosis_id,
                "tool_call.failed",
                "POLICY",
                {
                    "tool_call_id": model.id,
                    "tool_name": model.tool_name,
                    "reason": "runtime_incompatible_tool",
                },
                timestamp,
            )
            session.commit()
            session.refresh(model)
            return model
        if model.tool_name == "get_agent_status":
            agent = session.get(AgentModel, arguments["agent_id"])
            model.result_json = agent.to_dict() if agent is not None else {"found": False}
            model.status = "COMPLETED"
            model.executed_at = timestamp
            model.budget_settlement_json = {
                "duration_seconds": 0,
                "artifact_bytes": 0,
                "agent_id": arguments.get("agent_id"),
                "settled_at": timestamp.isoformat(),
            }
            model.budget_reservation_status = "SETTLED"
            _append_event(
                session,
                model.diagnosis_id,
                "tool_call.completed",
                "SYSTEM",
                {"tool_call_id": model.id, "tool_name": model.tool_name},
                timestamp,
            )
            session.commit()
            session.refresh(model)
            return model

        collector_type = TOOL_TO_COLLECTOR.get(model.tool_name)
        if collector_type is None:
            model.status = "FAILED"
            model.result_json = {"error": "tool has no executor"}
            model.executed_at = timestamp
            _release_budget_reservation(model, timestamp=timestamp, reason="executor_missing")
            session.commit()
            session.refresh(model)
            return model
        task_request = CreateTaskRequest(
            name=f"Drop Insight: {model.tool_name}",
            agent_id=arguments["agent_id"],
            target_pid=arguments["pid"],
            collector_type=collector_type,
            sample_rate=arguments.get("sample_rate", 99),
            duration_sec=arguments["duration_seconds"],
            options={
                "diagnosis_step_id": tool_call_id,
                "drop_insight_diagnosis_id": model.diagnosis_id,
                "drop_insight_tool_call_id": tool_call_id,
                **(
                    {"event": arguments["event"]}
                    if arguments.get("event")
                    else {}
                ),
            },
            process_binding=_binding_request(binding),
        )
        task = SqlRepository().create_task_in_session(
            session,
            task_request,
        )
        _issue_task_upload_authorizations(
            session,
            task,
            timestamp=timestamp,
        )
        model.task_id = task.id
        model.status = "TASK_CREATED"
        model.executed_at = now_utc()
        _append_event(
            session,
            model.diagnosis_id,
            "tool_call.task_created",
            "SYSTEM",
            {"tool_call_id": model.id, "task_id": task.id},
            model.executed_at,
            effect_key=f"tool_call:{model.id}:task_created",
        )
        session.commit()
        session.refresh(model)
        return model
    except IntegrityError as error:
        session.rollback()
        if not _is_task_identity_conflict(error):
            raise
        task = (
            session.query(TaskModel)
            .filter(TaskModel.diagnosis_step_id == tool_call_id)
            .first()
        )
        if task is None:
            raise
        model = (
            session.query(DropInsightToolCallModel)
            .filter(DropInsightToolCallModel.id == tool_call_id)
            .with_for_update()
            .first()
        )
        if model is None:
            raise ValueError("tool call not found")
        arguments = model.arguments_json or {}
        replay_timestamp = now_utc()
        diagnosis = (
            session.query(DropInsightSessionModel)
            .filter(DropInsightSessionModel.id == model.diagnosis_id)
            .with_for_update()
            .first()
        )
        if diagnosis is None:
            raise ValueError("diagnosis not found")
        binding = _validated_target_binding(session, diagnosis, now=replay_timestamp)
        if (
            arguments.get("agent_id") != binding.agent_id
            or arguments.get("pid") != binding.pid
        ):
            raise ValueError("replayed tool call disagrees with process binding")
        collector_type = TOOL_TO_COLLECTOR.get(model.tool_name)
        if collector_type is None:
            raise
        task_request = CreateTaskRequest(
            name=f"Drop Insight: {model.tool_name}",
            agent_id=arguments["agent_id"],
            target_pid=arguments["pid"],
            collector_type=collector_type,
            sample_rate=arguments.get("sample_rate", 99),
            duration_sec=arguments["duration_seconds"],
            options={
                "diagnosis_step_id": tool_call_id,
                "drop_insight_diagnosis_id": model.diagnosis_id,
                "drop_insight_tool_call_id": tool_call_id,
                **(
                    {"event": arguments["event"]}
                    if arguments.get("event")
                    else {}
                ),
            },
            process_binding=_binding_request(binding),
        )
        if not _task_matches_request(
            session,
            task,
            task_request,
            now=replay_timestamp,
        ):
            raise ValueError("existing task does not match immutable request authority")
        if model.task_id and model.task_id != task.id:
            raise ValueError("tool call is linked to a different task")
        if model.status == "APPROVED":
            model.task_id = task.id
            model.status = "TASK_CREATED"
            model.executed_at = model.executed_at or now_utc()
            _append_event(
                session,
                model.diagnosis_id,
                "tool_call.task_created",
                "SYSTEM",
                {"tool_call_id": model.id, "task_id": task.id},
                model.executed_at,
                effect_key=f"tool_call:{model.id}:task_created",
            )
            session.commit()
            session.refresh(model)
        return model
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


_UNKNOWN_HYPOTHESIS = {
    "statement": "其他未知原因（OTHER/UNKNOWN）",
    # 字段需非空才能通过 schema 校验；占位文案表明该候选是兜底假设。
    "expected": ["存在尚未被既有候选假设充分解释的观测现象"],
    "falsification": ["所有观测现象均能被既有候选假设充分解释"],
}

# 每个领域的常见竞品假设（方案 §5.2：保留 1~3 个候选 + OTHER/UNKNOWN）。
# 主假设由 planner 决定；这里提供可区分的备选，避免假设成为答案边界。
_ALTERNATIVE_HYPOTHESES: dict[str, list[dict]] = {
    "CPU_HOTSPOT": [
        {
            "statement": "同宿主机其他工作负载与目标进程争抢 CPU 资源",
            "expected": ["同宿主机对照实例存在相似 CPU 热点"],
            "falsification": ["同宿主机其他实例 CPU 平稳且目标存在独立热点"],
        },
        {
            "statement": "内存压力或 I/O 等待间接导致 CPU 饱和",
            "expected": ["系统指标显示内存压力、换页或 I/O 等待升高"],
            "falsification": ["内存与 I/O 指标平稳且 CPU 热点集中在业务函数"],
        },
    ],
    "IO_LATENCY": [
        {
            "statement": "同宿主机 IO 争抢导致目标进程写盘变慢",
            "expected": ["同窗口其他容器存在高 IO 负载或队列深度上升"],
            "falsification": ["宿主机 IO 平稳且目标进程自身同步写盘占主导"],
        },
        {
            "statement": "目标进程同步写盘或落盘路径过重",
            "expected": ["IO 热点集中在目标进程的写系统调用"],
            "falsification": ["目标进程 IO 占比低但块设备延迟仍高"],
        },
    ],
    "NETWORK_DEGRADATION": [
        {
            "statement": "宿主机网络丢包或重传导致服务变慢",
            "expected": ["网络指标显示重传率、丢包或队列积压升高"],
            "falsification": ["网络指标平稳且本实例存在独立热点"],
        },
        {
            "statement": "下游依赖响应变慢传播到本实例",
            "expected": ["下游调用耗时上升或连接池排队"],
            "falsification": ["下游指标正常且本实例存在独立热点"],
        },
    ],
    "JVM_GC": [
        {
            "statement": "堆内存配置过小导致频繁 Full GC",
            "expected": ["GC 日志显示 Full GC 频率高且堆使用接近上限"],
            "falsification": ["GC 平稳且停顿集中在业务代码"],
        },
        {
            "statement": "内存泄漏导致堆持续增长",
            "expected": ["堆使用随时间单调上升且无法回落"],
            "falsification": ["堆使用稳定且 GC 正常"],
        },
    ],
    "PYTHON_RUNTIME": [
        {
            "statement": "GIL 竞争限制多线程并发能力",
            "expected": ["多线程 CPU 密集但并行度受限"],
            "falsification": ["单线程热点明确且 GIL 非瓶颈"],
        },
        {
            "statement": "等待或 I/O 阻塞被误判为用户态热点",
            "expected": ["系统调用或等待占比偏高"],
            "falsification": ["用户态函数独占热点"],
        },
    ],
    "MEMORY_PRESSURE": [
        {
            "statement": "无界缓存或对象未释放导致 RSS 增长",
            "expected": ["RSS 单调增长且存在无界缓存路径"],
            "falsification": ["RSS 稳定且内存压力来自宿主机"],
        },
        {
            "statement": "同宿主机内存争抢触发换页",
            "expected": ["同窗口宿主机内存压力或 swap 上升"],
            "falsification": ["宿主机内存平稳且目标自身 RSS 异常增长"],
        },
    ],
    "DOWNSTREAM_DEPENDENCY": [
        {
            "statement": "本实例资源正常但下游队列积压",
            "expected": ["下游队列深度或延迟上升"],
            "falsification": ["下游平稳且本实例存在独立热点"],
        },
        {
            "statement": "连接池耗尽导致调用排队等待",
            "expected": ["连接池使用率接近上限或等待时长上升"],
            "falsification": ["连接池充足且延迟来自其他环节"],
        },
    ],
    "QUEUE_CONGESTION": [
        {
            "statement": "消费者处理能力不足导致积压",
            "expected": ["消费者资源饱和且消费速率跟不上生产"],
            "falsification": ["消费者资源充足且队列持续积压"],
        },
        {
            "statement": "生产者突发写入导致瞬时积压",
            "expected": ["生产速率出现尖峰"],
            "falsification": ["生产速率平稳但消费速率下降"],
        },
    ],
    "DATABASE_LOCK": [
        {
            "statement": "慢查询持有锁时间过长",
            "expected": ["存在长事务或慢查询锁等待"],
            "falsification": ["锁等待快照为空且连接池正常"],
        },
        {
            "statement": "连接池耗尽导致数据库排队",
            "expected": ["连接池使用率接近上限"],
            "falsification": ["连接池充足且查询本身慢"],
        },
    ],
    "NOISY_NEIGHBOR": [
        {
            "statement": "目标实例自身存在独立热点",
            "expected": ["目标进程存在独占性的热点函数"],
            "falsification": ["目标资源平稳而宿主机其他实例异常"],
        },
        {
            "statement": "宿主机共享资源（带宽/IO）被其他租户占用",
            "expected": ["宿主机共享指标出现争抢"],
            "falsification": ["共享资源平稳且目标自身异常"],
        },
    ],
    "CONTAINER_RESOURCE_LIMIT": [
        {
            "statement": "CPU 配额限制导致节流",
            "expected": ["出现 CPU 节流或配额用尽"],
            "falsification": ["配额充足且无节流"],
        },
        {
            "statement": "内存限制触发频繁回收或 OOM 风险",
            "expected": ["内存接近限额或存在回收波动"],
            "falsification": ["内存余量充足且资源平稳"],
        },
    ],
}


def _candidate_hypotheses(category: str, plan: dict) -> list[dict]:
    """主假设 + 领域备选假设 + OTHER/UNKNOWN（方案 §5.2）。"""
    primary = {
        "statement": plan["statement"],
        "expected": plan.get("expected", []),
        "falsification": plan.get("falsification", []),
    }
    alternatives = _ALTERNATIVE_HYPOTHESES.get(category, [])
    return [primary, *alternatives, _UNKNOWN_HYPOTHESIS]


def _score_candidate_hypotheses(diagnosis_id: str) -> None:
    """基于已导入证据给「非主假设」的候选假设打分。

    主假设状态由报告生成决定（generate_report）；这里只更新备选假设与
    OTHER/UNKNOWN，让候选集合保留支持/反证/未知三种合法状态。
    """
    session = new_session()
    try:
        # Several orchestrator callers may advance the same diagnosis at the
        # same time (background Worker, browser retry and an acceptance poller).
        # Lock before reading candidate statuses so every scorer observes the
        # previously committed transition instead of all racing from OPEN.
        # `_append_event` takes the same row lock later and is therefore a
        # re-entrant no-op inside this transaction.
        if _lock_diagnosis(session, diagnosis_id) is None:
            return
        hypotheses = (
            session.query(DropInsightHypothesisModel)
            .filter(DropInsightHypothesisModel.diagnosis_id == diagnosis_id)
            .order_by(
                DropInsightHypothesisModel.round_index.asc(),
                DropInsightHypothesisModel.id.asc(),
            )
            .all()
        )
        if not hypotheses:
            return
        report_hypothesis_ids = {
            row.hypothesis_id
            for row in session.query(DropInsightReportModel)
            .filter(DropInsightReportModel.diagnosis_id == diagnosis_id)
            .all()
            if row.hypothesis_id
        }
        evidence_rows = (
            session.query(DropInsightEvidenceModel)
            .filter(DropInsightEvidenceModel.diagnosis_id == diagnosis_id)
            .order_by(DropInsightEvidenceModel.created_at.desc())
            .all()
        )
        metadata = None
        for row in evidence_rows:
            envelope = EvidenceEnvelope.model_validate(row.envelope_json)
            obs_meta = (envelope.observation or {}).get("metadata") or {}
            if isinstance(obs_meta, dict) and obs_meta.get("top_functions"):
                metadata = obs_meta
                break
        if metadata is None:
            return
        timestamp = now_utc()
        changed = []
        for hypothesis in hypotheses:
            if hypothesis.id in report_hypothesis_ids:
                continue
            if hypothesis.status in {"SUPPORTED", "COUNTER"}:
                continue
            predicate = _compute_hypothesis_predicate(hypothesis, metadata)
            if predicate and predicate["outcome"] == "SUPPORT":
                next_status = "SUPPORTED"
            elif predicate and predicate["outcome"] == "COUNTER":
                next_status = "COUNTER"
            else:
                next_status = "INCONCLUSIVE"
            if hypothesis.status == next_status:
                continue
            hypothesis.status = next_status
            hypothesis.updated_at = timestamp
            changed.append(
                {
                    "hypothesis_id": hypothesis.id,
                    "round_index": hypothesis.round_index,
                    "status": hypothesis.status,
                }
            )
        if changed:
            # Keep the durable payload canonical. This also makes semantic
            # no-op suppression deterministic across PostgreSQL query plans.
            changed.sort(
                key=lambda item: (
                    int(item.get("round_index") or 0),
                    str(item.get("hypothesis_id") or ""),
                )
            )
            _append_event(
                session,
                diagnosis_id,
                "hypotheses.scored",
                "SYSTEM",
                {"hypotheses": changed},
                timestamp,
            )
            session.commit()
    finally:
        session.close()


def _apply_active_planner_skill(
    diagnosis,
    diagnosis_id: str,
    plan: dict,
    target: dict,
    *,
    round_index: int | None = None,
    phase: str = "INITIAL_PLAN",
    attempted_tools: list[str] | set[str] | None = None,
    available_tools: list[str] | set[str] | None = None,
    reuse_existing: bool = True,
) -> dict | None:
    """Resolve one published Skill route for the current planning round.

    The Skill subsystem owns retrieval and route progression.  This service
    boundary deliberately passes the *already authorised* tool set into it;
    the returned Skill remains a planning prior and can never expand runtime
    capabilities, approval policy, evidence admission, or the diagnosis scope.
    """
    if (getattr(diagnosis, "skill_policy", None) or "AUTO") == "DISABLED":
        return None
    from .skill_evolution import apply_active_skill

    decision = apply_active_skill(
        diagnosis_id,
        str(plan.get("category") or phase),
        plan,
        target,
        round_index=round_index,
        phase=phase,
        attempted_tools=attempted_tools,
        available_tools=available_tools,
        reuse_existing=reuse_existing,
        return_decision=True,
    )
    if not decision:
        return None
    _record_skill_route_decision(
        diagnosis_id,
        decision,
        round_index=round_index,
        phase=phase,
    )
    if decision.get("applied", True) is not False:
        return decision
    # An exhausted, previously validated Skill still contributes its stop and
    # refutation contract to this round's reasoning context.  It has no selected
    # tool, so it cannot dispatch a stale route step.  Rejected/unmatched content
    # is never elevated into the trusted model context.
    if (
        str(decision.get("state") or "").upper() == "EXHAUSTED"
        and decision.get("skill_instructions")
    ):
        return decision
    return None


def _skill_selected_tool(
    activation: dict | None,
    allowed_tools: list[str],
) -> str | None:
    """Return a Skill route step only when the server allow-list permits it."""

    selected = str((activation or {}).get("selected_tool") or "").strip()
    return selected if selected and selected in allowed_tools else None


def _skill_event_summary(activation: dict | None) -> dict | None:
    """Build a compact public event view without copying full Skill Markdown."""

    if not activation:
        return None
    instructions = activation.get("skill_instructions") or {}
    summary = {
        key: activation.get(key)
        for key in (
            "applied",
            "state",
            "skill_id",
            "skill_name",
            "version",
            "category",
            "skill_category",
            "requested_category",
            "baseline_category",
            "selected_category",
            "match_score",
            "baseline_tool",
            "selected_tool",
            "category_correction",
            "reuse_step",
            "load_mode",
            "exit_reason",
        )
        if activation.get(key) is not None
    } | {
        key: instructions.get(key)
        for key in (
            "content_sha256",
            "source_sha256",
            "source_path",
            "loaded_sections",
            "load_mode",
        )
        if isinstance(instructions, dict) and instructions.get(key) is not None
    }
    if isinstance(instructions, dict) and instructions.get("name"):
        summary.setdefault("skill_name", instructions["name"])
    if activation.get("skill_category"):
        summary.setdefault("category", activation["skill_category"])
        summary.setdefault("selected_category", activation["skill_category"])
    if activation.get("requested_category"):
        summary.setdefault("baseline_category", activation["requested_category"])
    return summary


def _record_skill_route_decision(
    diagnosis_id: str,
    decision: dict,
    *,
    round_index: int | None,
    phase: str,
) -> None:
    """Publish one idempotent, compact Skill route event for live clients."""

    state = str(decision.get("state") or "ACTIVATED").upper()
    if decision.get("applied", True) is False:
        # A first-round ordinary abstention is not a route exit.  Rejections
        # and exhaustion are important because they explain why the tree left
        # a previously viable route.
        if state == "NOT_MATCHED":
            return
        event_type = "skill.route_exited"
    elif state == "REUSED":
        event_type = "skill.route_reused"
    else:
        # SWITCHED is a fresh activation with state preserved in the payload.
        event_type = "skill.route_activated"
    payload = _skill_event_summary(decision) or {}
    payload.update(
        {
            "round_index": round_index,
            "phase": phase,
            "knowledge_is_evidence": False,
        }
    )
    session = new_session()
    try:
        timestamp = now_utc()
        _append_event(
            session,
            diagnosis_id,
            event_type,
            "SYSTEM",
            payload,
            timestamp,
            effect_key=(
                f"skill-route:{diagnosis_id}:{round_index or 0}:"
                f"{phase.casefold()}"
            ),
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        if not _event_effect_exists(
            diagnosis_id,
            f"skill-route:{diagnosis_id}:{round_index or 0}:{phase.casefold()}",
        ):
            raise
    finally:
        session.close()


def run_diagnosis_planner(
    diagnosis_id: str,
    payload: RunPlannerRequest,
    *,
    requested_by: str = "system:planner",
) -> dict | None:
    diagnosis = get_diagnosis(diagnosis_id)
    if diagnosis is None:
        return None
    validation_session = new_session()
    try:
        binding = _validated_target_binding(validation_session, diagnosis)
    finally:
        validation_session.close()
    target = diagnosis.target_json or {}
    # Only counter-evidence, insufficient evidence, or user correction opens a new round.
    existing_calls = list_tool_calls(diagnosis_id)
    existing_hypotheses = list_hypotheses(diagnosis_id)
    if existing_calls and existing_hypotheses:
        primary = next(
            (item for item in existing_hypotheses if item.id == existing_calls[0].hypothesis_id),
            existing_hypotheses[0],
        )
        retrievals = list_knowledge_retrievals(diagnosis_id)
        return {
            "planner_kind": "IDEMPOTENT_REPLAY",
            "planner_version": "rules-v2",
            "classification_confidence": 1.0,
            "category": "EXISTING_PLAN",
            "decision_source": primary.source,
            "reasoning_summary": "返回已持久化的诊断计划；重复请求不会创建新假设。",
            "retrieval_trace": (
                retrievals[-1]["retrieval_trace"] if retrievals else None
            ),
            "hypothesis": primary.to_dict(),
            "tool_call": existing_calls[0].to_dict(),
        }

    query = (diagnosis.query or "").lower()
    # Route from positive symptom clauses. Requested counter-checks remain in
    # the full query for hypothesis generation, but cannot choose the primary
    # category merely because they mention I/O, GC or another alternative.
    intent_query = _primary_intent_query(query)
    triage_arguments = _planner_tool_arguments("collect_sys_metrics", target)
    questions: list[dict] | None = None
    if _is_database_query(query):
        plan = {
            "planner_version": "rules-v2",
            "category": "DATABASE_LOCK",
            "statement": "请求变慢可能与数据库锁等待或连接阻塞有关",
            "expected": ["数据库快照存在等待锁的会话", "阻塞关系能够指向至少一个 blocker"],
            "falsification": ["系统资源平稳且数据库锁等待快照为空"],
            "tool_name": "collect_database_diagnostics",
            "arguments": _planner_tool_arguments("collect_database_diagnostics", target),
        }
    elif _should_route_downstream_dependency(intent_query, query):
        plan = {
            "planner_version": "rules-v3",
            "category": "DOWNSTREAM_DEPENDENCY",
            "statement": "入口服务变慢可能由下游依赖响应延迟传播引起",
            "expected": ["下游请求量与响应延迟在同一观测窗口内同步升高"],
            "falsification": ["下游响应平稳且本实例存在独立热点"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in intent_query for token in (
        "入口负载", "到达率", "完成率", "请求被拒绝", "load saturation"
    )):
        plan = {
            "planner_version": "rules-v3",
            "category": "LOAD_SATURATION",
            "statement": "入口请求到达率可能超过目标服务的持续处理能力",
            "expected": ["到达率高于完成率，且拒绝数、队列或处理延迟同步上升"],
            "falsification": ["到达率未超过完成率，且没有拒绝或积压"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in intent_query for token in ("丢包", "packet loss", "网络", "重传", "timeout", "超时")):
        plan = {
            "planner_version": "rules-v2",
            "category": "NETWORK_DEGRADATION",
            "statement": "服务异常可能由网络丢包、重传或连接超时引起",
            "expected": ["系统初筛显示网络或等待指标异常，需继续采集连接与重传证据"],
            "falsification": ["同窗口网络指标和对照实例均正常"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in intent_query for token in ("队列", "积压", "backlog", "consumer lag", "mq")):
        plan = {
            "planner_version": "rules-v3",
            "category": "QUEUE_CONGESTION",
            "statement": "吞吐下降可能由生产速率超过消费速率并形成队列积压引起",
            "expected": ["生产速率高于消费速率，且队列深度或消费延迟同步增长"],
            "falsification": ["生产消费速率平衡且队列无积压"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in intent_query for token in ("噪声邻居", "同宿主机", "资源争抢", "noisy neighbor")):
        plan = {
            "planner_version": "rules-v3",
            "category": "NOISY_NEIGHBOR",
            "statement": "目标服务可能受到同宿主机其他工作负载的资源干扰",
            "expected": ["独立 peer 在同一宿主机和时间窗口内持续消耗资源"],
            "falsification": ["同宿主机其他实例平稳且目标自身存在独立热点"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in intent_query for token in ("fd 泄漏", "文件描述符", "too many open files")):
        plan = {
            "planner_version": "rules-v3",
            "category": "FD_LEAK",
            "statement": "目标进程可能存在文件描述符持续增长或未释放",
            "expected": ["FD 数在采样窗口内持续增长并接近进程上限"],
            "falsification": ["FD 数稳定或增长能由连接池预热解释"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in intent_query for token in ("锁竞争", "futex", "mutex", "自旋锁")):
        lock_tool = _lock_profile_tool(diagnosis)
        plan = {
            "planner_version": "rules-v3",
            "category": "LOCK_CONTENTION",
            "statement": "目标进程可能因锁竞争、自旋或频繁上下文切换而降低吞吐",
            "expected": ["锁等待计数或锁相关调用路径与吞吐下降同窗增长"],
            "falsification": ["锁相关等待平稳且热点来自独立业务计算"],
            "tool_name": lock_tool,
            "arguments": _planner_tool_arguments(
                lock_tool,
                target,
                query=diagnosis.query,
            ),
        }
    elif any(token in intent_query for token in ("io", "i/o", "disk", "磁盘", "写入", "读取", "filechannel", "fdatasync")):
        plan = {
            "planner_version": "rules-v3",
            "category": "IO_LATENCY",
            "statement": "目标进程的性能下降可能由磁盘 I/O 延迟或同步写入路径阻塞引起",
            "expected": ["进程写入活动与块设备高延迟分布在同一窗口出现"],
            "falsification": ["进程写入和块设备延迟均处于基线"],
            "tool_name": "start_ebpf_io_profile",
            "arguments": _planner_tool_arguments("start_ebpf_io_profile", target),
        }
    elif any(token in intent_query for token in ("内存", "memory", "rss", "pss", "oom", "堆外", "offheap")):
        plan = {
            "planner_version": "rules-v3",
            "category": "MEMORY_PRESSURE",
            "statement": "目标进程可能存在可观测的内存足迹增长或对象保留",
            "expected": ["RSS/PSS 或受约束的进程内保留量在故障窗口内明显增长"],
            "falsification": ["进程内存足迹稳定且没有换页或保留量增长"],
            "tool_name": "collect_memory_profile",
            "arguments": _planner_tool_arguments("collect_memory_profile", target),
        }
    elif any(token in intent_query for token in ("python", "py-spy", "gil", "协程")):
        plan = {
            "planner_version": "rules-v3",
            "category": "PYTHON_RUNTIME",
            "statement": "目标 Python 进程可能存在用户态热点函数或 GIL 竞争",
            "expected": ["py-spy 样本集中在少数 Python 函数或线程"],
            "falsification": ["Python 栈样本均匀且无明显热点"],
            "tool_name": "start_pyspy_profile",
            "arguments": _planner_tool_arguments("start_pyspy_profile", target),
        }
    elif _query_mentions_go_runtime(intent_query):
        plan = {
            "planner_version": "rules-v3",
            "category": "GO_RUNTIME",
            "statement": "目标 Go 服务可能存在 CPU 热点或 goroutine 执行路径异常",
            "expected": ["pprof 样本集中在少数 Go 函数或运行时路径"],
            "falsification": ["Go CPU 样本分散且系统资源处于基线"],
            "tool_name": "collect_go_profile",
            "arguments": _planner_tool_arguments("collect_go_profile", target),
        }
    elif any(
        token in intent_query
        for token in (
            "cpu 持续",
            "cpu 升高",
            "cpu 异常",
            "cpu 飙",
            "cpu 热点",
            "计算热点",
            "热点函数",
            "火焰图",
            "busy loop",
        )
    ):
        plan = {
            "planner_version": "rules-v3",
            "classification_confidence": 0.95,
            "category": "CPU_HOTSPOT",
            "statement": "目标进程可能存在 CPU 热点函数或系统调用开销",
            "expected": ["perf 样本集中在少数热点函数或内核调用链"],
            "falsification": ["CPU 样本均匀且没有显著热点"],
            "tool_name": "start_perf_profile",
            "arguments": _planner_tool_arguments("start_perf_profile", target),
        }
    elif any(token in intent_query for token in ("jvm", "java", "gc", "full gc", "垃圾回收")):
        plan = {
            "planner_version": "rules-v2",
            "category": "JVM_GC",
            "statement": "Java 服务停顿可能与 GC 压力或堆内存波动有关",
            "expected": ["系统初筛显示 CPU、RSS 或停顿窗口异常，需继续采集 JVM 证据"],
            "falsification": ["GC、堆和系统资源在同窗口均处于基线范围"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in query for token in ("fd 泄漏", "文件描述符", "too many open files")):
        plan = {
            "planner_version": "rules-v3",
            "category": "FD_LEAK",
            "statement": "目标进程可能存在文件描述符持续增长或未释放",
            "expected": ["FD 数在采样窗口内持续增长并接近进程上限"],
            "falsification": ["FD 数稳定或增长能由连接池预热解释"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in query for token in ("锁竞争", "futex", "mutex", "自旋锁")):
        lock_tool = _lock_profile_tool(diagnosis)
        plan = {
            "planner_version": "rules-v3",
            "category": "LOCK_CONTENTION",
            "statement": "目标进程可能因锁竞争、自旋或频繁上下文切换而消耗 CPU",
            "expected": ["调用栈集中在锁、futex 或自旋路径，并与线程等待同窗"],
            "falsification": ["锁相关栈占比低且热点来自独立业务计算"],
            "tool_name": lock_tool,
            "arguments": _planner_tool_arguments(
                lock_tool,
                target,
                query=diagnosis.query,
            ),
        }
    elif any(token in query for token in ("下游", "依赖服务", "rpc", "upstream", "downstream")):
        plan = {
            "planner_version": "rules-v2",
            "category": "DOWNSTREAM_DEPENDENCY",
            "statement": "入口服务变慢可能由下游依赖节点传播引起",
            "expected": ["本实例资源不足以解释延迟，需要同窗口下游和对照实例证据"],
            "falsification": ["下游节点正常且本实例存在独立热点"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in query for token in ("队列", "积压", "backlog", "consumer lag", "mq")):
        plan = {
            "planner_version": "rules-v2",
            "category": "QUEUE_CONGESTION",
            "statement": "吞吐下降可能由队列积压或消费者处理能力不足引起",
            "expected": ["系统初筛显示消费者资源饱和，需继续引用队列深度与消费速率"],
            "falsification": ["队列无积压且消费者资源正常"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in query for token in ("限流", "cpu limit", "memory limit", "容器限制", "throttl")):
        plan = {
            "planner_version": "rules-v2",
            "category": "CONTAINER_RESOURCE_LIMIT",
            "statement": "性能下降可能由容器 CPU 节流或内存限制触发",
            "expected": ["系统指标显示资源接近限额或出现节流相关异常"],
            "falsification": ["容器限额充足且无节流记录"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in query for token in ("噪声邻居", "同宿主机", "资源争抢", "noisy neighbor")):
        plan = {
            "planner_version": "rules-v2",
            "category": "NOISY_NEIGHBOR",
            "statement": "目标服务可能受到同宿主机其他工作负载的资源干扰",
            "expected": ["目标与同宿主机对照实例在同窗口出现共享资源竞争"],
            "falsification": ["同宿主机其他实例平稳且目标自身存在独立热点"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in query for token in ("io", "disk", "磁盘", "写入", "读取", "延迟")):
        plan = {
            "planner_version": "rules-v2",
            "category": "IO_LATENCY",
            "statement": "目标进程的性能下降可能由磁盘 IO 延迟或内核写入路径阻塞引起",
            "expected": ["eBPF IO 延迟分布出现长尾或高延迟桶"],
            "falsification": ["IO 延迟分布与基线一致且没有长尾"],
            "tool_name": "start_ebpf_io_profile",
            "arguments": _planner_tool_arguments("start_ebpf_io_profile", target),
        }
    elif any(token in query for token in ("python", "py-spy", "gil", "协程")):
        plan = {
            "planner_version": "rules-v2",
            "category": "PYTHON_RUNTIME",
            "statement": "目标 Python 进程可能存在用户态热点函数或 GIL 竞争",
            "expected": ["py-spy 样本集中在少数 Python 函数或线程"],
            "falsification": ["Python 栈样本均匀且无明显热点"],
            "tool_name": "start_pyspy_profile",
            "arguments": _planner_tool_arguments("start_pyspy_profile", target),
        }
    elif any(token in query for token in ("内存", "memory", "rss", "oom")):
        plan = {
            "planner_version": "rules-v2",
            "category": "MEMORY_PRESSURE",
            "statement": "目标进程可能存在内存压力或异常 RSS 增长",
            "expected": ["系统指标显示 RSS 或内存压力持续异常"],
            "falsification": ["RSS、换页和内存压力均处于正常范围"],
            "tool_name": "collect_sys_metrics",
            "arguments": _planner_tool_arguments("collect_sys_metrics", target),
        }
    elif any(
        token in query
        for token in ("cpu", "热点", "火焰图", "算力", "负载高", "load high")
    ):
        plan = {
            "planner_version": "rules-v2",
            "classification_confidence": 0.95,
            "category": "CPU_HOTSPOT",
            "statement": "目标进程可能存在 CPU 热点函数或系统调用开销",
            "expected": ["perf 样本集中在少数热点函数或内核调用链"],
            "falsification": ["CPU 样本均匀且没有显著热点"],
            "tool_name": "start_perf_profile",
            "arguments": _planner_tool_arguments("start_perf_profile", target),
        }
    else:
        questions = [
            {
                "question_id": "problem.domain",
                "prompt": "请补充异常属于 CPU、内存、磁盘 IO、网络、数据库还是语言运行时。",
            },
            {
                "question_id": "problem.symptom",
                "prompt": "请补充可观测症状，例如延迟、错误率、吞吐或资源曲线。",
            },
        ]
        # UNKNOWN is a provisional baseline, not a terminal keyword-classifier
        # decision.  Cross-category Skill retrieval gets one chance to recover
        # intent from the complete query and bound runtime before we ask the
        # user for clarification.
        plan = {
            "planner_version": "rules-v3",
            "category": "UNKNOWN",
            "classification_confidence": 0.0,
            "statement": "异常领域尚未明确，需要先采集系统基线或复用匹配的诊断 Skill",
            "expected": ["系统基线或 Skill 路线能够区分下一步应进入的证据域"],
            "falsification": ["没有已发布 Skill 命中且问题仍缺少可观测症状"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }

    available_tools = _available_planner_tools(diagnosis, binding)
    allowed_tools = _category_allowed_tools(plan["category"], available_tools)
    if not allowed_tools:
        allowed_tools = list(available_tools)
    if not allowed_tools:
        raise ValueError("bound Agent exposes no runtime-compatible diagnostic collectors")
    if plan["tool_name"] not in allowed_tools:
        plan["tool_name"] = allowed_tools[0]
        plan["arguments"] = _planner_tool_arguments(plan["tool_name"], target)

    # 已发布技能只提供经过门禁验证的探针顺序先验。环境漂移或类别不匹配
    # 时不会命中，规则规划器仍是可复现的安全兜底。
    skill_activation = _apply_active_planner_skill(
        diagnosis,
        diagnosis_id,
        plan,
        target,
        round_index=1,
        phase="INITIAL_PLAN",
        attempted_tools={item.tool_name for item in existing_calls},
        available_tools=available_tools,
        reuse_existing=True,
    )
    if skill_activation and skill_activation.get("applied", True) is not False:
        correction = skill_activation.get("category_correction") or {}
        selected_category = str(
            skill_activation.get("selected_category")
            or skill_activation.get("category")
            or skill_activation.get("skill_category")
            or (
                correction.get("selected_category")
                if isinstance(correction, dict)
                else ""
            )
            or (correction.get("to") if isinstance(correction, dict) else "")
            or ""
        ).strip().upper()
        if selected_category and selected_category != "UNKNOWN":
            previous_category = str(plan.get("category") or "UNKNOWN")
            plan["category"] = selected_category
            plan["classification_confidence"] = max(
                float(plan.get("classification_confidence") or 0.0),
                float(skill_activation.get("match_score") or 0.0),
            )
            if selected_category != previous_category:
                # The old keyword baseline must not leak a contradictory cause
                # into deterministic fallback after Skill retrieval corrected
                # the route category.
                plan["statement"] = (
                    f"跨类别 Skill 将诊断方向从 {previous_category} 修正为 "
                    f"{selected_category}，该方向仍需本次真实证据验证"
                )
                plan["expected"] = [
                    "Skill 路线采集的新证据能够支持或推翻修正后的候选原因"
                ]
                plan["falsification"] = [
                    "本次可信证据不支持该 Skill 路线，需退出并探索其他证据域"
                ]
            corrected_allowed = _category_allowed_tools(
                selected_category,
                available_tools,
            )
            if corrected_allowed:
                allowed_tools = corrected_allowed

    if questions is not None and not (
        skill_activation and skill_activation.get("applied", True) is not False
    ):
        session = new_session()
        try:
            persisted = _lock_diagnosis(session, diagnosis_id)
            if persisted is None:
                return None
            persisted.clarification_questions_json = questions
            timestamp = now_utc()
            _append_event(
                session,
                diagnosis_id,
                "planner.needs_clarification",
                "SYSTEM",
                {
                    "planner_kind": "SKILL_AWARE_ROUTER",
                    "planner_version": "rules-v3",
                    "category": "UNKNOWN",
                    "classification_confidence": 0.0,
                    "skill_retrieval": "ABSTAINED",
                    "questions": questions,
                },
                timestamp,
            )
            _cas_session_update(
                session,
                persisted,
                status="NEEDS_CLARIFICATION",
                timestamp=timestamp,
            )
            session.commit()
        finally:
            session.close()
        return {
            "planner_kind": "SKILL_AWARE_ROUTER",
            "planner_version": "rules-v3",
            "classification_confidence": 0.0,
            "category": "UNKNOWN",
            "status": "NEEDS_CLARIFICATION",
            "clarification_questions": questions,
            "hypothesis": None,
            "tool_call": None,
        }
    skill_first_tool: str | None = None
    if skill_activation:
        selected_skill_tool = _skill_selected_tool(
            skill_activation,
            available_tools,
        )
        if selected_skill_tool is not None:
            plan["tool_name"] = selected_skill_tool
            if selected_skill_tool not in allowed_tools:
                allowed_tools.insert(0, selected_skill_tool)
            # A published Skill is the treatment-arm route prior for the first
            # real probe.  The model still generates and ranks hypotheses, but
            # must not silently collapse AUTO back onto the control arm by
            # replacing this runtime-compatible first tool.
            skill_first_tool = selected_skill_tool
        plan["arguments"] = _planner_tool_arguments(plan["tool_name"], target)

    # 规则负责范围/工具白名单，模型只在边界内提出和排序可证伪假设。
    # 模型不可用时保留确定性规则结果，且把来源显式展示给用户。
    model_attempted = True
    retrieval_trace = _record_planner_knowledge_retrieval(
        diagnosis_id,
        query=diagnosis.query,
        category=plan["category"],
        phase="INITIAL_PLAN",
        effect_key=f"diagnosis:{diagnosis_id}:initial:knowledge_retrieval",
        round_index=1,
    )
    proposal = propose_hypothesis_plan(
        diagnosis_id=diagnosis_id,
        query=diagnosis.query,
        target=target,
        category=plan["category"],
        rule_plan=plan,
        prior_hypotheses=[item.to_dict() for item in list_hypotheses(diagnosis_id)],
        allowed_tools=allowed_tools,
        route_priors=_successful_tool_route_priors(),
        active_skill=skill_activation,
        retrieval_trace=retrieval_trace,
    )
    if proposal and proposal.get("tool_name") in allowed_tools:
        if skill_first_tool is None:
            plan["tool_name"] = proposal["tool_name"]
        plan["arguments"] = _planner_tool_arguments(plan["tool_name"], target)
        model_candidates = [
            {
                "statement": item["statement"],
                "expected": item["expected_observations"],
                "falsification": item["falsification_criteria"],
                "reason": item.get("rationale") or proposal.get("reasoning_summary", ""),
                "prior_probability": item.get("prior_probability"),
                "estimated_value": item.get("estimated_value"),
            }
            for item in proposal["hypotheses"]
        ]
        generation_reason = proposal.get("reasoning_summary", "模型在策略边界内生成候选假设")
        plan["statement"] = model_candidates[0]["statement"]
        plan["expected"] = model_candidates[0]["expected"]
        plan["falsification"] = model_candidates[0]["falsification"]
        if proposal.get("language_normalization") == "SERVER_RULE_FALLBACK":
            candidates = _candidate_hypotheses(plan["category"], plan)
            source = "SERVER_LANGUAGE_RULE_FALLBACK"
        else:
            candidates = [*model_candidates, _UNKNOWN_HYPOTHESIS]
            source = "MODEL"
    else:
        candidates = _candidate_hypotheses(plan["category"], plan)
        source = "DETERMINISTIC_RULE"
        generation_reason = (
            "模型调用不可用或输出未通过约束校验，使用可复现规则兜底。"
            if model_attempted
            else f"规则分类器已选择 {plan['category']} 诊断路径；该类别当前使用确定性规划。"
        )

    # LATS expansion keeps top-k mutually falsifiable candidates plus the
    # open-world sentinel.  LM scores are priors only; missing/invalid values
    # use deterministic fallbacks in prepare_candidates().
    lats_config = LATSConfig.from_budget(diagnosis.budget_json or {})
    prepared_candidates = prepare_candidates(
        candidates,
        top_k=lats_config.top_k,
        default_tool=plan["tool_name"],
        value_lambda=lats_config.value_lambda,
    )
    durable_candidates: list[dict] = []
    known_by_statement = {
        stable_candidate_key(item.statement): item
        for item in list_hypotheses(diagnosis_id)
    }
    for candidate_index, candidate in enumerate(prepared_candidates):
        candidate_key = candidate["candidate_key"]
        hypothesis_row = known_by_statement.get(candidate_key)
        if hypothesis_row is None:
            hypothesis_row = create_hypothesis(
            diagnosis_id,
            CreateHypothesisRequest(
                statement=candidate["statement"],
                    expected_observations=candidate["expected_observations"],
                    falsification_criteria=candidate["falsification_criteria"],
            ),
                source=(
                    "SYSTEM_FALLBACK"
                    if candidate.get("is_open_world_sentinel")
                    else source
                ),
            round_index=1,
            generation_reason=candidate.get("reason", generation_reason),
            effect_key=(
                f"diagnosis:{diagnosis_id}:initial:hypothesis:{candidate_index}"
            ),
        )
        if hypothesis_row is not None:
            durable_candidates.append(
                {
                    **candidate,
                    "node_id": f"hypothesis:{hypothesis_row.id}",
                    "hypothesis_id": hypothesis_row.id,
                }
            )
            known_by_statement[candidate_key] = hypothesis_row

    selection = _record_lats_expansion_and_selection(
        diagnosis_id,
        durable_candidates,
        phase="INITIAL_EXPANSION",
        round_index=1,
        parent_hypothesis_id=None,
        effect_prefix=f"diagnosis:{diagnosis_id}:initial:lats",
    )
    selected_hypothesis_id = (
        str((selection or {}).get("node_id") or "").removeprefix("hypothesis:")
    )
    hypotheses = list_hypotheses(diagnosis_id)
    hypothesis = next(
        (item for item in hypotheses if item.id == selected_hypothesis_id),
        None,
    )
    if hypothesis is None:
        hypothesis = hypotheses[0] if hypotheses else None
    if hypothesis is None:
        raise ValueError("LATS expansion did not produce an executable hypothesis")
    selected_candidate = next(
        (
            item for item in durable_candidates
            if item.get("hypothesis_id") == hypothesis.id
        ),
        {},
    )
    recommended_tool = selected_candidate.get("recommended_tool")
    if skill_first_tool is not None:
        plan["tool_name"] = skill_first_tool
        plan["arguments"] = _planner_tool_arguments(skill_first_tool, target)
    elif recommended_tool in allowed_tools:
        plan["tool_name"] = recommended_tool
        plan["arguments"] = _planner_tool_arguments(recommended_tool, target)

    # Rebuild the final request after Skill/model/LATS selection so JVM event
    # choice follows the diagnosis intent (allocation, lock, wall or CPU).
    plan["arguments"] = _planner_tool_arguments(
        plan["tool_name"], target, query=diagnosis.query
    )

    existing_calls = list_tool_calls(diagnosis_id)
    tool_call = next(
        (
            item for item in existing_calls
            if item.hypothesis_id == hypothesis.id
            and item.tool_name == plan["tool_name"]
            and item.arguments_json == plan["arguments"]
        ),
        None,
    )
    if tool_call is None:
        tool_call = request_tool_call(
            diagnosis_id,
            CreateToolCallRequest(
                hypothesis_id=hypothesis.id,
                tool_name=plan["tool_name"],
                arguments=plan["arguments"],
            ),
            requested_by=requested_by,
            effect_key=f"diagnosis:{diagnosis_id}:initial:tool_call",
        )
    if tool_call is not None:
        _record_lats_action_dispatched(
            diagnosis_id,
            hypothesis.id,
            tool_call,
            effect_prefix=f"diagnosis:{diagnosis_id}:initial:lats",
        )
    return {
        "planner_kind": (
            "SERVER_RULE_FALLBACK"
            if proposal
            and proposal.get("language_normalization") == "SERVER_RULE_FALLBACK"
            else "LANGGRAPH_AGENT"
            if proposal and proposal.get("agent_framework")
            else "MODEL_ASSISTED"
            if proposal
            else "DETERMINISTIC_RULES"
        ),
        "planner_version": (
            proposal.get("agent_version", plan["planner_version"])
            if proposal
            else plan["planner_version"]
        ),
        "classification_confidence": plan.get("classification_confidence", 0.9),
        "category": plan["category"],
        "decision_source": source,
        "reasoning_summary": generation_reason,
        "retrieval_trace": retrieval_trace,
        "skill_activation": skill_activation,
        "lats_selection": selection,
        "agent_runtime": (
            {
                "framework": proposal.get("agent_framework"),
                "version": proposal.get("agent_version"),
                "checkpoint_backend": proposal.get("checkpoint_backend"),
                "thread_id": diagnosis_id,
                "language_normalization": proposal.get("language_normalization"),
            }
            if proposal and proposal.get("agent_framework")
            else None
        ),
        "hypothesis": hypothesis.to_dict(),
        "tool_call": tool_call.to_dict(),
    }


def _evidence_time_scope(diagnosis: DropInsightSessionModel) -> dict:
    if diagnosis.mode == "REPRODUCTION":
        selected = diagnosis.effective_time_range_json or {}
        scope_name = "effective live"
        if selected.get("state") != "FINALIZED":
            raise ValueError(
                "effective live diagnosis time range must be finalized before evidence import"
            )
    else:
        selected = (
            diagnosis.requested_time_range_json
            or diagnosis.time_range_json
            or {}
        )
        scope_name = "requested"
    if not selected.get("start") or not selected.get("end"):
        raise ValueError(
            f"{scope_name} diagnosis time range must be finalized before evidence import"
        )
    return selected


def import_task_evidence(
    diagnosis_id: str,
    payload: ImportTaskEvidenceRequest,
    *,
    terminal_tool_call_id: str | None = None,
) -> list[DropInsightEvidenceModel] | None:
    session = new_session()
    try:
        diagnosis = _lock_diagnosis(
            session, diagnosis_id, payload.expected_version
        )
        if diagnosis is None:
            return None
        terminal_tool_call = None
        terminal_evidence_already_imported = False
        if terminal_tool_call_id is not None:
            terminal_tool_call = (
                session.query(DropInsightToolCallModel)
                .filter(
                    DropInsightToolCallModel.id == terminal_tool_call_id,
                    DropInsightToolCallModel.diagnosis_id == diagnosis_id,
                )
                .with_for_update()
                .first()
            )
            if terminal_tool_call is None:
                raise ValueError("terminal tool call not found")
            if terminal_tool_call.task_id != payload.task_id:
                raise ValueError("task does not belong to terminal tool call")
            if terminal_tool_call.terminal_processing_status in {
                "EVIDENCE_IMPORTED",
                "REPORT_EFFECTS_DONE",
            }:
                terminal_evidence_already_imported = True
            elif (
                terminal_tool_call.terminal_processing_status
                != "TERMINAL_RECORDED"
            ):
                raise ValueError("task terminal state has not been recorded")
        hypothesis = session.get(DropInsightHypothesisModel, payload.hypothesis_id)
        if hypothesis is None or hypothesis.diagnosis_id != diagnosis_id:
            raise ValueError("hypothesis does not belong to diagnosis")
        task = session.get(TaskModel, payload.task_id)
        if task is None:
            raise ValueError("task not found")
        if task.status != "DONE":
            raise ValueError("only DONE tasks can be imported as evidence")

        binding = _validated_target_binding(session, diagnosis)
        target = diagnosis.target_json or {}
        evidence_time_scope = _evidence_time_scope(diagnosis)
        task_binding = _canonical_binding(task.process_binding_json)
        request_binding = _canonical_binding(
            (task.request_params or {}).get("process_binding")
        )
        canonical_binding = binding.to_dict()
        if (
            task.agent_id != binding.agent_id
            or task.target_pid != binding.pid
            or task.process_snapshot_id != binding.process_snapshot_id
            or task_binding != canonical_binding
            or request_binding != canonical_binding
        ):
            raise ValueError("task does not match diagnosis immutable process binding")

        attempt = (
            session.query(TaskAttemptModel)
            .filter(TaskAttemptModel.task_id == task.id)
            .order_by(TaskAttemptModel.attempt_no.desc())
            .first()
        )
        # TaskAttempt represents collection execution. SUCCEEDED is the
        # canonical terminal state; DONE remains accepted for legacy rows.
        if attempt is None or attempt.status not in {"SUCCEEDED", "DONE"}:
            raise ValueError(
                "该任务没有成功完成的执行批次（TaskAttempt）。"
                "请在任务面板重新创建采集任务，等待采集状态为 SUCCEEDED、分析状态为 SUCCEEDED 后再导入；"
                "历史任务无法补齐真实执行溯源。"
            )
        artifacts = (
            session.query(ArtifactModel)
            .filter(ArtifactModel.task_id == task.id)
            .order_by(ArtifactModel.id.asc())
            .all()
        )
        if not artifacts:
            raise ValueError("task has no artifacts")
        if terminal_evidence_already_imported:
            evidence_ids = [
                f"ev_task_{task.id}_{artifact.id}" for artifact in artifacts
            ]
            return (
                session.query(DropInsightEvidenceModel)
                .filter(DropInsightEvidenceModel.id.in_(evidence_ids))
                .order_by(DropInsightEvidenceModel.id.asc())
                .all()
            )
        successful_jobs = (
            session.query(AnalysisJobModel)
            .filter(
                AnalysisJobModel.task_id == task.id,
                AnalysisJobModel.status == "SUCCEEDED",
            )
            .all()
        )
        validated_artifact_ids = {
            int(artifact_id)
            for job in successful_jobs
            for artifact_id in (job.output_artifact_ids_json or [])
        }
        analysis_jobs_by_artifact = {
            int(artifact_id): job
            for job in successful_jobs
            for artifact_id in (job.output_artifact_ids_json or [])
        }

        imported: list[DropInsightEvidenceModel] = []
        created_evidence = False
        timestamp = now_utc()
        for artifact in artifacts:
            evidence_id = f"ev_task_{task.id}_{artifact.id}"
            existing = session.get(DropInsightEvidenceModel, evidence_id)
            if existing is not None:
                imported.append(existing)
                continue

            metadata = dict(artifact.meta_json or {})
            predicate = _compute_hypothesis_predicate(hypothesis, metadata)
            if predicate is not None:
                metadata["hypothesis_predicate"] = predicate
            analysis_job = analysis_jobs_by_artifact.get(artifact.id)
            assessment = assess_artifact_evidence(
                task.collector_type,
                artifact.artifact_type,
                metadata,
                analyzer_validated=(
                    artifact.id in validated_artifact_ids
                    and artifact.integrity_status == "VERIFIED"
                ),
            )
            sample_count = assessment.sample_count
            degraded = bool(
                metadata.get("degraded")
                or (task.request_params or {}).get("degraded")
                or "degraded" in (task.status_reason or "").lower()
            )
            quality_level = (
                "HIGH"
                if (
                    assessment.sample_count_known
                    and sample_count >= assessment.minimum_samples
                    and not degraded
                    and assessment.schema_valid
                    and assessment.analyzer_validated
                )
                else "MEDIUM"
                if assessment.sample_count_known and sample_count > 0
                else "LOW"
            )
            event_start = task.started_at or task.created_at
            event_end = task.finished_at or timestamp
            envelope = EvidenceEnvelope(
                evidence_id=evidence_id,
                diagnosis_id=diagnosis_id,
                evidence_type=f"{task.collector_type.upper()}_{artifact.artifact_type.upper()}",
                source={
                    "tool_name": task.collector_type,
                    "task_id": task.id,
                    "task_attempt_id": attempt.id,
                    "artifact_id": str(artifact.id),
                    "artifact_sha256": artifact.sha256 or "",
                    "analysis_job_id": analysis_job.id if analysis_job else "",
                    "analyzer_type": analysis_job.analyzer_type if analysis_job else "",
                    "analyzer_version": str(
                        analysis_job.analyzer_version
                        if analysis_job
                        else metadata.get("analyzer_version", "unknown")
                    ),
                    "analyzer_output_schema_version": CONTRACT_VERSION,
                    "observation_json_pointer": "/metadata",
                },
                scope={
                    "agent_id": task.agent_id,
                    "service": target.get("service"),
                    "host_id": target.get("host_id"),
                    "instance_id": target.get("instance_id"),
                    "container_id": target.get("container_id"),
                    "pid": task.target_pid,
                },
                time_range={
                    "start": event_start,
                    "end": event_end,
                    "timezone": evidence_time_scope.get(
                        "timezone",
                        "Asia/Shanghai",
                    ),
                },
                observation={
                    "artifact_type": artifact.artifact_type,
                    "object_key": artifact.object_key,
                    "content_type": artifact.content_type,
                    "size_bytes": artifact.size_bytes,
                    "metadata": metadata,
                },
                quality={
                    "level": quality_level,
                    "sample_count": sample_count,
                    "sample_count_known": assessment.sample_count_known,
                    "degraded": degraded,
                    "target_match": True,
                    "time_overlap": _time_ranges_overlap(
                        event_start,
                        event_end,
                        evidence_time_scope,
                    ),
                    "schema_valid": assessment.schema_valid,
                    "analyzer_validated": assessment.analyzer_validated,
                    "minimum_samples": assessment.minimum_samples,
                },
                limitations=list(assessment.limitations),
            )
            classification = classify_evidence(envelope)
            evidence_role = _derive_imported_evidence_role(
                hypothesis,
                artifact,
                assessment,
                predicate=predicate,
            )
            if classification["can_support_conclusion"]:
                if evidence_role == "COUNTER":
                    classification["decision"] = "ACCEPT_COUNTER"
                elif evidence_role == "NEUTRAL":
                    classification = {
                        "decision": "ACCEPT_NEUTRAL",
                        "can_support_conclusion": False,
                        "reasons": [
                            "Analyzer 未产出能够支持或证伪当前假设的结构化谓词"
                        ],
                    }
            model = DropInsightEvidenceModel(
                id=evidence_id,
                diagnosis_id=diagnosis_id,
                hypothesis_id=payload.hypothesis_id,
                role=evidence_role,
                envelope_json=envelope.model_dump(mode="json"),
                classification_json=classification,
                created_at=timestamp,
            )
            session.add(model)
            imported.append(model)
            created_evidence = True

        if created_evidence:
            _append_event(
                session,
                diagnosis_id,
                "task_evidence.imported",
                "SYSTEM",
                {
                    "task_id": task.id,
                    "task_attempt_id": attempt.id,
                    "evidence_refs": [item.id for item in imported],
                },
                timestamp,
            )
            _cas_session_update(
                session,
                diagnosis,
                status="COLLECTING_EVIDENCE",
                timestamp=timestamp,
            )
        if terminal_tool_call is not None:
            terminal_tool_call.terminal_processing_status = "EVIDENCE_IMPORTED"
        session.commit()
        for item in imported:
            session.refresh(item)
        return imported
    finally:
        session.close()


def clarify_diagnosis(
    diagnosis_id: str,
    payload: ClarifyDiagnosisRequest,
    *,
    actor: str = "USER",
) -> dict | None:
    """Resolve clarification scope from opaque, persisted process authority."""

    session = new_session()
    try:
        diagnosis = _lock_diagnosis(session, diagnosis_id, payload.expected_version)
        if diagnosis is None:
            return None
        timestamp = now_utc()
        previous_target = diagnosis.target_json or {}
        submitted_target = payload.target.model_dump() if payload.target is not None else {}
        discovery_id = submitted_target.get("discovery_id")
        binding_id = submitted_target.get("binding_id")
        if bool(discovery_id) != bool(binding_id):
            raise ValueError("discovery_id and binding_id must be supplied together")

        service = submitted_target.get("service") or previous_target.get("service")
        environment = (
            submitted_target.get("environment") or previous_target.get("environment")
        )
        binding = None
        if discovery_id and binding_id:
            binding = _selected_discovery_binding(
                session,
                diagnosis,
                discovery_id=discovery_id,
                binding_id=binding_id,
                timestamp=timestamp,
            )
            discovery = session.get(DropInsightTargetDiscoveryModel, discovery_id)
            service = service or discovery.service_filter
            environment = environment or discovery.environment_filter
        elif previous_target.get("process_binding"):
            binding = _validated_target_binding(session, diagnosis, now=timestamp)

        replacement_target = {
            key: value
            for key, value in {
                "service": service,
                "environment": environment,
            }.items()
            if value is not None
        }
        if binding is not None:
            replacement_target.update(
                {
                    "agent_id": binding.agent_id,
                    "pid": binding.pid,
                    "process_binding": binding.to_dict(),
                }
            )
        diagnosis.target_json = replacement_target

        submitted_range = (
            payload.time_range.model_dump(mode="json")
            if payload.time_range is not None
            else None
        )
        requested_range = diagnosis.requested_time_range_json or {}
        if not requested_range and diagnosis.time_range_json:
            requested_range = diagnosis.time_range_json or {}
        if submitted_range is not None:
            if requested_range and not _diagnostic_time_ranges_equivalent(
                submitted_range,
                requested_range,
            ):
                raise ValueError(
                    "requested diagnosis time range is immutable once established"
                )
            if not requested_range:
                requested_range = submitted_range
                diagnosis.requested_time_range_json = submitted_range
                diagnosis.time_range_json = submitted_range
            else:
                diagnosis.requested_time_range_json = requested_range
                diagnosis.time_range_json = requested_range
        elif requested_range:
            diagnosis.requested_time_range_json = requested_range
            diagnosis.time_range_json = requested_range

        remaining_questions = _scope_questions(replacement_target, requested_range)
        diagnosis.clarification_questions_json = remaining_questions
        _append_event(
            session,
            diagnosis_id,
            "diagnosis.clarified",
            actor,
            {
                "target": replacement_target,
                "time_range": diagnosis.time_range_json or {},
                "requested_time_range": requested_range,
                "effective_time_range": diagnosis.effective_time_range_json or {},
            },
            timestamp,
        )
        next_status = "NEEDS_CLARIFICATION" if remaining_questions else "UNDERSTANDING"
        _cas_session_update(session, diagnosis, status=next_status, timestamp=timestamp)
        _invalidate_diagnosis_discoveries(
            session,
            diagnosis_id,
            timestamp=timestamp,
        )
        session.commit()
        session.refresh(diagnosis)
        return diagnosis.to_dict()
    except _DiscoveryInvalidationError as exc:
        session.rollback()
        _persist_discovery_invalidation(
            exc.discovery_id,
            diagnosis_id,
            timestamp=timestamp,
        )
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
