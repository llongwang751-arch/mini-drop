from __future__ import annotations

import hashlib
import logging
import os
import re
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import NoReturn
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from server.app.database import new_session
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
)
from server.app.state_machine import now_utc

from .evidence import EvidenceEnvelope, calibrate_confidence, classify_evidence
from .artifact_evidence import assess_artifact_evidence
from .claim_verifier import verify_report_claims
from .policy import PolicyContext, evaluate_tool_call
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
    PreviewToolCallRequest,
    RunPlannerRequest,
    SubmitDiagnosisFeedbackRequest,
    DiagnosticTimeRange,
)
from server.app.schemas import CreateTaskRequest, ProcessIdentityBindingRequest
from server.app.process_attestation import (
    PROCESS_SNAPSHOT_MAX_AGE,
    ProcessIdentityBinding,
)
from server.app.sql_repository import SqlRepository
from server.app.prometheus_metrics import record_evidence_decision
from server.app.diagnosis.source_mapper import map_hot_functions
from .adaptive_planner import propose_hypothesis_plan


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
    "数据库锁", "锁等待", "deadlock", "mysql lock", "postgres lock", "db lock",
)


def _is_database_query(query: str) -> bool:
    lowered = query.casefold()
    return any(token in lowered for token in _DATABASE_QUERY_TOKENS)


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


def _auto_scope_service_filter(query: str) -> str | None:
    """Extract an explicit machine-style service name, when the user gave one."""

    specific = sorted(
        (token for token in _auto_scope_tokens(query) if "-" in token or "." in token),
        key=lambda token: (-len(token), token),
    )
    if specific:
        return specific[0]
    if _is_database_query(query):
        return os.getenv("MINI_DROP_DATABASE_SERVICE", "mini-drop-postgres").strip() or None
    return None


def _select_auto_scope_candidate(query: str, discovery: dict) -> dict | None:
    candidates = [item for item in discovery.get("candidates", []) if item.get("eligible")]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None
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
    ranked = sorted(
        ((_auto_scope_score(query, item), item) for item in candidates),
        key=lambda pair: (-pair[0], str(pair[1].get("binding_id") or "")),
    )
    top_score, top = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else -1
    if top_score >= 20 and top_score > runner_up:
        return top
    return None


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
        selected = _select_auto_scope_candidate(query, discovery)
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


