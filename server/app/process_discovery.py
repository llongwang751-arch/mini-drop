"""Host process discovery via /proc.

The API container runs with ``pid: host`` (same as the agents), so scanning
``/proc`` lists processes visible to the whole host. This feeds the Dashboard
quick-collection "目标 PID" dropdown so a user does not have to look up a busy
PID by hand. CPU is the process-lifetime average fraction of host uptime —
good enough to rank a demo hotspot, no external ``ps`` binary required.
"""

from __future__ import annotations

import os


def top_processes(limit: int = 20) -> list[dict]:
    """Return the top-N processes by lifetime CPU share, host-wide."""
    try:
        with open("/proc/uptime", encoding="utf-8") as handle:
            uptime = float(handle.read().split()[0])
    except (IOError, ValueError, IndexError):
        return []
    if uptime <= 0:
        return []

    entries: list[dict] = []
    try:
        proc_entries = os.listdir("/proc")
    except OSError:
        return []
    for name in proc_entries:
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
                parts = handle.read().split()
            # parts[1] = comm, parts[13] = utime, parts[14] = stime (clock ticks).
            comm = parts[1].strip("()")
            cpu_seconds = (int(parts[13]) + int(parts[14])) / 100.0
        except (IOError, IndexError, ValueError, PermissionError):
            continue
        cpu_percent = cpu_seconds / uptime * 100.0
        if cpu_percent <= 0:
            continue
        entries.append({"pid": pid, "comm": comm, "cpu_percent": round(cpu_percent, 1)})

    entries.sort(key=lambda entry: entry["cpu_percent"], reverse=True)
    return entries[: max(1, int(limit))]
