"""诊断事件存储原语：幂等追加、语义去重、outbox 同事务发布、行锁与 CAS 状态迁移。

从 service.py 拆出的叶子模块：只依赖 SQLAlchemy 与领域模型，不回调任何
service 层 helper。service 命名空间继续 re-export 这些名字，作为调用方与
测试的唯一补丁点。
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select, update

from server.app.models import (
    DropInsightEventModel,
    DropInsightSessionModel,
    OutboxMessageModel,
)


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
    if _latest_semantic_event_has_payload(
        session,
        diagnosis_id,
        event_type,
        actor,
        payload,
    ):
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
    # A single state transition may append several durable events before the
    # surrounding transaction commits (for example action_proposed followed
    # by awaiting_approval, or simulation_started followed by
    # action_dispatched).  Flush here so the next max(sequence) query observes
    # this event and allocates the next sequence instead of reusing it.  The
    # event and its outbox row still commit atomically in the caller's
    # transaction.
    session.flush()
    return True


_EVENT_SEMANTIC_SCOPE_KEYS = (
    "round_index",
    "iteration",
    "hypothesis_id",
    "parent_hypothesis_id",
    "node_id",
    "tool_call_id",
    "task_id",
    "task_attempt_id",
    "report_id",
    "evidence_id",
    "intervention_id",
    "observation_id",
    "snapshot_id",
)


def _event_semantic_scope(payload: dict) -> tuple:
    """Return the stable round/entity slot whose latest value an event describes.

    Event payloads remain the source of truth; this scope is used only to find
    the previous value for no-op suppression. Mutable fields such as status,
    score and reward are deliberately excluded so a real A -> B -> A change is
    retained instead of being mistaken for a historical duplicate.
    """

    scope = [
        (key, _freeze_event_value(payload[key]))
        for key in _EVENT_SEMANTIC_SCOPE_KEYS
        if payload.get(key) is not None
    ]
    hypotheses = payload.get("hypotheses")
    if isinstance(hypotheses, list):
        hypothesis_scope = sorted(
            (
                str(item.get("hypothesis_id")),
                item.get("round_index"),
            )
            for item in hypotheses
            if isinstance(item, dict) and item.get("hypothesis_id")
        )
        if hypothesis_scope:
            scope.append(("hypotheses", tuple(hypothesis_scope)))
    return tuple(scope)


def _freeze_event_value(value):
    if isinstance(value, dict):
        return tuple(
            (str(key), _freeze_event_value(item))
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_event_value(item) for item in value)
    return value


def _latest_semantic_event_has_payload(
    session,
    diagnosis_id: str,
    event_type: str,
    actor: str,
    payload: dict,
) -> bool:
    """Suppress only a repeated latest value in the same semantic slot.

    The diagnosis row is already locked by ``_append_event``, so this check is
    safe against concurrent pollers. Looking at the latest value per slot (not
    every historical payload) preserves genuine state/score reversals and new
    LATS iterations while making at-least-once orchestration a durable no-op.
    """

    semantic_scope = _event_semantic_scope(payload)
    rows = session.execute(
        select(DropInsightEventModel.payload_json)
        .where(
            DropInsightEventModel.diagnosis_id == diagnosis_id,
            DropInsightEventModel.event_type == event_type,
            DropInsightEventModel.actor == actor,
        )
        .order_by(DropInsightEventModel.sequence.desc())
    ).scalars()
    for existing_payload in rows:
        existing_payload = dict(existing_payload or {})
        if semantic_scope:
            if _event_semantic_scope(existing_payload) != semantic_scope:
                continue
        return _freeze_event_value(existing_payload) == _freeze_event_value(payload)
    return False


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
    "NEEDS_CLARIFICATION": {"UNDERSTANDING", "HYPOTHESIZING", "NEEDS_CLARIFICATION", "CANCELLED"},
    "UNDERSTANDING": {"PLANNING", "HYPOTHESIZING", "NEEDS_CLARIFICATION", "UNDERSTANDING", "CANCELLED"},
    "PLANNING": {"HYPOTHESIZING", "COLLECTING_EVIDENCE", "INSUFFICIENT_EVIDENCE", "PLANNING", "CANCELLED"},
    "HYPOTHESIZING": {"PLANNING", "COLLECTING_EVIDENCE", "INSUFFICIENT_EVIDENCE", "HYPOTHESIZING", "CANCELLED"},
    "COLLECTING_EVIDENCE": {"HYPOTHESIZING", "INSUFFICIENT_EVIDENCE", "COMPLETED", "COLLECTING_EVIDENCE", "CANCELLED"},
    "INSUFFICIENT_EVIDENCE": {"HYPOTHESIZING", "PLANNING", "COLLECTING_EVIDENCE", "INSUFFICIENT_EVIDENCE"},
    # A verified report remains immutable, but an explicit human turn may
    # reopen the session and create a later auditable round.
    "COMPLETED": {"COMPLETED", "HYPOTHESIZING"},
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
