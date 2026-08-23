from __future__ import annotations

from threading import RLock

from server.app.agent_runtime.port import (
    AcceptedTurn,
    AgentTurnInput,
    CaseContextSnapshot,
    RuntimeBinding,
    RuntimeState,
)


class DeterministicAgentRuntime:
    runtime_type = "deterministic"
    runtime_version = "1"

    def __init__(self) -> None:
        self._lock = RLock()
        self._bindings: dict[str, RuntimeBinding] = {}
        self._accepted: dict[tuple[str, str], tuple[AgentTurnInput, AcceptedTurn]] = {}
        self._sealed: set[tuple[str, str]] = set()

    def start_or_resume(self, case_context: CaseContextSnapshot) -> RuntimeBinding:
        with self._lock:
            current = self._bindings.get(case_context.diagnosis_id)
            if current is not None:
                if current.runtime_generation != case_context.runtime_generation:
                    raise ValueError("stale runtime generation")
                return current
            binding = RuntimeBinding(
                diagnosis_id=case_context.diagnosis_id,
                runtime_session_id=f"deterministic:{case_context.diagnosis_id}",
                runtime_generation=case_context.runtime_generation,
                runtime_type=self.runtime_type,
                runtime_version=self.runtime_version,
            )
            self._bindings[case_context.diagnosis_id] = binding
            return binding

    def submit_turn(self, turn: AgentTurnInput) -> AcceptedTurn:
        with self._lock:
            binding = self._bindings.get(turn.diagnosis_id)
            if binding is None:
                raise ValueError("runtime binding does not exist")
            if binding.runtime_generation != turn.runtime_generation:
                raise ValueError("stale runtime generation")
            key = (turn.diagnosis_id, turn.client_command_id)
            existing = self._accepted.get(key)
            if existing is not None:
                prior, accepted = existing
                immutable_prior = prior.model_dump(exclude={"turn_id"})
                immutable_turn = turn.model_dump(exclude={"turn_id"})
                if immutable_prior != immutable_turn:
                    raise ValueError("client_command_id 已用于不同请求")
                return accepted
            accepted = AcceptedTurn(
                turn_id=turn.turn_id,
                accepted=True,
                mode=self.runtime_type,
                detail="提交到确定性调查路径，不调用模型。",
            )
            self._accepted[key] = (turn, accepted)
            return accepted

    def get_accepted_turn(
        self, diagnosis_id: str, client_command_id: str,
    ) -> AcceptedTurn | None:
        with self._lock:
            existing = self._accepted.get((diagnosis_id, client_command_id))
            return existing[1] if existing is not None else None

    def get_state(self, diagnosis_id: str) -> RuntimeState:
        with self._lock:
            binding = self._bindings.get(diagnosis_id)
            if binding is None:
                raise ValueError("runtime binding does not exist")
            return RuntimeState(
                diagnosis_id=diagnosis_id,
                runtime_session_id=binding.runtime_session_id,
                runtime_generation=binding.runtime_generation,
                status="READY",
            )

    def seal_turn(self, diagnosis_id: str, turn_id: str) -> None:
        with self._lock:
            self._sealed.add((diagnosis_id, turn_id))

    def cancel_turn(self, diagnosis_id: str, turn_id: str, reason: str) -> None:
        self.seal_turn(diagnosis_id, turn_id)
