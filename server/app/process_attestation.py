"""Agent-scoped process snapshot normalization and immutable identity bindings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterable, Mapping

PROCESS_SNAPSHOT_MAX_AGE = timedelta(seconds=15)
MAX_PROCESS_CANDIDATES = 256
MAX_PROCESS_TEXT_LENGTH = 256
MAX_PROCESS_CAPABILITIES = 16
MAX_PROCESS_CAPABILITY_LENGTH = 64


class ProcessSnapshotState(str, Enum):
    ABSENT = "absent"
    COMPLETE_EMPTY = "complete-empty"
    COMPLETE_POPULATED = "complete-populated"
    PARTIAL = "partial"
    FAILED = "failed"
    TRUNCATED = "truncated"


@dataclass(frozen=True, slots=True)
class ProcessCandidateInput:
    pid: int
    process_start_ticks: int
    pid_namespace_inode: int
    namespace_pid: int
    executable_identity: str
    comm: str = ""
    cgroup: str = ""
    service_hint: str = ""
    instance_hint: str = ""
    collector_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProcessCandidateSnapshotInput:
    generation: int
    boot_id: str
    observed_at_unix_ms: int
    complete: bool
    truncated: bool
    candidates: tuple[ProcessCandidateInput, ...]
    error: str = ""


@dataclass(frozen=True, slots=True)
class ProcessIdentityBinding:
    agent_id: str
    pid: int
    boot_id: str
    process_start_ticks: int
    pid_namespace_inode: int
    namespace_pid: int
    executable_identity: str
    process_snapshot_id: str
    snapshot_generation: int
    snapshot_received_at: datetime

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ProcessIdentityBinding":
        return cls(
            agent_id=str(value["agent_id"]),
            pid=int(value["pid"]),
            boot_id=str(value["boot_id"]),
            process_start_ticks=int(value["process_start_ticks"]),
            pid_namespace_inode=int(value["pid_namespace_inode"]),
            namespace_pid=int(value["namespace_pid"]),
            executable_identity=str(value["executable_identity"]),
            process_snapshot_id=str(value["process_snapshot_id"]),
            snapshot_generation=int(value["snapshot_generation"]),
            snapshot_received_at=_as_utc(value["snapshot_received_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "pid": self.pid,
            "boot_id": self.boot_id,
            "process_start_ticks": self.process_start_ticks,
            "pid_namespace_inode": self.pid_namespace_inode,
            "namespace_pid": self.namespace_pid,
            "executable_identity": self.executable_identity,
            "process_snapshot_id": self.process_snapshot_id,
            "snapshot_generation": self.snapshot_generation,
            "snapshot_received_at": self.snapshot_received_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ResolvedProcessCandidate:
    agent_id: str
    snapshot_id: str
    snapshot_generation: int
    snapshot_received_at: datetime
    boot_id: str
    candidate: ProcessCandidateInput

    def binding(self) -> ProcessIdentityBinding:
        return ProcessIdentityBinding(
            agent_id=self.agent_id,
            pid=self.candidate.pid,
            boot_id=self.boot_id,
            process_start_ticks=self.candidate.process_start_ticks,
            pid_namespace_inode=self.candidate.pid_namespace_inode,
            namespace_pid=self.candidate.namespace_pid,
            executable_identity=self.candidate.executable_identity,
            process_snapshot_id=self.snapshot_id,
            snapshot_generation=self.snapshot_generation,
            snapshot_received_at=self.snapshot_received_at,
        )


@dataclass(frozen=True, slots=True)
class ResolvedProcessSnapshot:
    agent_id: str
    snapshot_id: str | None
    generation: int | None
    received_at: datetime | None
    observed_at_unix_ms: int | None
    boot_id: str
    state: ProcessSnapshotState
    authoritative: bool
    candidates: tuple[ResolvedProcessCandidate, ...] = ()
    error: str = ""

    def is_fresh(
        self,
        *,
        now: datetime | None = None,
        max_age: timedelta = PROCESS_SNAPSHOT_MAX_AGE,
    ) -> bool:
        if self.received_at is None:
            return False
        age = _as_utc(now or datetime.now(timezone.utc)) - _as_utc(self.received_at)
        return timedelta(0) <= age <= max_age


@dataclass(frozen=True, slots=True)
class NormalizedProcessSnapshot:
    state: ProcessSnapshotState
    authoritative: bool
    snapshot: ProcessCandidateSnapshotInput | None


def normalize_process_candidate_snapshot(value: Any | None) -> NormalizedProcessSnapshot:
    if value is None:
        return NormalizedProcessSnapshot(ProcessSnapshotState.ABSENT, False, None)

    bounded = False
    raw_candidates = list(_field(value, "candidates", ()) or ())
    if len(raw_candidates) > MAX_PROCESS_CANDIDATES:
        bounded = True
        raw_candidates = raw_candidates[:MAX_PROCESS_CANDIDATES]

    candidates: list[ProcessCandidateInput] = []
    identities: set[tuple[Any, ...]] = set()
    invalid = False
    for raw in raw_candidates:
        candidate, candidate_bounded = _normalize_candidate(raw)
        bounded = bounded or candidate_bounded
        identity = candidate_identity_tuple(candidate)
        if not candidate_identity_complete(candidate):
            invalid = True
        if identity in identities:
            invalid = True
            continue
        candidates.append(candidate)
        identities.add(identity)

    boot_id, boot_bounded = _bounded_text(_field(value, "boot_id", ""))
    error, error_bounded = _bounded_text(_field(value, "error", ""))
    bounded = bounded or boot_bounded or error_bounded
    generation = _nonnegative_int(_field(value, "generation", 0))
    observed_at_unix_ms = _nonnegative_int(
        _field(value, "observed_at_unix_ms", 0)
    )
    complete = bool(_field(value, "complete", False))
    client_truncated = bool(_field(value, "truncated", False))

    snapshot = ProcessCandidateSnapshotInput(
        generation=generation,
        boot_id=boot_id,
        observed_at_unix_ms=observed_at_unix_ms,
        complete=complete,
        truncated=client_truncated or bounded,
        candidates=tuple(candidates),
        error=error,
    )

    contradictory = bool(error and (complete or candidates))
    incomplete_header = generation <= 0 or not boot_id
    if error:
        state = ProcessSnapshotState.FAILED
    elif snapshot.truncated:
        state = ProcessSnapshotState.TRUNCATED
    elif not complete or invalid or contradictory or incomplete_header:
        state = ProcessSnapshotState.PARTIAL
    elif candidates:
        state = ProcessSnapshotState.COMPLETE_POPULATED
    else:
        state = ProcessSnapshotState.COMPLETE_EMPTY
    return NormalizedProcessSnapshot(
        state=state,
        authoritative=state in {
            ProcessSnapshotState.COMPLETE_EMPTY,
            ProcessSnapshotState.COMPLETE_POPULATED,
        },
        snapshot=snapshot,
    )


def binding_matches_candidate(
    binding: ProcessIdentityBinding,
    candidate: ResolvedProcessCandidate,
) -> bool:
    return binding == candidate.binding()


def candidate_identity_tuple(candidate: ProcessCandidateInput) -> tuple[Any, ...]:
    return (
        candidate.pid,
        candidate.process_start_ticks,
        candidate.pid_namespace_inode,
        candidate.namespace_pid,
        candidate.executable_identity,
    )


def candidate_identity_complete(candidate: ProcessCandidateInput) -> bool:
    return all(
        (
            candidate.pid > 0,
            candidate.process_start_ticks > 0,
            candidate.pid_namespace_inode > 0,
            candidate.namespace_pid > 0,
            bool(candidate.executable_identity),
        )
    )


def _normalize_candidate(value: Any) -> tuple[ProcessCandidateInput, bool]:
    bounded = False
    text_values: dict[str, str] = {}
    for name in (
        "executable_identity",
        "comm",
        "cgroup",
        "service_hint",
        "instance_hint",
    ):
        text_values[name], changed = _bounded_text(_field(value, name, ""))
        bounded = bounded or changed

    raw_capabilities: Iterable[Any] = _field(
        value, "collector_capabilities", ()
    ) or ()
    capabilities = list(raw_capabilities)
    if len(capabilities) > MAX_PROCESS_CAPABILITIES:
        bounded = True
        capabilities = capabilities[:MAX_PROCESS_CAPABILITIES]
    normalized_capabilities: list[str] = []
    for capability in capabilities:
        normalized, changed = _bounded_text(
            capability, max_length=MAX_PROCESS_CAPABILITY_LENGTH
        )
        bounded = bounded or changed
        normalized_capabilities.append(normalized)

    return ProcessCandidateInput(
        pid=_nonnegative_int(_field(value, "pid", 0)),
        process_start_ticks=_nonnegative_int(
            _field(value, "process_start_ticks", 0)
        ),
        pid_namespace_inode=_nonnegative_int(
            _field(value, "pid_namespace_inode", 0)
        ),
        namespace_pid=_nonnegative_int(_field(value, "namespace_pid", 0)),
        executable_identity=text_values["executable_identity"],
        comm=text_values["comm"],
        cgroup=text_values["cgroup"],
        service_hint=text_values["service_hint"],
        instance_hint=text_values["instance_hint"],
        collector_capabilities=tuple(normalized_capabilities),
    ), bounded


def _field(value: Any, name: str, default: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _bounded_text(value: Any, *, max_length: int = MAX_PROCESS_TEXT_LENGTH) -> tuple[str, bool]:
    raw_text = str(value or "")
    text = "".join(
        character
        for character in raw_text
        if ord(character) >= 0x20 and ord(character) != 0x7F
    )
    changed = text != raw_text
    if len(text) > max_length:
        text = text[:max_length]
        changed = True
    return text, changed


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _as_utc(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise TypeError("expected datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
