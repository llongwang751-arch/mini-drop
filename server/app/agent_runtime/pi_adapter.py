from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from server.app.agent_runtime.config import (
    AgentRuntimeMode,
    pi_internal_token,
    pi_runtime_url,
    pi_runtime_version,
    runtime_mode,
)
from server.app.agent_runtime.port import (
    AcceptedTurn,
    AgentTurnInput,
    CaseContextSnapshot,
    RuntimeBinding,
    RuntimeState,
)


class PiSidecarError(RuntimeError):
    pass


class PiDefinitiveRejection(PiSidecarError):
    pass


class PiAcceptanceUnknown(PiSidecarError):
    pass


class PiProtocolError(PiSidecarError):
    pass


class PiAgentRuntimeAdapter:
    runtime_type = "pi"

    def __init__(self, base_url: str | None = None, timeout: float = 30.0):
        self._url = (base_url or pi_runtime_url()).rstrip("/")
        if not self._url:
            raise RuntimeError(
                "Pi runtime requested but MINI_DROP_PI_RUNTIME_URL is empty"
            )
        self._timeout = timeout
        self._shadow = runtime_mode() == AgentRuntimeMode.PI_SHADOW
        self.runtime_version = f"pi-{pi_runtime_version()}"

    @staticmethod
    def _segment(value: str) -> str:
        return urllib.parse.quote(value, safe="")

    def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        submit: bool = False,
        not_found_none: bool = False,
    ) -> Any:
        request = urllib.request.Request(
            f"{self._url}{path}",
            data=(
                None
                if body is None
                else json.dumps(body, ensure_ascii=False).encode("utf-8")
            ),
            method=method,
        )
        request.add_header("Accept", "application/json")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        token = pi_internal_token()
        if token:
            request.add_header("X-Internal-Token", token)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if not_found_none and exc.code == 404:
                return None
            raise PiDefinitiveRejection(
                f"sidecar {method} {path}: HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            message = f"sidecar {method} {path} transport failure: {exc}"
            if submit:
                raise PiAcceptanceUnknown(
                    f"{message}; turn acceptance unknown"
                ) from exc
            raise PiSidecarError(message) from exc

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            error = PiProtocolError(
                f"sidecar {method} {path} returned invalid JSON"
            )
            if submit:
                raise PiAcceptanceUnknown(
                    f"{error}; turn acceptance unknown"
                ) from exc
            raise error from exc
        if not isinstance(payload, dict) or "ok" not in payload:
            raise PiProtocolError(
                f"sidecar {method} {path} returned an invalid envelope"
            )
        if payload["ok"] is not True:
            raise PiDefinitiveRejection(
                f"sidecar {method} {path}: {payload.get('error', 'rejected')}"
            )
        return payload.get("data")

    def _diagnosis_path(self, diagnosis_id: str) -> str:
        return (
            "/internal/runtime/v1/diagnoses/"
            f"{self._segment(diagnosis_id)}"
        )

    def start_or_resume(self, case_context: CaseContextSnapshot) -> RuntimeBinding:
        data = self._call(
            "POST",
            f"{self._diagnosis_path(case_context.diagnosis_id)}/resume",
            {"context": case_context.model_dump(mode="json")},
        )
        binding = RuntimeBinding.model_validate(data)
        if (
            binding.diagnosis_id != case_context.diagnosis_id
            or binding.runtime_generation != case_context.runtime_generation
        ):
            raise PiProtocolError("sidecar returned a different runtime binding")
        return binding

    def submit_turn(self, turn: AgentTurnInput) -> AcceptedTurn:
        data = self._call(
            "POST",
            f"{self._diagnosis_path(turn.diagnosis_id)}/turn",
            {"turn": turn.model_dump(mode="json"), "shadow": self._shadow},
            submit=True,
        )
        accepted = AcceptedTurn.model_validate(data)
        if accepted.turn_id != turn.turn_id:
            raise PiProtocolError("sidecar replaced the authoritative turn identity")
        if not accepted.accepted:
            raise PiDefinitiveRejection("sidecar definitively rejected the turn")
        return accepted

    def get_accepted_turn(
        self, diagnosis_id: str, client_command_id: str,
    ) -> AcceptedTurn | None:
        data = self._call(
            "GET",
            f"{self._diagnosis_path(diagnosis_id)}/turns/accepted/"
            f"{self._segment(client_command_id)}",
            not_found_none=True,
        )
        return None if data is None else AcceptedTurn.model_validate(data)

    def get_state(self, diagnosis_id: str) -> RuntimeState:
        data = self._call(
            "GET", f"{self._diagnosis_path(diagnosis_id)}/state",
        )
        state = RuntimeState.model_validate(data)
        if state.diagnosis_id != diagnosis_id:
            raise PiProtocolError("sidecar returned state for a different diagnosis")
        return state

    def seal_turn(self, diagnosis_id: str, turn_id: str) -> None:
        self._call(
            "POST",
            f"{self._diagnosis_path(diagnosis_id)}/turns/"
            f"{self._segment(turn_id)}/seal",
            {},
        )

    def cancel_turn(self, diagnosis_id: str, turn_id: str, reason: str) -> None:
        self._call(
            "POST",
            f"{self._diagnosis_path(diagnosis_id)}/turns/"
            f"{self._segment(turn_id)}/cancel",
            {"reason": reason},
        )
