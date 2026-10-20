#!/usr/bin/env python3
"""在 UID10001 玩具进程上比较真实 RunnerBuilder 的 perf/raw 分配器调用栈。

这里只做诊断对照：raw-stack 变体或中断采集都不升级为生产 Runner 成功结果。
RAW 保留在仅 root 可读且有容量限制的 spool，对外只返回计数、摘要和固定错误码。
"""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time

from diagnostic_state import identity, limits, inspect_waits
from stack_controls import compare_argv, stop_gate_argv


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            value.update(block)
    return value.hexdigest()


def snapshot(pid):
    try:
        value = identity(pid)
        fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        result = {"identity":value, "state":fields[0], "cpu_ticks":int(fields[11])+int(fields[12])}
        try:
            counters = dict(line.split(":",1) for line in (Path("/proc") / str(pid) / "io").read_text().splitlines())
            result["io"] = {key:int(counters[key]) for key in ("syscw", "write_bytes")}
        except OSError as error:
            result["io_read_error"] = type(error).__name__
        return result
    except FileNotFoundError:
        return {"state":"exited"}


def main():
    if (len(sys.argv) not in (3,4) or (len(sys.argv)==4 and sys.argv[3]!="--stop-gate-only") or not re.fullmatch(r"[1-9][0-9]{0,9}", sys.argv[1]) or
        not re.fullmatch(r"stack-[a-f0-9]{32}", sys.argv[2])):
        raise RuntimeError("Use only the explicit toy PID and random stack token")
    pid = int(sys.argv[1])
    target = identity(pid)
    if pid <= 1 or pid == os.getpid() or target["uid"] != 10001 or os.geteuid() != 0:
        raise RuntimeError("Stack control requires the separate UID10001 toy")
    root = Path("/var/lib/drop-helper")
    capacity = os.statvfs(root)
    if capacity.f_frsize * capacity.f_blocks > 2147483648:
        raise RuntimeError("Stack control requires the physical bounded filesystem")
    directory = root / sys.argv[2]
    directory.mkdir(mode=0o700)
    manifest = json.loads(Path("/opt/drop-tools/manifest.json").read_text())
    tool = manifest["tools"]["bpftrace"]
    if tool["path"] != "/usr/bin/bpftrace" or digest(Path(tool["path"])) != tool["sha256"]:
        raise RuntimeError("Stack control refuses unpinned bpftrace")
    built = subprocess.run(["/fixture/drop_runner_probe", "--allocator-argv", str(pid)],
                           capture_output=True, text=True, timeout=10, check=True)
    stop_gate_only = len(sys.argv)==4
    variants = [stop_gate_argv(json.loads(built.stdout),pid)] if stop_gate_only else compare_argv(json.loads(built.stdout), pid)
    reports = []
    for mode, arguments in zip(("perf_stop_gate",) if stop_gate_only else ("perf", "raw"), variants):
        if identity(pid) != target:
            raise RuntimeError("Stack-control target identity changed")
        before = snapshot(pid)
        output_path = directory / (mode + ".raw")
        error_path = directory / (mode + ".err")
        started = time.monotonic()
        cpu_before = Path("/sys/fs/cgroup/cpu.stat").read_text()
        interrupted = False
        checkpoint = {}
        with output_path.open("xb") as output, error_path.open("xb") as errors:
            process = subprocess.Popen(arguments, stdout=output, stderr=errors,
                                       env={"PATH":"/usr/bin:/bin", "LC_ALL":"C"}, preexec_fn=limits)
            try:
                selected = identity(process.pid)
            except FileNotFoundError:
                # 语法或附加失败可能发生在采样 /proc 之前；应保留真实退出码，不能臆造成功。
                process.wait(timeout=2)
                selected = None
            executable = Path(tool["path"]).stat()
            if selected is not None and (selected["uid"] != 0 or selected["executable_device"] != executable.st_dev or
                selected["executable_inode"] != executable.st_ino):
                raise RuntimeError("Unexpected diagnostic process identity")
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                interrupted = True
                checkpoint = {"profiler":inspect_waits(selected), "target":inspect_waits(target)} if selected else {}
                if selected is None or identity(process.pid) != selected or os.getpgid(pid) == process.pid:
                    raise RuntimeError("Refuse changed debugger or business process group")
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    if identity(process.pid) != selected:
                        raise RuntimeError("Refuse changed diagnostic process")
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
            finally:
                if process.poll() is None and selected is not None and identity(process.pid) == selected and os.getpgid(pid) != process.pid:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
            output.flush()
            errors.flush()
            os.fsync(output.fileno())
            os.fsync(errors.fileno())
        info = output_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 268435456:
            raise RuntimeError("Diagnostic output crossed its regular-file or size bound")
        counts = {"alloc":0, "free":0, "stack_begin":0, "stack_end":0}
        with output_path.open("rb") as stream:
            for line in iter(lambda: stream.readline(65537), b""):
                if len(line) > 65536:
                    raise RuntimeError("Diagnostic record exceeded line bound")
                if line.startswith(b'{"schema_version":1,"event":"alloc"'):
                    counts["alloc"] += 1
                elif line.startswith(b'{"schema_version":1,"event":"free"'):
                    counts["free"] += 1
                elif line.strip() == b"DROP_STACK_BEGIN":
                    counts["stack_begin"] += 1
                elif line.strip() == b"DROP_STACK_END":
                    counts["stack_end"] += 1
        with error_path.open("rb") as stream:
            error = stream.read(65536).decode("utf-8", errors="replace")
        diagnostics = [re.sub(r"0x[0-9a-fA-F]+", "<address>", line[:1024]) for line in error.splitlines()
                       if any(word in line.lower() for word in ("error", "warning", "failed", "denied", "not permitted", "read-only"))]
        after = snapshot(pid)
        time.sleep(1)
        resumed = snapshot(pid)
        reports.append({"stack_format":mode, "returncode":process.returncode,
                        "interrupted_at_twenty_seconds":interrupted,
                        "wait_checkpoint":checkpoint,
                        "wall_seconds":round(time.monotonic()-started, 3),
                        "raw_size_bytes":info.st_size, "raw_sha256":digest(output_path), "counts":counts,
                        "program_sha256":hashlib.sha256(arguments[5].encode()).hexdigest(),
                        "target_before":before, "target_after":after, "target_resumed":resumed,
                        "target_identity_unchanged":after.get("identity")==resumed.get("identity")==target,
                        "target_cpu_progress":resumed.get("cpu_ticks",0)>after.get("cpu_ticks",0),
                        "target_io_progress":resumed.get("io",{}).get("syscw",0)>after.get("io",{}).get("syscw",0),
                        "helper_cpu_stat_before":cpu_before,
                        "helper_cpu_stat_after":Path("/sys/fs/cgroup/cpu.stat").read_text(),
                        "helper_memory_events":Path("/sys/fs/cgroup/memory.events").read_text(),
                        "diagnostics":diagnostics[:16]})
    print(json.dumps({"validation_mode":"diagnostic_only_stop_gate_control_not_capture_success" if stop_gate_only else "diagnostic_only_perf_raw_stack_comparison_not_capture_success",
                      "source":"actual_RunnerBuilder_only_stop_gate_added" if stop_gate_only else "actual_RunnerBuilder_only_stack_format_differs", "results":reports}))


if __name__ == "__main__":
    main()
