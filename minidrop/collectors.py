import os
import csv
import io
import re
import shutil
import subprocess
import tempfile
import threading
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


def _sample_resources(pid, duration, rate):
    samples = []
    interval = max(0.05, min(1.0, 1 / max(rate, 1)))
    deadline = time.time() + duration
    while time.time() < deadline:
        samples.append(_proc_sample(pid))
        time.sleep(interval)
    return samples


def _resource_stacks(pid, samples, prefix="proc"):
    proc = _process_name(pid)
    if not samples:
        return [{"stack": [prefix, proc, "no-resource-samples"], "value": 1}]
    first, last = samples[0], samples[-1]
    cpu_delta = max(1, int(last.get("cpu_ticks", 0) - first.get("cpu_ticks", 0)))
    write_delta = max(0, int(last.get("write_bytes", 0) - first.get("write_bytes", 0)))
    read_delta = max(0, int(last.get("read_bytes", 0) - first.get("read_bytes", 0)))
    rss_peak = max(int(item.get("rss_kb", 0)) for item in samples)
    stacks = [
        {"stack": [prefix, proc, "cpu-ticks"], "value": max(1, cpu_delta)},
        {"stack": [prefix, proc, "rss-peak"], "value": max(1, rss_peak // 1024)},
    ]
    if write_delta:
        stacks.append({"stack": [prefix, proc, "write-bytes"], "value": max(1, write_delta // 4096)})
    if read_delta:
        stacks.append({"stack": [prefix, proc, "read-bytes"], "value": max(1, read_delta // 4096)})
    if len(stacks) < 3:
        stacks.append({"stack": [prefix, proc, "sampler-loop"], "value": max(1, len(samples))})
    return stacks


def _parse_perf_script(text):
    stacks = []
    current = []
    for line in text.splitlines():
        if not line.strip():
            if current:
                stacks.append({"stack": list(reversed(current)), "value": 1})
                current = []
            continue
        if line[:1].isspace():
            fields = line.strip().split()
            if len(fields) >= 2:
                current.append(fields[1].split("+")[0])
    if current:
        stacks.append({"stack": list(reversed(current)), "value": 1})
    return stacks


def _collapsed_stacks(text, prefix=None):
    stacks = []
    for line in text.splitlines():
        if " " not in line:
            continue
        stack, value = line.rsplit(" ", 1)
        if value.isdigit():
            frames = [x for x in stack.split(";") if x]
            if prefix:
                frames.insert(0, prefix)
            stacks.append({"stack": frames, "value": int(value)})
    return stacks


class ResourceCollector:
    name = "resource"

    def collect(self, pid, duration, rate):
        samples = _sample_resources(pid, duration, rate)
        backend = "tasklist" if os.name == "nt" else "/proc"
        return {"samples": samples, "stacks": _resource_stacks(pid, samples, backend),
                "meta": {"collector": self.name, "backend": backend}}


class PerfCollector(ResourceCollector):
    name = "perf"

    def collect(self, pid, duration, rate):
        tool = shutil.which("perf")
        if not tool or os.name != "posix":
            result = super().collect(pid, duration, rate)
            result["meta"].update({"collector": self.name, "degraded": True, "reason": "perf unavailable"})
            return result
        samples = []
        error = []
        def sample():
            try:
                samples.extend(_sample_resources(pid, duration, rate))
            except Exception as exc:
                error.append(str(exc))
        worker = threading.Thread(target=sample)
        worker.start()
        with tempfile.TemporaryDirectory(prefix="minidrop-perf-") as tmp:
            data = str(Path(tmp) / "perf.data")
            run = subprocess.run(
                [tool, "record", "-F", str(rate), "-g", "-p", str(pid), "-o", data, "--", "sleep", str(duration)],
                capture_output=True, text=True, timeout=duration + 15,
            )
            script = subprocess.run([tool, "script", "-i", data], capture_output=True, text=True, timeout=20)
        worker.join()
        stacks = _parse_perf_script(script.stdout)
        degraded = run.returncode != 0 or script.returncode != 0 or not stacks
        if not stacks:
            stacks = _resource_stacks(pid, samples, "perf-fallback")
        return {
            "samples": samples,
            "stacks": stacks,
            "meta": {
                "collector": self.name, "backend": "perf record + perf script", "degraded": degraded,
                "reason": "perf produced no stack samples" if degraded else "",
                "stderr": (run.stderr + script.stderr + "".join(error))[-1000:],
            },
        }


class EBPFCollector(ResourceCollector):
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


class PySpyCollector(ResourceCollector):
    name = "pyspy"

    def collect(self, pid, duration, rate):
        tool = shutil.which("py-spy")
        if not tool:
            result = super().collect(pid, duration, rate)
            result["meta"].update({"collector": self.name, "degraded": True, "reason": "py-spy unavailable"})
            result["stacks"] = [{"stack": ["python", _process_name(pid), "language-frame"], "value": len(result["samples"])}]
            return result
        with tempfile.TemporaryDirectory(prefix="minidrop-pyspy-") as tmp:
            output = str(Path(tmp) / "stacks.txt")
            run = subprocess.run(
                [tool, "record", "--pid", str(pid), "--duration", str(duration), "--rate", str(rate),
                 "--format", "raw", "--output", output],
                capture_output=True, text=True, timeout=duration + 15,
            )
            raw = _read(output)
        result = {"samples": [], "stacks": _collapsed_stacks(raw, "python"), "meta": {}}
        try:
            result["samples"] = [_proc_sample(pid)]
        except ProcessLookupError:
            pass
        result["meta"].update({"collector": self.name, "backend": "py-spy", "degraded": run.returncode != 0})
        if not result["stacks"]:
            result["stacks"] = [{"stack": ["python", _process_name(pid), "no-language-samples"], "value": 1}]
            result["meta"].update({"degraded": True, "reason": "py-spy produced no language stack samples",
                                   "stderr": run.stderr[-1000:]})
        return result


COLLECTORS = {"perf": PerfCollector, "ebpf": EBPFCollector, "pyspy": PySpyCollector}


def get_collector(name):
    if name not in COLLECTORS:
        raise ValueError(f"unsupported collector: {name}")
    return COLLECTORS[name]()
