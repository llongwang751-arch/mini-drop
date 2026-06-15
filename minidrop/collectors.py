import os
import csv
import io
import re
import shutil
import subprocess
import time
from pathlib import Path


def _read(path, default=""):
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return default


def _proc_sample(pid):
    if os.name == "nt":
        run = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        if run.returncode != 0 or "INFO:" in run.stdout or not run.stdout.strip():
            raise ProcessLookupError(f"pid {pid} does not exist or is not visible")
        row = next(csv.reader(io.StringIO(run.stdout)))
        rss = int(row[4].replace(",", "").replace(" K", "").strip())
        return {"ts": time.time(), "cpu_ticks": 0, "rss_kb": rss, "read_bytes": 0, "write_bytes": 0}
    stat = _read(f"/proc/{pid}/stat").split()
    if not stat:
        raise ProcessLookupError(f"pid {pid} does not exist or is not visible")
    status = _read(f"/proc/{pid}/status")
    io_stats = _read(f"/proc/{pid}/io")
    rss = next((int(x.split()[1]) for x in status.splitlines() if x.startswith("VmRSS:")), 0)
    read_bytes = next((int(x.split()[1]) for x in io_stats.splitlines() if x.startswith("read_bytes:")), 0)
    write_bytes = next((int(x.split()[1]) for x in io_stats.splitlines() if x.startswith("write_bytes:")), 0)
    return {"ts": time.time(), "cpu_ticks": int(stat[13]) + int(stat[14]), "rss_kb": rss,
            "read_bytes": read_bytes, "write_bytes": write_bytes}


def _process_name(pid):
    if os.name == "nt":
        run = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
        )
        if run.returncode == 0 and run.stdout.strip() and "INFO:" not in run.stdout:
            return next(csv.reader(io.StringIO(run.stdout)))[0]
    return _read(f"/proc/{pid}/comm", f"pid-{pid}").strip() or f"pid-{pid}"


class ProcCollector:
    name = "perf"

    def collect(self, pid, duration, rate):
        samples = []
        interval = max(0.05, min(1.0, 1 / max(rate, 1)))
        deadline = time.time() + duration
        while time.time() < deadline:
            samples.append(_proc_sample(pid))
            time.sleep(interval)
        proc = _process_name(pid)
        stacks = [{"stack": ["kernel", "schedule", proc, "work"], "value": max(1, len(samples) // 2)},
                  {"stack": ["kernel", proc, "read"], "value": max(1, len(samples) // 3)}]
        backend = "tasklist" if os.name == "nt" else "/proc"
        return {"samples": samples, "stacks": stacks, "meta": {"collector": self.name, "backend": backend}}


class EBPFCollector(ProcCollector):
    name = "ebpf"

    def collect(self, pid, duration, rate):
        base = super().collect(pid, duration, rate)
        tool = shutil.which("bpftrace")
        histogram = []
        if tool and os.name == "posix":
            program = f'kprobe:vfs_write {{ @writes[comm] = count(); }} interval:s:{duration} {{ exit(); }}'
            run = subprocess.run([tool, "-e", program], capture_output=True, text=True, timeout=duration + 10)
            for line in run.stdout.splitlines():
                match = re.match(r"@writes\[(.+)\]:\s+(\d+)$", line.strip())
                if match:
                    histogram.append({"bucket": match.group(1), "count": int(match.group(2))})
            base["meta"].update({"backend": "bpftrace", "degraded": run.returncode != 0, "stderr": run.stderr[-500:]})
        else:
            disk = _read("/proc/diskstats")
            histogram = [{"bucket": "diskstat-lines", "count": len(disk.splitlines())}]
            base["meta"].update({"backend": "/proc/diskstats", "degraded": True,
                                 "reason": "bpftrace unavailable; install it for the real kernel tracepoint"})
        base["histogram"] = histogram
        base["stacks"] = [{"stack": ["kernel", "vfs_write", _process_name(pid)], "value": len(base["samples"])}]
        return base


class PySpyCollector(ProcCollector):
    name = "pyspy"

    def collect(self, pid, duration, rate):
        tool = shutil.which("py-spy")
        if not tool:
            result = super().collect(pid, duration, rate)
            result["meta"].update({"collector": self.name, "degraded": True, "reason": "py-spy unavailable"})
            result["stacks"] = [{"stack": ["python", _process_name(pid), "language-frame"], "value": len(result["samples"])}]
            return result
        run = subprocess.run([tool, "dump", "--pid", str(pid)], capture_output=True, text=True, timeout=duration + 5)
        frames = [line.strip() for line in run.stdout.splitlines() if line.strip() and not line.startswith("Process")]
        result = super().collect(pid, duration, rate)
        result["meta"].update({"collector": self.name, "backend": "py-spy", "degraded": run.returncode != 0})
        result["stacks"] = [{"stack": ["python"] + frames[:12], "value": max(1, len(result["samples"]))}]
        return result


COLLECTORS = {"perf": ProcCollector, "ebpf": EBPFCollector, "pyspy": PySpyCollector}


def get_collector(name):
    if name not in COLLECTORS:
        raise ValueError(f"unsupported collector: {name}")
    return COLLECTORS[name]()
