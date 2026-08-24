"""Authoritative host-process snapshots attached to Agent heartbeats.

The immutable identity fields collected here mirror the native Agent.  They
let the server bind a diagnosis to the process that was actually observed,
instead of trusting a reusable PID supplied by a browser.
"""

from __future__ import annotations

import os
import re
import socket
import time
from pathlib import Path
from typing import Iterable


MAX_CANDIDATES = 256
_HEX_CONTAINER_HOSTNAME = re.compile(r"[0-9a-f]{12,64}", re.IGNORECASE)


def _read_text(path: Path, limit: int = 256) -> str:
    try:
        value = path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return ""
    return "".join(ch for ch in value if ch >= " " and ch != "\x7f")[:limit]


def _parse_stat(path: Path) -> tuple[int, int] | None:
    try:
        value = path.read_text(encoding="utf-8", errors="replace")
        fields = value[value.rfind(")") + 2 :].split()
        parent_pid = int(fields[1])  # fields begin at Linux /proc stat field 3
        start_ticks = int(fields[19])
    except (OSError, ValueError, IndexError):
        return None
    return (parent_pid, start_ticks) if start_ticks > 0 else None


def _namespace_pid(path: Path) -> int:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    for line in lines:
        if line.startswith("NSpid:"):
            try:
                return int(line.split()[-1])
            except (ValueError, IndexError):
                return 0
    return 0


def _namespace_inode(path: Path) -> int:
    try:
        target = os.readlink(path)
        return int(target[target.index("[") + 1 : target.index("]")])
    except (OSError, ValueError):
        return 0


def _cgroup(path: Path) -> str:
    fallback = ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        fields = line.split(":", 2)
        if len(fields) != 3 or fields[2] in {"", "/"}:
            continue
        fallback = fallback or fields[2]
        if any(token in fields[2] for token in (".service", "docker", "kubepods", "containerd")):
            return fields[2][:256]
    return fallback[:256]


def _hints(root: Path, cgroup: str, host_hostname: str) -> tuple[str, str]:
    parts = [part for part in cgroup.split("/") if part]
    service = next((part for part in parts if part.endswith(".service")), "")
    instance = parts[-1] if parts else ""
    container_hostname = _read_text(root / "root/etc/hostname")
    if (
        container_hostname
        and container_hostname != host_hostname
        and not _HEX_CONTAINER_HOSTNAME.fullmatch(container_hostname)
    ):
        service = container_hostname
        instance = container_hostname
    return service[:256], instance[:256]


def _is_agent_descendant(pid: int, self_pid: int, parents: dict[int, int]) -> bool:
    visited: set[int] = set()
    while pid and pid not in visited:
        if pid == self_pid:
            return True
        visited.add(pid)
        pid = parents.get(pid, 0)
    return False


def fill_process_candidate_snapshot(
    wire_snapshot,
    capabilities: Iterable[str],
    *,
    proc_root: str | Path = "/proc",
    self_pid: int | None = None,
) -> None:
    """Fill a protobuf ProcessCandidateSnapshot from one bounded /proc scan."""

    root = Path(proc_root)
    wire_snapshot.generation = time.monotonic_ns()
    wire_snapshot.observed_at_unix_ms = int(time.time() * 1000)
    wire_snapshot.boot_id = _read_text(root / "sys/kernel/random/boot_id")
    if not wire_snapshot.boot_id:
        wire_snapshot.error = "cannot read boot_id"
        return

    try:
        process_roots = sorted(
            (item for item in root.iterdir() if item.name.isdigit()),
            key=lambda item: int(item.name),
        )
    except OSError as exc:
        wire_snapshot.error = f"cannot scan proc root: {exc}"[:256]
        return

    observed: list[tuple[Path, int, int]] = []
    parents: dict[int, int] = {}
    for process_root in process_roots:
        parsed = _parse_stat(process_root / "stat")
        if parsed is None:
            continue
        pid = int(process_root.name)
        parent_pid, start_ticks = parsed
        parents[pid] = parent_pid
        observed.append((process_root, pid, start_ticks))

    safe_capabilities = [str(item)[:64] for item in capabilities][:16]
    own_pid = self_pid or os.getpid()
    host_hostname = _read_text(root / "1/root/etc/hostname") or socket.gethostname()
    for process_root, pid, start_ticks in observed:
        if _is_agent_descendant(pid, own_pid, parents):
            continue
        namespace_inode = _namespace_inode(process_root / "ns/pid")
        namespace_pid = _namespace_pid(process_root / "status")
        if not namespace_inode or not namespace_pid:
            continue
        try:
            executable_identity = Path(os.readlink(process_root / "exe")).name[:256]
        except OSError:
            # Kernel threads and processes that disappear mid-scan cannot be
            # bound immutably; omitting them keeps the snapshot authoritative.
            continue
        if not executable_identity:
            continue
        if len(wire_snapshot.candidates) >= MAX_CANDIDATES:
            wire_snapshot.truncated = True
            return
        candidate = wire_snapshot.candidates.add()
        candidate.pid = pid
        candidate.process_start_ticks = start_ticks
        candidate.pid_namespace_inode = namespace_inode
        candidate.namespace_pid = namespace_pid
        candidate.comm = _read_text(process_root / "comm")
        candidate.executable_identity = executable_identity
        candidate.cgroup = _cgroup(process_root / "cgroup")
        candidate.service_hint, candidate.instance_hint = _hints(
            process_root, candidate.cgroup, host_hostname
        )
        candidate.collector_capabilities.extend(safe_capabilities)

    wire_snapshot.complete = True
