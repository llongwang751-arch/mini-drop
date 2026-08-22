"""Host distribution and profiler capability detection.

The control plane must schedule work from *runtime* capabilities rather than
from collectors merely compiled/imported into the Agent.  This module keeps
the detection side-effect free so it can also be used by installation checks
and TLinux compatibility tests.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import platform
import re
import shutil
from typing import Callable, Iterable


@dataclass(frozen=True)
class HostPlatform:
    distro_id: str
    distro_name: str
    version_id: str
    major_version: int | None
    kernel_release: str
    architecture: str
    package_manager: str | None
    is_tlinux: bool

    @property
    def tlinux_generation(self) -> int | None:
        return self.major_version if self.is_tlinux else None


@dataclass(frozen=True)
class CapabilityStatus:
    name: str
    available: bool
    reason: str


def parse_os_release(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _major_version(value: str) -> int | None:
    match = re.match(r"\s*(\d+)", value or "")
    return int(match.group(1)) if match else None


def detect_host_platform(
    *,
    os_release_text: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    kernel_release: str | None = None,
    architecture: str | None = None,
) -> HostPlatform:
    release = parse_os_release(
        os_release_text if os_release_text is not None else _read_text("/etc/os-release")
    )
    distro_id = release.get("ID", platform.system() or "unknown").lower()
    distro_name = release.get("PRETTY_NAME") or release.get("NAME") or distro_id
    version_id = release.get("VERSION_ID", "")
    identity = " ".join(
        [distro_id, distro_name, release.get("ID_LIKE", "")]
    ).lower()
    is_tlinux = any(token in identity for token in ("tlinux", "tencentos"))
    package_manager = next(
        (name for name in ("dnf", "yum", "apt-get") if which(name)),
        None,
    )
    return HostPlatform(
        distro_id=distro_id,
        distro_name=distro_name,
        version_id=version_id,
        major_version=_major_version(version_id),
        kernel_release=kernel_release or platform.release(),
        architecture=(architecture or platform.machine() or "unknown").lower(),
        package_manager=package_manager,
        is_tlinux=is_tlinux,
    )


def _async_profiler_available(which: Callable[[str], str | None]) -> bool:
    configured = os.getenv("ASYNC_PROFILER_BIN")
    candidates = [
        configured,
        which("asprof"),
        which("profiler.sh"),
        "/opt/async-profiler/bin/asprof",
        "/opt/async-profiler/profiler.sh",
    ]
    return any(candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK)
               for candidate in candidates)


def detect_collector_capabilities(
    registered: Iterable[str],
    *,
    which: Callable[[str], str | None] = shutil.which,
    path_exists: Callable[[str], bool] = os.path.exists,
) -> dict[str, CapabilityStatus]:
    """Return collector availability with an operator-readable reason."""
    tracefs = path_exists("/sys/kernel/tracing/available_events") or path_exists(
        "/sys/kernel/debug/tracing/available_events"
    )
    proc = path_exists("/proc/self/status")
    checks: dict[str, tuple[bool, str]] = {
        "perf_cpu": (bool(which("perf")), "requires perf"),
        "continuous_perf": (bool(which("perf")), "requires perf"),
        "ebpf_io": (
            bool(which("bpftrace")) and tracefs,
            "requires bpftrace and mounted tracefs",
        ),
        "pyspy": (bool(which("py-spy")), "requires py-spy"),
        "java_async": (
            _async_profiler_available(which),
            "requires async-profiler (asprof/profiler.sh)",
        ),
        # Raw pprof acquisition uses Python urllib; `go tool pprof` is only an
        # optional renderer and therefore must not block task scheduling.
        "go_pprof": (True, "HTTP pprof acquisition is built in"),
        "memory_smaps": (proc and path_exists("/proc/self/smaps"), "requires procfs smaps"),
        "sys_metrics": (proc, "requires procfs"),
    }
    result: dict[str, CapabilityStatus] = {}
    for name in sorted(set(registered)):
        available, requirement = checks.get(name, (False, "no runtime probe defined"))
        result[name] = CapabilityStatus(
            name=name,
            available=available,
            reason="available" if available else requirement,
        )
    return result


def build_compatibility_report(
    registered: Iterable[str],
    *,
    os_release_text: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    path_exists: Callable[[str], bool] = os.path.exists,
    kernel_release: str | None = None,
    architecture: str | None = None,
) -> dict[str, object]:
    host = detect_host_platform(
        os_release_text=os_release_text,
        which=which,
        kernel_release=kernel_release,
        architecture=architecture,
    )
    statuses = detect_collector_capabilities(
        registered,
        which=which,
        path_exists=path_exists,
    )
    supported_tlinux = not host.is_tlinux or host.tlinux_generation in {2, 3, 4}
    return {
        "host": asdict(host),
        "supported_tlinux_generation": supported_tlinux,
        "available_collectors": [name for name, item in statuses.items() if item.available],
        "unavailable_collectors": {
            name: item.reason for name, item in statuses.items() if not item.available
        },
        "kernel_features": {
            "tracefs": path_exists("/sys/kernel/tracing/available_events")
            or path_exists("/sys/kernel/debug/tracing/available_events"),
            "btf": path_exists("/sys/kernel/btf/vmlinux"),
            "procfs": path_exists("/proc/self/status"),
        },
    }


def compact_os_info(report: dict[str, object]) -> str:
    """Encode structured host metadata in the existing string proto field."""
    return json.dumps(
        {
            "host": report["host"],
            "kernel_features": report["kernel_features"],
            "unavailable_collectors": report["unavailable_collectors"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
