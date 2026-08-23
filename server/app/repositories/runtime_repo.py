from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from server.app.models import (
    AgentRuntimeBindingModel,
    AgentRuntimeEventModel,
    AgentRuntimeTurnModel,
    DiagnosisSessionModel,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _immutable_turn_request(row: AgentRuntimeTurnModel) -> tuple[Any, ...]:
    return (
        row.runtime_generation,
        row.user_message,
        row.requested_mode,
        row.side_effect_policy,
        row.actor_id,
    )


_TERMINAL_TRANSITIONS = {
    "COMPLETED": {"ACCEPTED"},
    "FAILED": {"SUBMITTING", "ACCEPTANCE_UNKNOWN", "ACCEPTED"},
    "CANCELLED": {"SUBMITTING", "ACCEPTANCE_UNKNOWN", "ACCEPTED"},
}


class RuntimeMixin:
    def upsert_agent_runtime_binding(
        self,
        *,
        diagnosis_id: str,
        runtime_type: str,
        runtime_version: str,
        runtime_session_id: str,
        runtime_generation: int,
        status: str = "READY",
        last_context_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        if runtime_generation < 1:
            raise ValueError("runtime_generation must be positive")
        with self._write_session() as session:
            if session.get(DiagnosisSessionModel, diagnosis_id) is None:
                raise ValueError("diagnosis session does not exist")
            now = _utcnow()
            row = session.get(AgentRuntimeBindingModel, diagnosis_id)
            if row is None:
                row = AgentRuntimeBindingModel(
                    diagnosis_id=diagnosis_id,
                    runtime_type=runtime_type,
                    runtime_version=runtime_version,
                    runtime_session_id=runtime_session_id,
                    runtime_generation=runtime_generation,
                    status=status,
                    last_event_seq=0,
                    last_context_snapshot_id=last_context_snapshot_id,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                if runtime_generation < row.runtime_generation:
                    raise ValueError("stale runtime generation")
                row.runtime_type = runtime_type
                row.runtime_version = runtime_version
                row.runtime_session_id = runtime_session_id
                row.runtime_generation = runtime_generation
                row.status = status
                row.last_context_snapshot_id = last_context_snapshot_id
                row.updated_at = now
            session.flush()
            return row.to_dict()

    def get_agent_runtime_binding(
        self, *, diagnosis_id: str,
    ) -> dict[str, Any] | None:
        with self._read_session() as session:
            row = session.get(AgentRuntimeBindingModel, diagnosis_id)
            return row.to_dict() if row is not None else None

    def record_agent_runtime_turn(
        self,
        *,
        diagnosis_id: str,
        turn_id: str,
        runtime_generation: int,
        user_message: str,
        requested_mode: str | None,
        side_effect_policy: str | None,
        actor_id: str | None,
        client_command_id: str,
        status: str = "SUBMITTING",
        runtime_session_id: str | None = None,
        accepted_mode: str | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        immutable = (
            runtime_generation,
            user_message,
            requested_mode,
            side_effect_policy,
            actor_id,
        )
        with self._write_session() as session:
            if session.get(DiagnosisSessionModel, diagnosis_id) is None:
                raise ValueError("diagnosis session does not exist")
            existing = session.query(AgentRuntimeTurnModel).filter_by(
                diagnosis_id=diagnosis_id,
                client_command_id=client_command_id,
            ).one_or_none()
            if existing is not None:
                if _immutable_turn_request(existing) != immutable:
                    raise ValueError("client_command_id 已用于不同请求")
                return existing.to_dict()

            binding = session.get(AgentRuntimeBindingModel, diagnosis_id)
            if binding is not None and binding.runtime_generation != runtime_generation:
                raise ValueError("stale runtime generation")
            now = _utcnow()
            row = AgentRuntimeTurnModel(
                diagnosis_id=diagnosis_id,
                turn_id=turn_id,
                runtime_session_id=runtime_session_id,
                runtime_generation=runtime_generation,
                user_message=user_message,
                requested_mode=requested_mode,
                side_effect_policy=side_effect_policy,
                actor_id=actor_id,
                client_command_id=client_command_id,
                status=status,
                accepted_mode=accepted_mode,
                detail=detail,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError as exc:
                raise ValueError("turn identity already exists") from exc
            return row.to_dict()

    def get_agent_runtime_turn(
        self, *, diagnosis_id: str, turn_id: str,
    ) -> dict[str, Any] | None:
        with self._read_session() as session:
            row = session.query(AgentRuntimeTurnModel).filter_by(
                diagnosis_id=diagnosis_id, turn_id=turn_id,
            ).one_or_none()
            return row.to_dict() if row is not None else None

    def get_agent_runtime_turn_by_command(
        self, *, diagnosis_id: str, client_command_id: str,
    ) -> dict[str, Any] | None:
        with self._read_session() as session:
            row = session.query(AgentRuntimeTurnModel).filter_by(
                diagnosis_id=diagnosis_id,
                client_command_id=client_command_id,
            ).one_or_none()
            return row.to_dict() if row is not None else None

    def _transition_agent_runtime_turn(
        self,
        *,
        diagnosis_id: str,
        turn_id: str,
        allowed_from: set[str],
        target_status: str,
        detail: str | None = None,
        runtime_session_id: str | None = None,
        runtime_generation: int | None = None,
        accepted_mode: str | None = None,
    ) -> dict[str, Any]:
        with self._write_session() as session:
            row = (
                session.query(AgentRuntimeTurnModel)
                .filter_by(diagnosis_id=diagnosis_id, turn_id=turn_id)
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                raise ValueError("runtime turn does not exist")
            if row.status == target_status:
                if runtime_generation is not None:
                    if runtime_generation != row.runtime_generation:
                        raise ValueError("stale runtime generation")
                    binding = session.get(AgentRuntimeBindingModel, diagnosis_id)
                    if (
                        binding is not None
                        and binding.runtime_generation != runtime_generation
                    ):
                        raise ValueError("stale runtime generation")
                if (
                    runtime_session_id is not None
                    and row.runtime_session_id != runtime_session_id
                ) or (
                    accepted_mode is not None
                    and row.accepted_mode != accepted_mode
                ):
                    raise ValueError("different runtime turn transition")
                return row.to_dict()
            if row.status not in allowed_from:
                raise ValueError(
                    f"illegal runtime turn transition: {row.status} -> {target_status}"
                )
            if runtime_generation is not None:
                if runtime_generation != row.runtime_generation:
                    raise ValueError("stale runtime generation")
                binding = session.get(AgentRuntimeBindingModel, diagnosis_id)
                if binding is not None and binding.runtime_generation != runtime_generation:
                    raise ValueError("stale runtime generation")
            row.status = target_status
            row.detail = detail
            if runtime_session_id is not None:
                row.runtime_session_id = runtime_session_id
            if accepted_mode is not None:
                row.accepted_mode = accepted_mode
            row.updated_at = _utcnow()
            session.flush()
            return row.to_dict()

    def mark_agent_runtime_turn_acceptance_unknown(
        self, *, diagnosis_id: str, turn_id: str, detail: str,
    ) -> dict[str, Any]:
        return self._transition_agent_runtime_turn(
            diagnosis_id=diagnosis_id,
            turn_id=turn_id,
            allowed_from={"SUBMITTING"},
            target_status="ACCEPTANCE_UNKNOWN",
            detail=detail,
        )

    def mark_agent_runtime_turn_accepted(
        self,
        *,
        diagnosis_id: str,
        turn_id: str,
        runtime_session_id: str,
        runtime_generation: int,
        accepted_mode: str,
    ) -> dict[str, Any]:
        return self._transition_agent_runtime_turn(
            diagnosis_id=diagnosis_id,
            turn_id=turn_id,
            allowed_from={"SUBMITTING", "ACCEPTANCE_UNKNOWN"},
            target_status="ACCEPTED",
            runtime_session_id=runtime_session_id,
            runtime_generation=runtime_generation,
            accepted_mode=accepted_mode,
        )

    def record_agent_runtime_events(
        self,
        *,
        diagnosis_id: str,
        turn_id: str,
        runtime_generation: int,
        events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not events:
            raise ValueError("runtime event batch is empty")
        with self._write_session() as session:
            turn = (
                session.query(AgentRuntimeTurnModel)
                .filter_by(diagnosis_id=diagnosis_id, turn_id=turn_id)
                .with_for_update()
                .one_or_none()
            )
            if turn is None:
                raise ValueError("runtime turn does not exist")
            binding = session.get(AgentRuntimeBindingModel, diagnosis_id)
            expected_generation = (
                binding.runtime_generation if binding is not None
                else turn.runtime_generation
            )
            if (
                runtime_generation != expected_generation
                or runtime_generation != turn.runtime_generation
            ):
                raise ValueError("stale runtime generation")
            if turn.sealed_at is not None:
                raise ValueError("sealed turn")

            results = []
            max_event_seq = binding.last_event_seq or 0 if binding is not None else 0
            for event in events:
                event_id = event["event_id"]
                event_seq = event["event_seq"]
                event_type = event["event_type"]
                payload = event["payload"]
                duplicate = session.query(AgentRuntimeEventModel).filter_by(
                    diagnosis_id=diagnosis_id, event_id=event_id,
                ).one_or_none()
                if duplicate is not None:
                    expected = (
                        turn_id, runtime_generation, event_seq, event_type, payload,
                    )
                    actual = (
                        duplicate.turn_id,
                        duplicate.runtime_generation,
                        duplicate.event_seq,
                        duplicate.event_type,
                        duplicate.payload_json or {},
                    )
                    if actual != expected:
                        raise ValueError("event_id 已用于不同事件")
                    result = duplicate.to_dict()
                    result["duplicate"] = True
                    results.append(result)
                    max_event_seq = max(max_event_seq, event_seq)
                    continue

                now = _utcnow()
                row = AgentRuntimeEventModel(
                    event_id=event_id,
                    diagnosis_id=diagnosis_id,
                    turn_id=turn_id,
                    runtime_generation=runtime_generation,
                    event_seq=event_seq,
                    event_type=event_type,
                    payload_json=payload,
                    created_at=now,
                )
                session.add(row)
                try:
                    session.flush()
                except IntegrityError as exc:
                    raise ValueError("runtime event sequence already exists") from exc
                results.append(row.to_dict())
                max_event_seq = max(max_event_seq, event_seq)

            if binding is not None:
                binding.last_event_seq = max_event_seq
                binding.updated_at = _utcnow()
            return results

    def record_agent_runtime_event(
        self,
        *,
        diagnosis_id: str,
        turn_id: str,
        runtime_generation: int,
        event_seq: int,
        event_type: str,
        payload: dict[str, Any],
        event_id: str,
    ) -> dict[str, Any]:
        return self.record_agent_runtime_events(
            diagnosis_id=diagnosis_id,
            turn_id=turn_id,
            runtime_generation=runtime_generation,
            events=[{
                "event_id": event_id,
                "event_seq": event_seq,
                "event_type": event_type,
                "payload": payload,
            }],
        )[0]

    def list_agent_runtime_events(
        self, *, diagnosis_id: str, turn_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._read_session() as session:
            query = session.query(AgentRuntimeEventModel).filter_by(
                diagnosis_id=diagnosis_id,
            )
            if turn_id is not None:
                query = query.filter_by(turn_id=turn_id)
            return [
                row.to_dict()
                for row in query.order_by(
                    AgentRuntimeEventModel.runtime_generation,
                    AgentRuntimeEventModel.event_seq,
                    AgentRuntimeEventModel.id,
                ).all()
            ]

    def seal_agent_runtime_turn(
        self,
        *,
        diagnosis_id: str,
        turn_id: str,
        terminal_status: str,
        final_message: dict[str, Any],
    ) -> dict[str, Any]:
        if terminal_status not in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise ValueError("invalid terminal runtime turn status")
        with self._write_session() as session:
            turn = (
                session.query(AgentRuntimeTurnModel)
                .filter_by(diagnosis_id=diagnosis_id, turn_id=turn_id)
                .with_for_update()
                .one_or_none()
            )
            if turn is None:
                raise ValueError("runtime turn does not exist")
            if turn.sealed_at is not None:
                if (
                    turn.status != terminal_status
                    or (turn.final_message_json or {}) != final_message
                ):
                    raise ValueError("different finalization")
                return turn.to_dict()
            if turn.status in {"FAILED", "CANCELLED", "COMPLETED"}:
                raise ValueError("different finalization")
            allowed_from = _TERMINAL_TRANSITIONS[terminal_status]
            if turn.status not in allowed_from:
                raise ValueError(
                    f"illegal runtime turn transition: {turn.status} -> {terminal_status}"
                )

            binding = session.get(AgentRuntimeBindingModel, diagnosis_id)
            if (
                binding is not None
                and binding.runtime_generation != turn.runtime_generation
            ):
                raise ValueError("stale runtime generation")
            now = _utcnow()
            completion_seq = (
                session.query(func.max(AgentRuntimeEventModel.event_seq))
                .filter_by(
                    diagnosis_id=diagnosis_id,
                    turn_id=turn_id,
                    runtime_generation=turn.runtime_generation,
                )
                .scalar()
                or 0
            ) + 1
            completion = AgentRuntimeEventModel(
                event_id=f"completion-{uuid4().hex}",
                diagnosis_id=diagnosis_id,
                turn_id=turn_id,
                runtime_generation=turn.runtime_generation,
                event_seq=completion_seq,
                event_type="turn.completed",
                payload_json={
                    "turn_id": turn_id,
                    "status": terminal_status,
                    "final_message": final_message,
                },
                created_at=now,
            )
            turn.status = terminal_status
            turn.final_message_json = final_message
            turn.sealed_at = now
            turn.completed_at = now
            turn.updated_at = now
            session.add(completion)
            binding = session.get(AgentRuntimeBindingModel, diagnosis_id)
            if binding is not None:
                binding.last_event_seq = max(
                    binding.last_event_seq or 0, completion_seq,
                )
                binding.updated_at = now
            session.flush()
            return turn.to_dict()
