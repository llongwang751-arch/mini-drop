"""Pure compatibility matrix for Linux profiling collectors.

The result explains *why* a route is or is not available. It never upgrades a
collector merely because a binary exists; kernel policy, identity and explicit
application opt-in remain separate gates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class CollectorSupport:
    collector: str
    status: str
    reason: str
    required: tuple[str, ...]


def kernel_tuple(release: str) -> tuple[int, int]:
    match = re.match(r"^(\d+)\.(\d+)", release)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def evaluate_kernel_support(
    *,
    release: str,
    perf_event_paranoid: int,
    capabilities: set[str],
    tools: set[str],
    same_uid: bool,
    ptrace_scope: int,
    btf_available: bool,
    gperftools_opt_in: bool,
) -> dict:
    caps = {item.upper().removeprefix("CAP_") for item in capabilities}
    kernel = kernel_tuple(release)
    privileged_perf = "PERFMON" in caps or "SYS_ADMIN" in caps
    unprivileged_process_perf = same_uid and perf_event_paranoid <= 2
    rows: list[CollectorSupport] = []

    if "perf" not in tools:
        rows.append(CollectorSupport("perf_cpu", "UNAVAILABLE", "perf binary is missing", ("perf",)))
    elif privileged_perf or unprivileged_process_perf:
        reason = "CAP_PERFMON/CAP_SYS_ADMIN bypass" if privileged_perf else "same-UID process profiling allowed by perf_event_paranoid"
        rows.append(CollectorSupport("perf_cpu", "SUPPORTED", reason, ("perf", "kernel perf_event policy")))
    else:
        rows.append(CollectorSupport("perf_cpu", "UNAVAILABLE", "kernel perf_event policy denies this identity", ("CAP_PERFMON or same UID with perf_event_paranoid<=2",)))

    ebpf_caps = "SYS_ADMIN" in caps or ({"BPF", "PERFMON"} <= caps)
    if "bpftrace" in tools and ebpf_caps and btf_available:
        rows.append(CollectorSupport("ebpf_io", "SUPPORTED", "bpftrace, BTF and eBPF capabilities are available", ("bpftrace", "BTF", "CAP_BPF+CAP_PERFMON")))
    elif "bpftrace" in tools and (ebpf_caps or btf_available):
        rows.append(CollectorSupport("ebpf_io", "DEGRADED", "one of BTF or eBPF capability gates is missing", ("bpftrace", "BTF", "eBPF capabilities")))
    else:
        rows.append(CollectorSupport("ebpf_io", "UNAVAILABLE", "bpftrace/kernel eBPF prerequisites are incomplete", ("bpftrace", "BTF", "eBPF capabilities")))

    pyspy_allowed = same_uid and ptrace_scope <= 1
    rows.append(CollectorSupport(
        "pyspy",
        "SUPPORTED" if "py-spy" in tools and pyspy_allowed else "DEGRADED" if "py-spy" in tools else "UNAVAILABLE",
        "same-UID Python sampler allowed by ptrace_scope" if "py-spy" in tools and pyspy_allowed else "py-spy needs both the target identity and an allowed ptrace policy" if "py-spy" in tools else "py-spy binary is missing",
        ("py-spy", "same UID / ptrace policy"),
    ))
    rows.append(CollectorSupport(
        "cpp_gperftools",
        "SUPPORTED" if "mini-drop-gperftools-bridge" in tools and same_uid and gperftools_opt_in else "UNAVAILABLE",
        "application opted into libprofiler signal capture" if gperftools_opt_in and same_uid else "requires same-UID application opt-in via CPUPROFILE and CPUPROFILESIGNAL",
        ("libprofiler app opt-in", "same UID", "mini-drop-gperftools-bridge"),
    ))
    rows.append(CollectorSupport("sys_metrics", "SUPPORTED", "read-only procfs fallback", ("/proc",)))
    return {
        "schema_version": "kernel_compatibility.v1",
        "kernel_release": release,
        "kernel_major_minor": list(kernel),
        "cap_perfmon_available_since_kernel_5_8": kernel >= (5, 8),
        "perf_event_paranoid": perf_event_paranoid,
        "ptrace_scope": ptrace_scope,
        "collectors": [asdict(row) for row in rows],
        "fallback_order": ["language_runtime", "perf_cpu", "cpp_gperftools", "sys_metrics"],
    }