def create_diagnosis(payload: CreateDiagnosisRequestV2) -> DropInsightSessionModel:
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
        payload_json={"status": status},
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
            limitations.append("No accepted supporting evidence; conclusion is not established.")
        elif not verification["has_independent_counter_or_control"]:
            limitations.append(
                "缺少独立反证或对照证据；结论已完成，但仍应在修复复测中补充独立验证。"
            )

        source_symbols = _extract_source_symbols(supporting + counter)
        verification["source_context"] = map_hot_functions(source_symbols)

        conclusion = _derive_report_conclusion(
            hypothesis.statement,
            support_refs=support_refs,
            counter_refs=counter_refs,
        )
        assumptions = ["结论仅适用于当前诊断目标与时间窗口"]
        next_actions = _derive_next_actions(
            support_refs=support_refs,
            counter_refs=counter_refs,
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
            confidence=round(confidence * 1000),
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
        next_status = (
            "COMPLETED"
            if support_refs and confidence >= 0.6
            else "COLLECTING_EVIDENCE"
            if support_refs
            else "INSUFFICIENT_EVIDENCE"
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
                elif verification_status in {"VERIFIED", "PARTIAL_WITHOUT_COUNTER"}:
                    _record_successful_route(diagnosis_id, report_id)
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
    previous = list_hypotheses(diagnosis_id)
    parent = next((item for item in previous if item.id == feedback.hypothesis_id), None)
    round_index = max([item.round_index or 1 for item in previous] or [1]) + 1
    correction = feedback.corrected_cause or feedback.feedback_note or "用户认为上一轮结论不完整"
    baseline = {
        "statement": correction,
        "expected": ["新采集证据与用户纠正的原因在同一目标和时间窗内一致"],
        "falsification": ["补充证据与该纠正原因不一致或出现更强反证"],
        "tool_name": _feedback_tool(correction, parent),
    }
    model_attempted = True
    proposal = propose_hypothesis_plan(
        query=diagnosis.query,
        target=target,
        category="HUMAN_CORRECTION",
        rule_plan=baseline,
        prior_hypotheses=[
            {"statement": item.statement, "status": item.status, "round": item.round_index}
            for item in previous
        ],
        user_correction=correction,
        allowed_tools=[
            "collect_sys_metrics", "start_perf_profile", "start_ebpf_io_profile",
            "start_pyspy_profile", "collect_database_diagnostics",
        ],
        route_priors=_successful_tool_route_priors(),
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
    _current_target_binding(diagnosis)
    tool_name = (proposal or {}).get("tool_name") or baseline["tool_name"]
    request_tool_call(
        diagnosis_id,
        CreateToolCallRequest(
            hypothesis_id=revision.id,
            tool_name=tool_name,
            arguments=_planner_tool_arguments(tool_name, target),
        ),
        requested_by="system:adaptive-replanner",
    )
    return revision


def _feedback_tool(correction: str, parent: DropInsightHypothesisModel | None) -> str:
    text = correction.lower()
    if any(token in text for token in _DATABASE_QUERY_TOKENS):
        return "collect_database_diagnostics"
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


def _planner_tool_arguments(tool_name: str, target: dict) -> dict:
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
    if tool_name in {"start_perf_profile", "start_pyspy_profile"}:
        arguments["sample_rate"] = 99
    return arguments


def _replan_from_counter_evidence(
    diagnosis_id: str,
    parent_hypothesis_id: str,
    report_id: str,
) -> DropInsightHypothesisModel | None:
    """Open a bounded new round when trusted evidence falsifies the primary hypothesis."""
    diagnosis = get_diagnosis(diagnosis_id)
    if diagnosis is None:
        return None
    previous = list_hypotheses(diagnosis_id)
    parent = next((item for item in previous if item.id == parent_hypothesis_id), None)
    if parent is None:
        return None
    round_index = (parent.round_index or 1) + 1
    max_rounds = int((diagnosis.budget_json or {}).get("max_diagnosis_rounds", 6))
    if round_index > max_rounds:
        return None
    target = diagnosis.target_json or {}
    statement = (
        f"第 {round_index} 轮：可信反证已推翻上一轮主假设，"
        "需验证同一时间窗内的替代资源或依赖原因"
    )
    tool_name = "collect_sys_metrics" if any(
        token in parent.statement.lower() for token in ("cpu", "热点", "python", "io")
    ) else "start_perf_profile"
    baseline = {
        "statement": statement,
        "expected": ["补充证据能解释上一轮未覆盖的异常范围"],
        "falsification": ["补充指标平稳且不能解释故障现象"],
        "tool_name": tool_name,
    }
    proposal = None
    if not _REPORT_EFFECT_RECONCILIATION.get():
        proposal = propose_hypothesis_plan(
            query=diagnosis.query,
            target=target,
            category="COUNTER_EVIDENCE_REPLAN",
            rule_plan=baseline,
            prior_hypotheses=[item.to_dict() for item in previous],
            user_correction="可信反证已推翻上一轮主假设",
            allowed_tools=["collect_sys_metrics", "start_perf_profile"],
            route_priors=_successful_tool_route_priors(),
        )
    candidate = (proposal or {}).get("hypotheses", [{}])[0]
    revision = create_hypothesis(
        diagnosis_id,
        CreateHypothesisRequest(
            statement=candidate.get("statement") or statement,
            expected_observations=candidate.get("expected_observations") or baseline["expected"],
            falsification_criteria=candidate.get("falsification_criteria") or baseline["falsification"],
        ),
        source="MODEL_REPLAN" if proposal else "COUNTER_EVIDENCE_RULE",
        round_index=round_index,
        parent_hypothesis_id=parent.id,
        generation_reason=(proposal or {}).get("reasoning_summary")
        or "可信反证推翻上一轮主假设，自动进入下一轮互补取证。",
        effect_key=f"report:{report_id}:counter:hypothesis",
    )
    if revision is not None:
        _current_target_binding(diagnosis)
        existing_call = _tool_call_by_effect_key(
            diagnosis_id,
            f"report:{report_id}:counter:tool_call",
        )
        selected_tool = (
            existing_call.tool_name
            if existing_call is not None
            else (proposal or {}).get("tool_name") or tool_name
        )
        call = request_tool_call(
            diagnosis_id,
            CreateToolCallRequest(
                hypothesis_id=revision.id,
                tool_name=selected_tool,
                arguments=_planner_tool_arguments(selected_tool, target),
            ),
            requested_by="system:counter-evidence-replanner",
            effect_key=f"report:{report_id}:counter:tool_call",
        )
        if call is not None:
            session = new_session()
            try:
                timestamp = now_utc()
                created = _append_event(
                    session,
                    diagnosis_id,
                    "planner.counter_replanned",
                    "SYSTEM",
                    {
                        "round_index": revision.round_index,
                        "previous_hypothesis_id": parent.id,
                        "hypothesis_id": revision.id,
                        "tool_name": call.tool_name,
                        "planner_kind": (
                            "MODEL_ASSISTED"
                            if proposal
                            else "DETERMINISTIC_FALLBACK"
                        ),
                        "reason": revision.generation_reason,
                        "requires_approval": (
                            call.policy_decision == "REQUIRE_APPROVAL"
                        ),
                    },
                    timestamp,
                    effect_key=f"report:{report_id}:counter:event",
                )
                if created:
                    persisted = _lock_diagnosis(session, diagnosis_id)
                    if persisted is not None:
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


def _replan_after_insufficient_evidence(
    diagnosis_id: str,
    parent_hypothesis_id: str,
    report_id: str,
) -> DropInsightHypothesisModel | None:
    """证据不足时自动换证据域，而不是立即把诊断交还给用户。"""
    diagnosis = get_diagnosis(diagnosis_id)
    if diagnosis is None:
        return None
    target = diagnosis.target_json or {}
    binding = _current_target_binding(diagnosis)
    capability_by_tool = {
        "collect_sys_metrics": "sys_metrics",
        "start_perf_profile": "perf_cpu",
        "start_ebpf_io_profile": "ebpf_io",
        "start_pyspy_profile": "pyspy",
        "collect_database_diagnostics": "database_lock",
    }
    session = new_session()
    try:
        agent = session.get(AgentModel, binding.agent_id)
        if agent is None or agent.status != "ONLINE":
            return None
        capabilities = set(agent.capabilities or [])
    finally:
        session.close()
    previous = list_hypotheses(diagnosis_id)
    parent = next((item for item in previous if item.id == parent_hypothesis_id), None)
    if parent is None:
        return None
    round_index = (parent.round_index or 1) + 1
    max_rounds = int((diagnosis.budget_json or {}).get("max_diagnosis_rounds", 6))
    if round_index > max_rounds:
        return None
    attempted = {item.tool_name for item in list_tool_calls(diagnosis_id)}
    all_tools = [
        "collect_sys_metrics", "start_perf_profile", "start_ebpf_io_profile",
        "start_pyspy_profile", "collect_database_diagnostics",
    ]
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
        and capability_by_tool[item] in capabilities
    ]
    if not remaining:
        return None
    fallback_tool = (
        existing_call.tool_name if existing_call is not None else remaining[0]
    )
    baseline = {
        "statement": (
            f"第 {round_index} 轮：上一证据域不足以建立结论，"
            "需切换证据域继续定位"
        ),
        "expected": ["新的独立采集结果能够支持或推翻至少一个候选假设"],
        "falsification": ["补充证据仍无区分力，或目标能力不支持该采集器"],
        "tool_name": fallback_tool,
    }
    proposal = None
    if not _REPORT_EFFECT_RECONCILIATION.get():
        proposal = propose_hypothesis_plan(
            query=diagnosis.query,
            target=target,
            category="INSUFFICIENT_EVIDENCE_REPLAN",
            rule_plan=baseline,
            prior_hypotheses=[item.to_dict() for item in previous],
            evidence_summary=[{
                "result": "insufficient_evidence",
                "attempted_tools": sorted(attempted),
            }],
            allowed_tools=remaining,
            route_priors=_successful_tool_route_priors(),
        )
    candidate = (proposal or {}).get("hypotheses", [{}])[0]
    reason = (proposal or {}).get("reasoning_summary") or (
        "上一证据域不足以建立结论，按剩余注册工具和历史成功路线切换取证方向。"
    )
    revision = create_hypothesis(
        diagnosis_id,
        CreateHypothesisRequest(
            statement=candidate.get("statement") or baseline["statement"],
            expected_observations=candidate.get("expected_observations") or baseline["expected"],
            falsification_criteria=candidate.get("falsification_criteria") or baseline["falsification"],
        ),
        source="MODEL_REPLAN" if proposal else "AUTONOMOUS_RULE_FALLBACK",
        round_index=round_index,
        parent_hypothesis_id=parent.id,
        generation_reason=reason,
        effect_key=f"report:{report_id}:insufficient:hypothesis",
    )
    if revision is None:
        return None
    selected_tool = (
        existing_call.tool_name
        if existing_call is not None
        else (proposal or {}).get("tool_name") or fallback_tool
    )
    call = request_tool_call(
        diagnosis_id,
        CreateToolCallRequest(
            hypothesis_id=revision.id,
            tool_name=selected_tool,
            arguments=_planner_tool_arguments(selected_tool, target),
        ),
        requested_by="system:insufficient-evidence-replanner",
        effect_key=f"report:{report_id}:insufficient:tool_call",
    )
    if call is None:
        return revision
    session = new_session()
    try:
        timestamp = now_utc()
        created = _append_event(
            session,
            diagnosis_id,
            "planner.insufficient_replanned",
            "SYSTEM",
            {
                "round_index": revision.round_index,
                "previous_hypothesis_id": parent.id,
                "hypothesis_id": revision.id,
                "tool_name": call.tool_name,
                "planner_kind": "MODEL_ASSISTED" if proposal else "DETERMINISTIC_FALLBACK",
                "reason": revision.generation_reason,
                "requires_approval": call.policy_decision == "REQUIRE_APPROVAL",
            },
            timestamp,
            effect_key=f"report:{report_id}:insufficient:event",
        )
        if created:
            persisted = _lock_diagnosis(session, diagnosis_id)
            if persisted is not None:
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
        )
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
        return _execute_approved_tool_call(tool_call_id)
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
        # A high-confidence accepted support report is a valid terminal result.
        # Independent counter/control remains a limitation and fix-verification
        # recommendation, not a reason to leave the UI spinning forever.
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
            seen.add(report.diagnosis_id)
            diagnosis = _lock_diagnosis(session, report.diagnosis_id)
            if diagnosis is None or diagnosis.status != "COLLECTING_EVIDENCE":
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
                    _cas_session_update(
                        session,
                        diagnosis,
                        status="INSUFFICIENT_EVIDENCE",
                        timestamp=now,
                    )
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
                tool_call.status = task_status
                tool_call.result_json = {
                    "task_status": task_status,
                    "reason": task.status_reason,
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
                _cas_session_update(
                    session,
                    diagnosis,
                    status="INSUFFICIENT_EVIDENCE",
                    timestamp=timestamp,
                )
                tool_call.terminal_processing_status = "REPORT_EFFECTS_DONE"
                tool_call.terminal_processed_at = timestamp
                session.commit()
                actions.append({
                    "tool_call_id": tool_call.id,
                    "task_id": task.id,
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
    failed = []
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
        binding = _validated_target_binding(session, diagnosis, now=timestamp)
        if arguments.get("agent_id") != binding.agent_id:
            raise ValueError("tool call Agent disagrees with process binding")
        if arguments.get("pid") is not None and arguments.get("pid") != binding.pid:
            raise ValueError("tool call PID disagrees with process binding")
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

        collector_type = {
            "collect_sys_metrics": "sys_metrics",
            "start_perf_profile": "perf_cpu",
            "start_ebpf_io_profile": "ebpf_io",
            "start_pyspy_profile": "pyspy",
            "collect_database_diagnostics": "database_lock",
        }.get(model.tool_name)
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
            },
            process_binding=_binding_request(binding),
        )
        task = SqlRepository().create_task_in_session(
            session,
            task_request,
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
        collector_type = {
            "collect_sys_metrics": "sys_metrics",
            "start_perf_profile": "perf_cpu",
            "start_ebpf_io_profile": "ebpf_io",
            "start_pyspy_profile": "pyspy",
            "collect_database_diagnostics": "database_lock",
        }.get(model.tool_name)
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
        hypotheses = (
            session.query(DropInsightHypothesisModel)
            .filter(DropInsightHypothesisModel.diagnosis_id == diagnosis_id)
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
                hypothesis.status = "SUPPORTED"
            elif predicate and predicate["outcome"] == "COUNTER":
                hypothesis.status = "COUNTER"
            else:
                hypothesis.status = "INCONCLUSIVE"
            hypothesis.updated_at = timestamp
            changed.append({"hypothesis_id": hypothesis.id, "status": hypothesis.status})
        if changed:
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
        return {
            "planner_kind": "IDEMPOTENT_REPLAY",
            "planner_version": "rules-v2",
            "classification_confidence": 1.0,
            "category": "EXISTING_PLAN",
            "decision_source": primary.source,
            "reasoning_summary": "返回已持久化的诊断计划；重复请求不会创建新假设。",
            "hypothesis": primary.to_dict(),
            "tool_call": existing_calls[0].to_dict(),
        }

    query = (diagnosis.query or "").lower()
    triage_arguments = _planner_tool_arguments("collect_sys_metrics", target)
    if any(token in query for token in ("数据库锁", "锁等待", "deadlock", "mysql lock", "db lock")):
        plan = {
            "planner_version": "rules-v2",
            "category": "DATABASE_LOCK",
            "statement": "请求变慢可能与数据库锁等待或连接阻塞有关",
            "expected": ["数据库快照存在等待锁的会话", "阻塞关系能够指向至少一个 blocker"],
            "falsification": ["系统资源平稳且数据库锁等待快照为空"],
            "tool_name": "collect_database_diagnostics",
            "arguments": _planner_tool_arguments("collect_database_diagnostics", target),
        }
    elif any(token in query for token in ("丢包", "packet loss", "网络抖动", "重传", "timeout", "超时")):
        plan = {
            "planner_version": "rules-v2",
            "category": "NETWORK_DEGRADATION",
            "statement": "服务异常可能由网络丢包、重传或连接超时引起",
            "expected": ["系统初筛显示网络或等待指标异常，需继续采集连接与重传证据"],
            "falsification": ["同窗口网络指标和对照实例均正常"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
        }
    elif any(token in query for token in ("jvm", "gc", "full gc", "垃圾回收")):
        plan = {
            "planner_version": "rules-v2",
            "category": "JVM_GC",
            "statement": "Java 服务停顿可能与 GC 压力或堆内存波动有关",
            "expected": ["系统初筛显示 CPU、RSS 或停顿窗口异常，需继续采集 JVM 证据"],
            "falsification": ["GC、堆和系统资源在同窗口均处于基线范围"],
            "tool_name": "collect_sys_metrics",
            "arguments": triage_arguments,
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
                    "planner_kind": "DETERMINISTIC_RULES",
                    "planner_version": "rules-v2",
                    "category": "UNKNOWN",
                    "classification_confidence": 0.0,
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
            "planner_kind": "DETERMINISTIC_RULES",
            "planner_version": "rules-v2",
            "classification_confidence": 0.0,
            "category": "UNKNOWN",
            "status": "NEEDS_CLARIFICATION",
            "clarification_questions": questions,
            "hypothesis": None,
            "tool_call": None,
        }

    # 已发布技能只提供经过门禁验证的探针顺序先验。环境漂移或类别不匹配
    # 时不会命中，规则规划器仍是可复现的安全兜底。
    from .skill_evolution import apply_active_skill

    skill_activation = apply_active_skill(
        diagnosis_id, plan["category"], plan, target
    )
    if skill_activation:
        plan["arguments"] = _planner_tool_arguments(plan["tool_name"], target)

    # 规则负责范围/工具白名单，模型只在边界内提出和排序可证伪假设。
    # 模型不可用时保留确定性规则结果，且把来源显式展示给用户。
    model_attempted = True
    proposal = propose_hypothesis_plan(
        query=diagnosis.query,
        target=target,
        category=plan["category"],
        rule_plan=plan,
        prior_hypotheses=[item.to_dict() for item in list_hypotheses(diagnosis_id)],
        allowed_tools=[plan["tool_name"]],
        route_priors=_successful_tool_route_priors(),
    )
    if proposal:
        plan["tool_name"] = proposal["tool_name"]
        plan["arguments"] = _planner_tool_arguments(plan["tool_name"], target)
        model_candidates = [
            {
                "statement": item["statement"],
                "expected": item["expected_observations"],
                "falsification": item["falsification_criteria"],
                "reason": item.get("rationale") or proposal.get("reasoning_summary", ""),
            }
            for item in proposal["hypotheses"]
        ]
        candidates = [*model_candidates, _UNKNOWN_HYPOTHESIS]
        source = "MODEL"
        generation_reason = proposal.get("reasoning_summary", "模型在策略边界内生成候选假设")
        plan["statement"] = model_candidates[0]["statement"]
        plan["expected"] = model_candidates[0]["expected"]
        plan["falsification"] = model_candidates[0]["falsification"]
    else:
        candidates = _candidate_hypotheses(plan["category"], plan)
        source = "DETERMINISTIC_RULE"
        generation_reason = (
            "模型调用不可用或输出未通过约束校验，使用可复现规则兜底。"
            if model_attempted
            else f"规则分类器已选择 {plan['category']} 诊断路径；该类别当前使用确定性规划。"
        )

    # 方案 §5.2：除主假设外，同时保留备选假设与 OTHER/UNKNOWN，
    # 避免假设成为答案边界。主假设仍驱动后续工具调用与报告生成。
    for candidate in candidates:
        if any(item.statement == candidate["statement"] for item in list_hypotheses(diagnosis_id)):
            continue
        create_hypothesis(
            diagnosis_id,
            CreateHypothesisRequest(
                statement=candidate["statement"],
                expected_observations=candidate["expected"],
                falsification_criteria=candidate["falsification"],
            ),
            source=source if candidate is not _UNKNOWN_HYPOTHESIS else "SYSTEM_FALLBACK",
            round_index=1,
            generation_reason=candidate.get("reason", generation_reason),
        )
    hypotheses = list_hypotheses(diagnosis_id)
    hypothesis = next(
        (item for item in hypotheses if item.statement == plan["statement"]),
        hypotheses[0] if hypotheses else None,
    )
    if hypothesis is None:
        hypothesis = create_hypothesis(
            diagnosis_id,
            CreateHypothesisRequest(
                statement=plan["statement"],
                expected_observations=plan.get("expected", []),
                falsification_criteria=plan.get("falsification", []),
            ),
            source=source,
            round_index=1,
            generation_reason=generation_reason,
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
        )
    return {
        "planner_kind": "MODEL_ASSISTED" if proposal else "DETERMINISTIC_RULES",
        "planner_version": plan["planner_version"],
        "classification_confidence": plan.get("classification_confidence", 0.9),
        "category": plan["category"],
        "decision_source": source,
        "reasoning_summary": generation_reason,
        "skill_activation": skill_activation,
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
            record_evidence_decision(
                classification["decision"],
                task.collector_type,
                artifact.artifact_type,
            )
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


def _append_event(
    session,
    diagnosis_id: str,
    event_type: str,
    actor: str,
    payload: dict,
    timestamp,
    *,
    effect_key: str | None = None,
) -> bool:
    session.execute(
        select(DropInsightSessionModel.id)
        .where(DropInsightSessionModel.id == diagnosis_id)
        .with_for_update()
    ).scalar_one()
    if effect_key:
        existing = session.execute(
            select(DropInsightEventModel.id).where(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.effect_key == effect_key,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return False
    current = session.execute(
        select(func.max(DropInsightEventModel.sequence)).where(
            DropInsightEventModel.diagnosis_id == diagnosis_id
        )
    ).scalar_one()
    sequence = int(current or 0) + 1
    event = DropInsightEventModel(
        id=f"event_{uuid4().hex}",
        diagnosis_id=diagnosis_id,
        sequence=sequence,
        event_type=event_type,
        actor=actor,
        payload_json=payload,
        effect_key=effect_key,
        occurred_at=timestamp,
    )
    session.add(event)
    _enqueue_diagnosis_event(session, event)
    return True


def _enqueue_diagnosis_event(session, event: DropInsightEventModel) -> None:
    """Persist SSE publication in the same transaction as the domain event."""
    timestamp = event.occurred_at
    session.add(
        OutboxMessageModel(
            id=f"outbox_{event.id}",
            aggregate_type="diagnosis",
            aggregate_id=event.diagnosis_id,
            event_type=event.event_type,
            payload_json={
                "event_id": event.id,
                "sequence": event.sequence,
                "event_type": event.event_type,
                "actor": event.actor,
                "payload": event.payload_json or {},
                "occurred_at": timestamp.isoformat(),
            },
            status="PENDING",
            attempts=0,
            next_attempt_at=timestamp,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )


def _lock_diagnosis(session, diagnosis_id: str, expected_version: int | None = None):
    diagnosis = (
        session.query(DropInsightSessionModel)
        .filter(DropInsightSessionModel.id == diagnosis_id)
        .with_for_update()
        .first()
    )
    if diagnosis is not None and expected_version is not None:
        if diagnosis.version != expected_version:
            raise ValueError(
                f"diagnosis version conflict: expected={expected_version}, "
                f"actual={diagnosis.version}"
            )
    return diagnosis


_SESSION_TRANSITIONS = {
    "NEEDS_CLARIFICATION": {"UNDERSTANDING", "HYPOTHESIZING", "NEEDS_CLARIFICATION"},
    "UNDERSTANDING": {"PLANNING", "HYPOTHESIZING", "NEEDS_CLARIFICATION", "UNDERSTANDING"},
    "PLANNING": {"HYPOTHESIZING", "COLLECTING_EVIDENCE", "INSUFFICIENT_EVIDENCE", "PLANNING"},
    "HYPOTHESIZING": {"PLANNING", "COLLECTING_EVIDENCE", "INSUFFICIENT_EVIDENCE", "HYPOTHESIZING"},
    "COLLECTING_EVIDENCE": {"HYPOTHESIZING", "INSUFFICIENT_EVIDENCE", "COMPLETED", "COLLECTING_EVIDENCE"},
    "INSUFFICIENT_EVIDENCE": {"HYPOTHESIZING", "PLANNING", "COLLECTING_EVIDENCE", "INSUFFICIENT_EVIDENCE"},
    "COMPLETED": {"COMPLETED"},
}


def _cas_session_update(session, diagnosis, *, status: str, timestamp) -> None:
    allowed = _SESSION_TRANSITIONS.get(diagnosis.status, set())
    if status not in allowed:
        raise ValueError(
            f"illegal diagnosis status transition: {diagnosis.status}->{status}"
        )
    expected_version = diagnosis.version
    result = session.execute(
        update(DropInsightSessionModel)
        .where(
            DropInsightSessionModel.id == diagnosis.id,
            DropInsightSessionModel.version == expected_version,
        )
        .values(
            status=status,
            updated_at=timestamp,
            version=expected_version + 1,
        )
    )
    if result.rowcount != 1:
        raise ValueError(
            f"diagnosis version conflict: expected={expected_version}"
        )
    diagnosis.status = status
    diagnosis.updated_at = timestamp
    diagnosis.version = expected_version + 1


def _derive_report_conclusion(
    hypothesis_statement: str,
    *,
    support_refs: list[str],
    counter_refs: list[str],
) -> str:
    """Create the authoritative conclusion from accepted server evidence."""

    if not support_refs:
        if counter_refs:
            return (
                "INSUFFICIENT_EVIDENCE：现有可信证据未支持该假设，且存在反证；"
                f"暂不接受假设“{hypothesis_statement}”。"
            )
        return (
            "INSUFFICIENT_EVIDENCE：当前没有能够支持该假设的可信证据；"
            f"假设“{hypothesis_statement}”仍待验证。"
        )
    if counter_refs:
        return (
            "MIXED_EVIDENCE：可信证据部分支持该假设，同时存在反证；"
            f"假设“{hypothesis_statement}”需要继续证伪。"
        )
    return f"SUPPORTED：可信采集证据支持假设“{hypothesis_statement}”。"


def _derive_next_actions(
    *,
    support_refs: list[str],
    counter_refs: list[str],
) -> list[str]:
    if not support_refs:
        return ["补充同一目标、同一时间窗口且经过 Analyzer 验证的结构化证据"]
    if counter_refs:
        return ["针对冲突证据执行独立的证伪采集，并比较同窗口结果"]
    return ["在相同负载下执行修复前后复测，确认热点和副作用变化"]


def _compute_hypothesis_predicate(
    hypothesis: DropInsightHypothesisModel,
    metadata: dict,
) -> dict | None:
    """Deterministically evaluate analyzer output against the hypothesis plan.

    Produces a normalized predicate: a top function matching an expected
    observation -> SUPPORT; matching a falsification criterion -> COUNTER;
    otherwise None (the caller keeps the artifact NEUTRAL). This is what makes
    the counter-evidence gate reachable: without a COUNTER path, no imported
    artifact can ever satisfy ``has_independent_counter_or_control``.
    """
    expected = hypothesis.expected_observations_json or []
    falsification = hypothesis.falsification_criteria_json or []
    statement = str(hypothesis.statement or "").casefold()

    if str(metadata.get("schema_version") or "").startswith("database_lock."):
        lock_wait_count = max(0, int(metadata.get("lock_wait_count") or 0))
        blocker_count = max(0, int(metadata.get("blocker_count") or 0))
        blocking_edge_count = max(
            0,
            int(
                metadata.get("blocking_edge_count")
                or min(lock_wait_count, blocker_count)
            ),
        )
        max_wait_ms = max(0.0, float(metadata.get("max_wait_ms") or 0.0))
        database_hypothesis = any(token in statement for token in (
            "数据库", "锁等待", "阻塞", "deadlock", "database lock", "db lock",
        ))
        if database_hypothesis and lock_wait_count > 0 and blocker_count > 0:
            covered_count = 3 if blocking_edge_count > 0 else 2
            covered = list(range(min(covered_count, len(expected)))) or [0]
            return {
                "outcome": "SUPPORT",
                "version": "hypothesis-predicate-v2",
                "reason": (
                    f"observed {lock_wait_count} lock-waiting session(s), "
                    f"{blocker_count} blocker(s), max wait {max_wait_ms:.1f} ms"
                ),
                "criterion_indexes": covered,
                "metrics": {
                    "lock_wait_count": lock_wait_count,
                    "blocker_count": blocker_count,
                    "blocking_edge_count": blocking_edge_count,
                    "lock_wait_ms": max_wait_ms,
                },
            }
        if database_hypothesis and lock_wait_count == 0:
            return {
                "outcome": "COUNTER",
                "version": "hypothesis-predicate-v2",
                "reason": "bounded database snapshots contained no lock-waiting sessions",
                "criterion_indexes": [0] if falsification else [],
                "metrics": {
                    "lock_wait_count": 0,
                    "blocker_count": 0,
                    "lock_wait_ms": 0.0,
                },
            }

    top_functions = metadata.get("top_functions")
    if not isinstance(top_functions, list):
        return None
    raw_named = [
        row
        for row in top_functions
        if isinstance(row, dict)
        and isinstance(row.get("name"), str)
        and row["name"].strip()
    ]
    if not raw_named:
        return None
    # Source-aware analyzers intentionally keep one TopN row per file/line.
    # Hypothesis scoring, however, reasons about functions.  A hot function
    # sampled on several executable lines must not be mistaken for several
    # unrelated weak hotspots (for example 40% + 25% + 10% in one loop).
    aggregated: dict[str, dict] = {}
    for row in raw_named:
        name = row["name"].strip()
        current = aggregated.setdefault(
            name,
            {"name": name, "percent": 0.0, "samples": 0, "locations": []},
        )
        current["percent"] += _safe_percent(row.get("percent"))
        try:
            current["samples"] += max(0, int(row.get("samples") or 0))
        except (TypeError, ValueError):
            pass
        if row.get("file") or row.get("line"):
            current["locations"].append({
                "file": row.get("file"),
                "line": row.get("line"),
                "percent": _safe_percent(row.get("percent")),
            })
    named = list(aggregated.values())
    def _percent(row: dict) -> float:
        return _safe_percent(row.get("percent"))

    def _is_kernel(name: str) -> bool:
        value = name.casefold().strip()
        markers = (
            "[kernel", "vmlinux", "__x64_sys_", "do_syscall_", "entry_syscall_",
            "schedule", "finish_task_switch", "irq", "softirq", "kworker",
        )
        return any(marker in value for marker in markers)

    def _is_lock(name: str) -> bool:
        value = name.casefold()
        return any(marker in value for marker in (
            "pthread_mutex", "futex", "spin_lock", "spinlock", "mutex_lock",
            "rwsem", "sem_wait", "lock_slowpath",
        ))

    def _predicate(outcome: str, reason: str, indexes: list[int], **metrics):
        return {
            "outcome": outcome,
            "version": "hypothesis-predicate-v2",
            "reason": reason,
            "criterion_indexes": indexes,
            "metrics": metrics,
        }

    significant = [row for row in named if _percent(row) >= 20.0]
    user_rows = [row for row in named if not _is_kernel(str(row["name"]))]
    kernel_rows = [row for row in named if _is_kernel(str(row["name"]))]
    lock_rows = [row for row in named if _is_lock(str(row["name"]))]
    dominant_user = max(user_rows, key=_percent, default=None)
    dominant_kernel = max(kernel_rows, key=_percent, default=None)
    dominant_user_pct = _percent(dominant_user) if dominant_user else 0.0
    dominant_kernel_pct = _percent(dominant_kernel) if dominant_kernel else 0.0

    hypothesis_text = " ".join(
        [statement, *(str(item).casefold() for item in expected if isinstance(item, str))]
    )
    user_hypothesis = any(token in hypothesis_text for token in (
        "用户态", "业务代码", "热点函数", "python hotspot",
        "hot function", "user-space", "userspace", "函数集中", "样本集中",
    )) or (
        "python" in hypothesis_text
        and "函数" in hypothesis_text
        and any(token in hypothesis_text for token in ("集中", "热点", "占比"))
    )
    # A GIL hypothesis often mentions a single hotspot in its falsification
    # wording.  The causal subject is still GIL contention and must be scored
    # before the generic user-hotspot branch.
    gil_hypothesis = "gil" in statement
    kernel_hypothesis = any(token in statement for token in (
        "内核态", "系统调用", "中断", "kernel", "syscall",
    ))
    lock_hypothesis = any(token in statement for token in (
        "锁竞争", "自旋", "lock contention", "spin",
    ))

    # Planner prose describes signal classes rather than concrete symbols.
    # Turn the Analyzer's TopN distribution into an explicit, auditable
    # predicate so high-quality data is not incorrectly left neutral.
    if gil_hypothesis:
        if dominant_user and dominant_user_pct >= 60.0 and 1 <= len(significant) <= 3:
            return _predicate(
                "COUNTER",
                f"single dominant hotspot {dominant_user['name']} at "
                f"{dominant_user_pct:.1f}% contradicts a GIL-contention explanation",
                [0, 1],
                dominant_function=dominant_user["name"],
                dominant_percent=dominant_user_pct,
                significant_hotspot_count=len(significant),
            )
        return _predicate(
            "NEUTRAL",
            "TopN function distribution alone does not establish GIL contention",
            [],
        )
    if user_hypothesis:
        if dominant_user and dominant_user_pct >= 60.0 and 1 <= len(significant) <= 3:
            return _predicate(
                "SUPPORT",
                f"dominant user-space hotspot {dominant_user['name']} accounts for "
                f"{dominant_user_pct:.1f}% with {len(significant)} significant hotspot(s)",
                [0, 1],
                dominant_function=dominant_user["name"],
                dominant_percent=dominant_user_pct,
                significant_hotspot_count=len(significant),
            )
        if dominant_kernel and dominant_kernel_pct >= 40.0 and dominant_user_pct < 40.0:
            return _predicate(
                "COUNTER",
                f"kernel hotspot {dominant_kernel['name']} dominates at {dominant_kernel_pct:.1f}%",
                [0],
                dominant_function=dominant_kernel["name"],
                dominant_percent=dominant_kernel_pct,
            )

    if kernel_hypothesis:
        if dominant_kernel and dominant_kernel_pct >= 40.0:
            return _predicate(
                "SUPPORT",
                f"kernel/syscall hotspot {dominant_kernel['name']} accounts for {dominant_kernel_pct:.1f}%",
                [0],
                dominant_function=dominant_kernel["name"],
                dominant_percent=dominant_kernel_pct,
            )
        if dominant_user and dominant_user_pct >= 60.0 and dominant_kernel_pct < 20.0:
            return _predicate(
                "COUNTER",
                f"user-space hotspot {dominant_user['name']} dominates while no kernel hotspot reaches 20%",
                [0],
                dominant_function=dominant_user["name"],
                dominant_percent=dominant_user_pct,
            )

    if lock_hypothesis:
        dominant_lock = max(lock_rows, key=_percent, default=None)
        if dominant_lock and _percent(dominant_lock) >= 5.0:
            return _predicate(
                "SUPPORT",
                f"lock-related hotspot {dominant_lock['name']} accounts for {_percent(dominant_lock):.1f}%",
                [0],
                dominant_function=dominant_lock["name"],
                dominant_percent=_percent(dominant_lock),
            )
        if dominant_user and dominant_user_pct >= 60.0 and not lock_rows:
            return _predicate(
                "COUNTER",
                "a strong non-lock user-space hotspot exists and no lock-related symbol was sampled",
                [0],
                dominant_function=dominant_user["name"],
                dominant_percent=dominant_user_pct,
            )

    def _matches(text_entries, name):
        lowered = name.casefold()
        for entry in text_entries:
            if not isinstance(entry, str):
                continue
            tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_.]*", entry.casefold())
            for token in tokens:
                if len(token) < 3:
                    continue
                if token in lowered or lowered in token:
                    return True
        return False

    for row in named:
        name = str(row["name"])
        if _matches(expected, name):
            return {
                "outcome": "SUPPORT",
                "version": "hypothesis-predicate-v2",
                "reason": f"top function {name} matches an expected observation",
                "criterion_indexes": [0],
            }
        if _matches(falsification, name):
            return {
                "outcome": "COUNTER",
                "version": "hypothesis-predicate-v2",
                "reason": f"top function {name} matches a falsification criterion",
                "criterion_indexes": [0],
            }
    return None


def _derive_imported_evidence_role(
    hypothesis: DropInsightHypothesisModel,
    artifact: ArtifactModel,
    assessment,
    predicate: dict | None = None,
) -> str:
    """Derive polarity from analyzer-produced predicates, never request data.

    Analyzer outputs may expose a normalized ``hypothesis_predicate``.  For
    perf TopN output we also accept the analyzer-produced top-functions list
    and compare it with the hypothesis text.  Other artifacts remain NEUTRAL
    instead of being optimistically labelled SUPPORT.
    """

    if not (assessment.schema_valid and assessment.analyzer_validated):
        return "NEUTRAL"
    metadata = artifact.meta_json or {}
    if predicate is None:
        predicate = metadata.get("hypothesis_predicate")
    if isinstance(predicate, dict):
        outcome = str(predicate.get("outcome") or "").upper()
        if outcome in {"SUPPORT", "COUNTER", "NEUTRAL"}:
            return outcome

    top_functions = metadata.get("top_functions")
    if isinstance(top_functions, list):
        statement = hypothesis.statement.casefold()
        valid_rows = [row for row in top_functions if isinstance(row, dict)]
        named = [
            row for row in valid_rows
            if isinstance(row.get("name"), str) and row["name"].strip()
        ]
        if any(row["name"].casefold() in statement for row in named):
            return "SUPPORT"
        if named and max(_safe_percent(row.get("percent")) for row in named) >= 30:
            # A strong hotspot exists, but it does not substantiate this
            # particular hypothesis.  It is useful context, not counterproof.
            return "NEUTRAL"
    return "NEUTRAL"


def _safe_percent(value) -> float:
    try:
        return max(0.0, min(100.0, float(value or 0)))
    except (TypeError, ValueError):
        return 0.0


def _parse_datetime(value):
    if value is None or not isinstance(value, str):
        return value
    try:
        from datetime import datetime

        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _sample_count(metadata: dict) -> int:
    for key in ("sample_count", "samples", "total_samples", "event_count"):
        value = metadata.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return max(0, value)
    return 0


def _time_ranges_overlap(start, end, requested: dict) -> bool:
    requested_start = requested.get("start")
    requested_end = requested.get("end")
    if not requested_start or not requested_end:
        return True
    try:
        from datetime import datetime, timezone

        if isinstance(requested_start, str):
            requested_start = datetime.fromisoformat(requested_start.replace("Z", "+00:00"))
        if isinstance(requested_end, str):
            requested_end = datetime.fromisoformat(requested_end.replace("Z", "+00:00"))
        start = _as_utc_with_timezone(start, timezone)
        end = _as_utc_with_timezone(end, timezone)
        requested_start = _as_utc_with_timezone(requested_start, timezone)
        requested_end = _as_utc_with_timezone(requested_end, timezone)
        return start < requested_end and end > requested_start
    except (TypeError, ValueError):
        return False


def _as_utc_with_timezone(value, timezone):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ── 修复前后 VERIFIED 验证闭环（guide #4.6）──────────────────

FIX_VERIFY_RELATIVE_THRESHOLD = 0.3


def compare_before_after(
    before_top: list[dict] | None,
    after_top: list[dict] | None,
    *,
    threshold: float = FIX_VERIFY_RELATIVE_THRESHOLD,
) -> dict:
    """Compare the dominant hotspot between a before and after profile task.

    A fix is VERIFIED when the before-task hotspot function has disappeared
    from the after top list or its percent dropped by at least ``threshold``
    (relative). Pure and deterministic so it can be unit-tested.
    """
    def _hotspots(rows):
        return sorted(
            [row for row in (rows or []) if isinstance(row, dict)],
            key=lambda row: float(row.get("percent") or 0),
            reverse=True,
        )

    before = _hotspots(before_top)
    after = _hotspots(after_top)
    if not before:
        return {
            "outcome": "REJECTED",
            "reason": "修复前任务没有有效 TopN 热点数据，无法建立对比基线",
        }
    if not after:
        return {
            "outcome": "REJECTED",
            "reason": "修复后任务没有有效 TopN 热点数据，不能把数据缺失当作热点消失",
        }
    hotspot = before[0]
    name = str(hotspot.get("name") or "")
    before_pct = float(hotspot.get("percent") or 0)
    after_names = {row.get("name") for row in after if row.get("name")}
    after_same = next((row for row in after if row.get("name") == name), None)
    after_pct = float(after_same.get("percent") or 0) if after_same else 0.0

    if name and name not in after_names:
        outcome, reason = "VERIFIED", f"修复后热点 {name} 已从 TopN 消失"
    elif after_pct <= before_pct * (1 - threshold):
        outcome, reason = (
            "VERIFIED",
            f"热点 {name} 占比由 {before_pct:.1f}% 降至 {after_pct:.1f}%",
        )
    else:
        outcome, reason = (
            "REJECTED",
            f"热点 {name} 占比未显著下降（{before_pct:.1f}% -> {after_pct:.1f}%）",
        )
    return {
        "outcome": outcome,
        "reason": reason,
        "before_hotspot": hotspot,
        "after_hotspot": after_same,
        "before_percent": before_pct,
        "after_percent": after_pct,
    }


def _task_top_functions(task_id: str) -> list[dict]:
    session = new_session()
    try:
        artifacts = (
            session.query(ArtifactModel)
            .filter(
                ArtifactModel.task_id == task_id,
                ArtifactModel.artifact_type == "top_json",
            )
            .all()
        )
        for artifact in artifacts:
            top = (artifact.meta_json or {}).get("top_functions")
            if isinstance(top, list):
                return top
        return []
    finally:
        session.close()


def verify_diagnosis_fix(
    diagnosis_id: str,
    *,
    before_task_id: str,
    after_task_id: str,
    fix_summary: str | None = None,
    created_by: str | None = None,
) -> dict | None:
    """Apply-fix -> same-load re-test -> before/after comparison."""
    before_top = _task_top_functions(before_task_id)
    after_top = _task_top_functions(after_task_id)
    comparison = compare_before_after(before_top, after_top)
    session = new_session()
    try:
        model = FixVerificationModel(
            id=f"fix_{uuid4().hex}",
            diagnosis_id=diagnosis_id,
            fix_summary=fix_summary,
            before_task_id=before_task_id,
            after_task_id=after_task_id,
            outcome=comparison["outcome"],
            before_hotspot_json=comparison.get("before_hotspot"),
            after_hotspot_json=comparison.get("after_hotspot"),
            comparison_json=comparison,
            created_by=created_by,
            created_at=now_utc(),
        )
        session.add(model)
        session.commit()
        session.refresh(model)
        return _fix_view(model)
    finally:
        session.close()


def list_fix_verifications(
    diagnosis_id: str, *, limit: int = 50
) -> list[dict]:
    session = new_session()
    try:
        rows = (
            session.query(FixVerificationModel)
            .filter(FixVerificationModel.diagnosis_id == diagnosis_id)
            .order_by(FixVerificationModel.created_at.desc())
            .limit(max(1, int(limit)))
            .all()
        )
        return [_fix_view(row) for row in rows]
    finally:
        session.close()


def _fix_view(model) -> dict:
    return {
        "id": model.id,
        "diagnosis_id": model.diagnosis_id,
        "fix_summary": model.fix_summary,
        "before_task_id": model.before_task_id,
        "after_task_id": model.after_task_id,
        "outcome": model.outcome,
        "comparison": model.comparison_json or {},
        "created_at": model.created_at,
    }


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
            if requested_range and submitted_range != requested_range:
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
