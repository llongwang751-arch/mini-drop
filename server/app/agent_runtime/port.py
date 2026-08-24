from __future__ import annotations

from typing import Literal, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CaseContextSnapshot(StrictModel):
    diagnosis_id: str = Field(min_length=1, max_length=128)
    case_id: Optional[str] = Field(default=None, max_length=128)
    runtime_generation: int = Field(ge=1)
    case_goal: str = ""
    target_scope: dict = Field(default_factory=dict)
    context_snapshot_id: Optional[str] = None


class AgentTurnInput(StrictModel):
    diagnosis_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    runtime_generation: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=8000)
    references: list[dict] = Field(default_factory=list)
    requested_mode: Optional[str] = None
    client_command_id: str = Field(min_length=1, max_length=128)


class AcceptedTurn(StrictModel):
    turn_id: str
    runtime_session_id: str = Field(min_length=1, max_length=128)
    runtime_generation: int = Field(ge=1)
    accepted: bool
    mode: str = "deterministic"
    detail: str = ""


class RuntimeBinding(StrictModel):
    diagnosis_id: str
    runtime_session_id: str
    runtime_generation: int = Field(ge=1)
    runtime_type: str
    runtime_version: str


class RuntimeState(StrictModel):
    diagnosis_id: str
    runtime_session_id: str
    runtime_generation: int = Field(ge=1)
    status: str
    active_turn_id: Optional[str] = None
    last_error: Optional[str] = None


class RuntimeEventInput(StrictModel):
    event_id: str = Field(min_length=1, max_length=128)
    event_seq: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict)


class RuntimeEventBatch(StrictModel):
    runtime_session_id: str = Field(min_length=1, max_length=128)
    runtime_generation: int = Field(ge=1)
    events: list[RuntimeEventInput] = Field(min_length=1, max_length=128)


class RuntimeTerminalOutcome(StrictModel):
    runtime_session_id: str = Field(min_length=1, max_length=128)
    runtime_generation: int = Field(ge=1)
    terminal_status: Literal["COMPLETED", "FAILED", "CANCELLED"]
    final_message: dict = Field(default_factory=dict)


class AgentRuntimePort(Protocol):
    runtime_type: str
    runtime_version: str

    def start_or_resume(self, case_context: CaseContextSnapshot) -> RuntimeBinding: ...

    def submit_turn(self, turn: AgentTurnInput) -> AcceptedTurn: ...

    def get_accepted_turn(
        self, diagnosis_id: str, client_command_id: str,
    ) -> AcceptedTurn | None: ...

    def get_state(self, diagnosis_id: str) -> RuntimeState: ...

    def seal_turn(self, diagnosis_id: str, turn_id: str) -> None: ...

    def cancel_turn(self, diagnosis_id: str, turn_id: str, reason: str) -> None: ...
