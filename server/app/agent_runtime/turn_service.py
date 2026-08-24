from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from server.app.agent_runtime.dispatcher import get_runtime
from server.app.agent_runtime.pi_adapter import (
    PiAcceptanceUnknown,
    PiDefinitiveRejection,
    PiProtocolError,
    PiSidecarError,
)
from server.app.agent_runtime.port import (
    AcceptedTurn,
    AgentRuntimePort,
    AgentTurnInput,
    CaseContextSnapshot,
    RuntimeBinding,
)
from server.app.diagnosis.store import DiagnosisStore
from server.app.sql_repository import SqlRepository

_RECOVERY_LEASE = timedelta(minutes=5)
_PENDING_STATUSES = {"SUBMITTING", "ACCEPTANCE_UNKNOWN"}
_TERMINAL_OR_ACCEPTED_STATUSES = {
    "ACCEPTED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
}


class RuntimeTurnNotFound(ValueError):
    pass


class RuntimeTurnConflict(ValueError):
    pass


class RuntimeTurnUnavailable(RuntimeError):
    pass


class RuntimeTurnService:
    def __init__(
        self,
        *,
        repository: SqlRepository | None = None,
        diagnosis_store: DiagnosisStore | None = None,
        runtime: AgentRuntimePort | None = None,
    ) -> None:
        self._repository = repository or SqlRepository()
        self._diagnosis_store = diagnosis_store or DiagnosisStore()
        self._runtime = runtime

    def submit(
        self,
        *,
        diagnosis_id: str,
        actor_id: str,
        message: str,
        client_command_id: str,
        requested_mode: str | None,
    ) -> dict[str, Any]:
        diagnosis = self._diagnosis_store.get_session(diagnosis_id)
        if diagnosis is None or diagnosis["creator_id"] != actor_id:
            raise RuntimeTurnNotFound("diagnosis session does not exist")

        existing = self._repository.get_agent_runtime_turn_by_command(
            diagnosis_id=diagnosis_id,
            client_command_id=client_command_id,
        )
        if existing is not None:
            self._require_same_request(
                existing,
                actor_id=actor_id,
                message=message,
                requested_mode=requested_mode,
            )
            return self._resume_existing(existing, diagnosis=diagnosis)

        stored_binding = self._repository.get_agent_runtime_binding(
            diagnosis_id=diagnosis_id,
        )
        if stored_binding is not None:
            self._require_compatible_binding(
                self._runtime or get_runtime(),
                stored_binding,
            )
        proposed_turn_id = f"turn-{uuid4().hex}"
        try:
            turn = self._repository.record_agent_runtime_turn(
                diagnosis_id=diagnosis_id,
                turn_id=proposed_turn_id,
                runtime_generation=(
                    stored_binding["runtime_generation"]
                    if stored_binding is not None
                    else 1
                ),
                user_message=message,
                requested_mode=requested_mode,
                side_effect_policy="READ_ONLY",
                actor_id=actor_id,
                client_command_id=client_command_id,
                status="SUBMITTING",
                runtime_session_id=(
                    stored_binding["runtime_session_id"]
                    if stored_binding is not None
                    else None
                ),
            )
        except ValueError as exc:
            raise RuntimeTurnConflict(str(exc)) from exc

        self._require_same_request(
            turn,
            actor_id=actor_id,
            message=message,
            requested_mode=requested_mode,
        )
        return self._resume_existing(turn, diagnosis=diagnosis)

    def _binding(
        self,
        *,
        runtime: AgentRuntimePort,
        diagnosis: dict[str, Any],
    ) -> RuntimeBinding:
        diagnosis_id = diagnosis["diagnosis_id"]
        stored = self._repository.get_agent_runtime_binding(
            diagnosis_id=diagnosis_id,
        )
        if stored is not None:
            self._require_compatible_binding(runtime, stored)
            return RuntimeBinding.model_validate({
                "diagnosis_id": diagnosis_id,
                "runtime_session_id": stored["runtime_session_id"],
                "runtime_generation": stored["runtime_generation"],
                "runtime_type": stored["runtime_type"],
                "runtime_version": stored["runtime_version"],
            })

        context = CaseContextSnapshot(
            diagnosis_id=diagnosis_id,
            case_id=diagnosis.get("case_id"),
            runtime_generation=1,
            case_goal=diagnosis.get("raw_query", ""),
            target_scope=diagnosis.get("target_scope", {}),
            context_snapshot_id=diagnosis.get("topology_snapshot_id"),
        )
        try:
            binding = runtime.start_or_resume(context)
        except (PiSidecarError, ValidationError, ValueError) as exc:
            raise RuntimeTurnUnavailable(str(exc)) from exc
        if (
            binding.diagnosis_id != diagnosis_id
            or binding.runtime_generation != context.runtime_generation
            or binding.runtime_type != runtime.runtime_type
            or binding.runtime_version != runtime.runtime_version
        ):
            raise RuntimeTurnUnavailable(
                "runtime returned a different binding authority"
            )
        try:
            stored = self._repository.upsert_agent_runtime_binding(
                diagnosis_id=diagnosis_id,
                runtime_type=binding.runtime_type,
                runtime_version=binding.runtime_version,
                runtime_session_id=binding.runtime_session_id,
                runtime_generation=binding.runtime_generation,
                last_context_snapshot_id=context.context_snapshot_id,
            )
        except ValueError as exc:
            raise RuntimeTurnConflict(str(exc)) from exc
        return RuntimeBinding.model_validate({
            "diagnosis_id": diagnosis_id,
            "runtime_session_id": stored["runtime_session_id"],
            "runtime_generation": stored["runtime_generation"],
            "runtime_type": stored["runtime_type"],
            "runtime_version": stored["runtime_version"],
        })

    def _resume_existing(
        self,
        turn: dict[str, Any],
        *,
        diagnosis: dict[str, Any],
    ) -> dict[str, Any]:
        if turn["status"] in _TERMINAL_OR_ACCEPTED_STATUSES:
            return turn
        if turn["status"] not in _PENDING_STATUSES:
            raise RuntimeTurnConflict(
                f"unsupported runtime turn status: {turn['status']}"
            )

        recovery_owner = f"runtime-recovery-{uuid4().hex}"
        try:
            turn = self._repository.claim_agent_runtime_turn_recovery(
                diagnosis_id=turn["diagnosis_id"],
                turn_id=turn["turn_id"],
                recovery_owner=recovery_owner,
                lease_expires_at=datetime.now(timezone.utc) + _RECOVERY_LEASE,
            )
        except ValueError as exc:
            raise RuntimeTurnConflict(str(exc)) from exc
        if not turn["recovery_claimed"]:
            return turn
        recovery_fencing_token = turn["recovery_fencing_token"]

        try:
            return self._recover_claimed(
                turn=turn,
                diagnosis=diagnosis,
                recovery_owner=recovery_owner,
                recovery_fencing_token=recovery_fencing_token,
            )
        finally:
            self._release_recovery(
                turn=turn,
                recovery_owner=recovery_owner,
                recovery_fencing_token=recovery_fencing_token,
            )

    def _recover_claimed(
        self,
        *,
        turn: dict[str, Any],
        diagnosis: dict[str, Any],
        recovery_owner: str,
        recovery_fencing_token: int,
    ) -> dict[str, Any]:
        runtime = self._runtime or get_runtime()
        binding = self._repository.get_agent_runtime_binding(
            diagnosis_id=turn["diagnosis_id"],
        )
        if binding is not None:
            self._require_compatible_binding(runtime, binding)

        if turn["recovery_phase"] == "NEEDS_BINDING":
            binding_authority = self._binding(
                runtime=runtime,
                diagnosis=diagnosis,
            )
            try:
                turn = self._repository.attach_agent_runtime_turn_authority(
                    diagnosis_id=turn["diagnosis_id"],
                    turn_id=turn["turn_id"],
                    runtime_session_id=binding_authority.runtime_session_id,
                    runtime_generation=binding_authority.runtime_generation,
                    recovery_owner=recovery_owner,
                    recovery_fencing_token=recovery_fencing_token,
                )
            except ValueError as exc:
                return self._current_after_recovery_conflict(turn, exc)
        elif turn["recovery_phase"] != "SUBMIT_INTENT":
            raise RuntimeTurnConflict("invalid runtime recovery phase")

        return self._reconcile_then_submit(
            runtime=runtime,
            turn=turn,
            recovery_owner=recovery_owner,
            recovery_fencing_token=recovery_fencing_token,
        )

    def _reconcile_then_submit(
        self,
        *,
        runtime: AgentRuntimePort,
        turn: dict[str, Any],
        recovery_owner: str,
        recovery_fencing_token: int,
    ) -> dict[str, Any]:
        accepted = self._lookup_accepted(runtime=runtime, turn=turn)
        if accepted is not None:
            return self._accept_or_preserve_winner(
                turn=turn,
                accepted=accepted,
                recovery_owner=recovery_owner,
                recovery_fencing_token=recovery_fencing_token,
            )

        try:
            accepted = runtime.submit_turn(self._turn_input(turn))
        except PiDefinitiveRejection as exc:
            return self._reject_submission(
                turn=turn,
                detail=str(exc),
                recovery_owner=recovery_owner,
                recovery_fencing_token=recovery_fencing_token,
            )
        except (PiAcceptanceUnknown, PiProtocolError, ValidationError) as exc:
            return self._mark_unknown_and_reconcile(
                runtime=runtime,
                turn=turn,
                detail=str(exc),
                recovery_owner=recovery_owner,
                recovery_fencing_token=recovery_fencing_token,
            )
        except PiSidecarError as exc:
            raise RuntimeTurnUnavailable(str(exc)) from exc
        except ValueError as exc:
            return self._reject_submission(
                turn=turn,
                detail=str(exc),
                recovery_owner=recovery_owner,
                recovery_fencing_token=recovery_fencing_token,
            )

        try:
            return self._accept(
                turn=turn,
                accepted=accepted,
                recovery_owner=recovery_owner,
                recovery_fencing_token=recovery_fencing_token,
            )
        except RuntimeTurnConflict as exc:
            return self._mark_unknown_and_reconcile(
                runtime=runtime,
                turn=turn,
                detail=str(exc),
                recovery_owner=recovery_owner,
                recovery_fencing_token=recovery_fencing_token,
            )

    def _mark_unknown_and_reconcile(
        self,
        *,
        runtime: AgentRuntimePort,
        turn: dict[str, Any],
        detail: str,
        recovery_owner: str,
        recovery_fencing_token: int,
    ) -> dict[str, Any]:
        if turn["status"] == "SUBMITTING":
            try:
                turn = self._repository.mark_agent_runtime_turn_acceptance_unknown(
                    diagnosis_id=turn["diagnosis_id"],
                    turn_id=turn["turn_id"],
                    detail=detail,
                    recovery_owner=recovery_owner,
                    recovery_fencing_token=recovery_fencing_token,
                )
            except ValueError as exc:
                return self._current_after_recovery_conflict(turn, exc)
        accepted = self._lookup_accepted(runtime=runtime, turn=turn)
        if accepted is None:
            return turn
        return self._accept_or_preserve_winner(
            turn=turn,
            accepted=accepted,
            recovery_owner=recovery_owner,
            recovery_fencing_token=recovery_fencing_token,
        )

    @staticmethod
    def _lookup_accepted(
        *,
        runtime: AgentRuntimePort,
        turn: dict[str, Any],
    ) -> AcceptedTurn | None:
        try:
            return runtime.get_accepted_turn(
                turn["diagnosis_id"],
                turn["client_command_id"],
            )
        except (PiSidecarError, ValidationError, ValueError) as exc:
            raise RuntimeTurnUnavailable(str(exc)) from exc

    def _accept_or_preserve_winner(
        self,
        *,
        turn: dict[str, Any],
        accepted: AcceptedTurn,
        recovery_owner: str,
        recovery_fencing_token: int,
    ) -> dict[str, Any]:
        try:
            return self._accept(
                turn=turn,
                accepted=accepted,
                recovery_owner=recovery_owner,
                recovery_fencing_token=recovery_fencing_token,
            )
        except RuntimeTurnConflict as exc:
            current = self._repository.get_agent_runtime_turn(
                diagnosis_id=turn["diagnosis_id"],
                turn_id=turn["turn_id"],
            )
            if current is None:
                raise
            if current["status"] in _PENDING_STATUSES:
                try:
                    return self._repository.mark_agent_runtime_turn_acceptance_unknown(
                        diagnosis_id=turn["diagnosis_id"],
                        turn_id=turn["turn_id"],
                        detail=str(exc),
                        recovery_owner=recovery_owner,
                        recovery_fencing_token=recovery_fencing_token,
                    )
                except ValueError as stale:
                    return self._current_after_recovery_conflict(turn, stale)
            return current

    def _accept(
        self,
        *,
        turn: dict[str, Any],
        accepted: AcceptedTurn,
        recovery_owner: str,
        recovery_fencing_token: int,
    ) -> dict[str, Any]:
        binding = self._repository.get_agent_runtime_binding(
            diagnosis_id=turn["diagnosis_id"],
        )
        if binding is None:
            raise RuntimeTurnConflict("runtime binding does not exist")
        if not accepted.accepted:
            raise RuntimeTurnConflict("runtime did not accept the turn")
        if accepted.turn_id != turn["turn_id"]:
            raise RuntimeTurnConflict("runtime returned a different turn identity")
        if (
            accepted.runtime_generation != turn["runtime_generation"]
            or accepted.runtime_generation != binding["runtime_generation"]
        ):
            raise RuntimeTurnConflict("stale runtime generation")
        if (
            accepted.runtime_session_id != turn["runtime_session_id"]
            or accepted.runtime_session_id != binding["runtime_session_id"]
        ):
            raise RuntimeTurnConflict("stale runtime session")
        try:
            return self._repository.mark_agent_runtime_turn_accepted(
                diagnosis_id=turn["diagnosis_id"],
                turn_id=turn["turn_id"],
                runtime_session_id=accepted.runtime_session_id,
                runtime_generation=accepted.runtime_generation,
                accepted_mode=accepted.mode,
                recovery_owner=recovery_owner,
                recovery_fencing_token=recovery_fencing_token,
            )
        except ValueError as exc:
            raise RuntimeTurnConflict(str(exc)) from exc

    def _reject_submission(
        self,
        *,
        turn: dict[str, Any],
        detail: str,
        recovery_owner: str,
        recovery_fencing_token: int,
    ) -> dict[str, Any]:
        try:
            return self._repository.reject_agent_runtime_turn_submission(
                diagnosis_id=turn["diagnosis_id"],
                turn_id=turn["turn_id"],
                final_message={"error": detail},
                recovery_owner=recovery_owner,
                recovery_fencing_token=recovery_fencing_token,
            )
        except ValueError as exc:
            return self._current_after_recovery_conflict(turn, exc)

    def _current_after_recovery_conflict(
        self,
        turn: dict[str, Any],
        exc: ValueError,
    ) -> dict[str, Any]:
        current = self._repository.get_agent_runtime_turn(
            diagnosis_id=turn["diagnosis_id"],
            turn_id=turn["turn_id"],
        )
        if current is None:
            raise RuntimeTurnConflict(str(exc)) from exc
        if str(exc) != "stale runtime recovery authority":
            raise RuntimeTurnConflict(str(exc)) from exc
        return current

    def _release_recovery(
        self,
        *,
        turn: dict[str, Any],
        recovery_owner: str,
        recovery_fencing_token: int,
    ) -> None:
        try:
            self._repository.release_agent_runtime_turn_recovery(
                diagnosis_id=turn["diagnosis_id"],
                turn_id=turn["turn_id"],
                recovery_owner=recovery_owner,
                recovery_fencing_token=recovery_fencing_token,
            )
        except ValueError as exc:
            if str(exc) != "stale runtime recovery authority":
                raise RuntimeTurnConflict(str(exc)) from exc

    @staticmethod
    def _require_compatible_binding(
        runtime: AgentRuntimePort,
        binding: dict[str, Any],
    ) -> None:
        if (
            binding["runtime_type"] != runtime.runtime_type
            or binding["runtime_version"] != runtime.runtime_version
        ):
            raise RuntimeTurnUnavailable(
                "stored runtime binding is incompatible with the active adapter"
            )

    @staticmethod
    def _turn_input(turn: dict[str, Any]) -> AgentTurnInput:
        return AgentTurnInput(
            diagnosis_id=turn["diagnosis_id"],
            turn_id=turn["turn_id"],
            runtime_generation=turn["runtime_generation"],
            message=turn["user_message"],
            requested_mode=turn["requested_mode"],
            client_command_id=turn["client_command_id"],
        )

    @staticmethod
    def _require_same_request(
        turn: dict[str, Any],
        *,
        actor_id: str,
        message: str,
        requested_mode: str | None,
    ) -> None:
        expected = (
            message,
            requested_mode,
            "READ_ONLY",
            actor_id,
        )
        actual = (
            turn["user_message"],
            turn["requested_mode"],
            turn["side_effect_policy"],
            turn["actor_id"],
        )
        if actual != expected:
            raise RuntimeTurnConflict(
                "client_command_id 已用于不同请求"
            )
