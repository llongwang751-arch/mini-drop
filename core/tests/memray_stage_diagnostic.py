#!/usr/bin/env python3
"""Diagnostic-only attach to the explicit UID10001 Python toy, never production.

The parent fixture owns the helper/container and a real bounded receive mount.
No verbose backtrace, evaluated pointer, command line or RAW byte is persisted.
Only the verified UID0 debugger child may be interrupted; never the business PID.
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

from attach_stages import instrument_attach, stage_names
from diagnostic_state import identity, limits, inspect_waits


def main():
    if len(sys.argv) != 3 or not re.fullmatch(r"[1-9][0-9]{0,9}", sys.argv[1]) or not re.fullmatch(r"stage-[a-f0-9]{32}",sys.argv[2]):
        raise RuntimeError("Use only the explicit toy PID and random stage token")
    pid = int(sys.argv[1])
    target = identity(pid)
    if pid <= 1 or pid == os.getpid() or target["uid"] != 10001 or os.geteuid() != 0:
        raise RuntimeError("Stage diagnostic requires the separate UID10001 toy")
    same_network_namespace = os.readlink("/proc/self/ns/net") == os.readlink("/proc/"+str(pid)+"/ns/net")
    incoming = Path("/run/drop-helper-incoming")
    capacity = os.statvfs(incoming)
    if capacity.f_frsize * capacity.f_blocks > 2147483648:
        raise RuntimeError("Stage diagnostic requires the physical bounded filesystem")
    directory = incoming / sys.argv[2]
    directory.mkdir(mode=0o1770)
    directory.chmod(0o1770)
    raw = directory / "allocations.bin"
    manifest = json.loads(Path("/opt/drop-tools/manifest.json").read_text())
    tool = manifest["tools"]["memray"]
    if hashlib.sha256(Path(tool["path"]).read_bytes()).hexdigest() != tool["sha256"]:
        raise RuntimeError("Stage diagnostic refuses unpinned memray")
    scripts = [Path(name) for name in tool["support_files"] if name.endswith("/commands/_attach.gdb")]
    if len(scripts) != 1:
        raise RuntimeError("Stage diagnostic requires exactly one pinned upstream GDB script")
    original_script = scripts[0]
    original_bytes = original_script.read_bytes()
    if hashlib.sha256(original_bytes).hexdigest() != tool["support_files"][str(original_script)]:
        raise RuntimeError("Stage diagnostic refuses modified upstream GDB script")
    attach_module = original_script.with_name("attach.py")
    if hashlib.sha256(attach_module.read_bytes()).hexdigest() != tool["support_files"].get(str(attach_module)):
        raise RuntimeError("Stage diagnostic refuses modified memray attach module")
    diagnostic_directory = Path("/var/lib/drop-helper") / sys.argv[2]
    diagnostic_directory.mkdir(mode=0o700)
    script = diagnostic_directory / "stages.gdb"
    script.write_text(instrument_attach(original_bytes.decode("utf-8")))
    script.chmod(0o600)
    gdb = Path(manifest["tools"]["gdb"]["path"]).stat()
    environment = {"PATH":"/opt/drop-tools/gdb-bin:/usr/bin:/bin", "LC_ALL":"C", "PYTHONUNBUFFERED":"1",
                   "DROP_GDB_MANIFEST_FILE":"/opt/drop-tools/manifest.json", "DROP_GDB_TARGET_IDENTITY":json.dumps(target)}
    started = time.monotonic()
    cpu_before = Path("/sys/fs/cgroup/cpu.stat").read_text()
    checkpoint = {}
    # Only this diagnostic process substitutes the pinned script with its
    # instrumented copy. The production memray package/manifest is untouched.
    entry = ("import pathlib,sys; import memray.commands.attach as attach; "
             "from memray.commands import main; attach.GDB_SCRIPT=pathlib.Path(sys.argv.pop(1)); "
             "sys.argv[0]='memray'; raise SystemExit(main())")
    process = subprocess.Popen(["/opt/drop-tools/venv/bin/python", "-c", entry, str(script), "attach", "--method", "gdb", "--verbose", "--output", str(raw), "--duration", "2", str(pid)],
                               env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, preexec_fn=limits)
    parent = identity(process.pid)
    interrupted = []
    phase = "completed"
    try:
        try:
            output = process.communicate(timeout=20)[0]
        except subprocess.TimeoutExpired:
            phase = "twenty_second_debugger_checkpoint"
            target_fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")",1)[1].split()
            checkpoint["target_state_before_debugger_interrupt"] = target_fields[0]
            checkpoint["target_waits_before_debugger_interrupt"] = inspect_waits(target)
            checkpoint["debugger_children"] = []
            children = Path("/proc") / str(process.pid) / "task" / str(process.pid) / "children"
            debugger = []
            for value in children.read_text().split()[:16]:
                selected = identity(int(value))
                if selected["uid"] == 0 and selected["executable_device"] == gdb.st_dev and selected["executable_inode"] == gdb.st_ino:
                    debugger.append(selected)
                    fields = (Path("/proc") / value / "stat").read_text().rsplit(")",1)[1].split()
                    checkpoint["debugger_children"].append({"pid":selected["pid"], "state":fields[0], "cpu_ticks":int(fields[11])+int(fields[12]), "waits":inspect_waits(selected)})
            for selected in debugger:
                # Recheck identity and direct parent immediately before signalling.
                state = (Path("/proc") / str(selected["pid"]) / "stat").read_text().rsplit(")",1)[1].split()
                if identity(selected["pid"]) != selected or int(state[1]) != process.pid:
                    raise RuntimeError("Debugger identity changed before checkpoint")
                os.kill(selected["pid"], signal.SIGINT)
                interrupted.append({"pid":selected["pid"], "uid":0, "signal":"INT"})
            try:
                output = process.communicate(timeout=3)[0]
            except subprocess.TimeoutExpired:
                for selected in debugger:
                    try:
                        if identity(selected["pid"]) == selected:
                            os.kill(selected["pid"], signal.SIGKILL)
                            interrupted.append({"pid":selected["pid"], "uid":0, "signal":"KILL"})
                    except FileNotFoundError:
                        pass
                output = process.communicate(timeout=3)[0]
    except subprocess.TimeoutExpired:
        phase = "bounded_parent_checkpoint"
        if identity(process.pid) != parent or os.getpgid(pid) == process.pid:
            raise RuntimeError("Diagnostic parent or business process group changed")
        os.killpg(process.pid, signal.SIGKILL)
        output = process.communicate(timeout=2)[0]
    finally:
        if process.poll() is None:
            if identity(process.pid) == parent and os.getpgid(pid) != process.pid:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
    selected = []
    sentinels = ("MEMRAY: Attached to process.", "MEMRAY: Checking if process is Python 3.7+.",
                 "MEMRAY: Process is Python 3.7+.", "Checking if we can access the library",
                 "cannot open shared object file", '"SUCCESS"', '"FAILURE"')
    for line in output[:1048576].splitlines():
        found = next((value for value in sentinels if value in line), None)
        if found:
            selected.append(found)
        elif line.startswith(("warning:", "Warning:", "Error:", "error:", "Cannot ", "Could not ", "Couldn't ", "No symbol ", "DROP_GDB_", "The program being debugged timed out")) or "Error in sourced command file" in line:
            selected.append(re.sub(r"0x[0-9a-fA-F]+", "<address>", line[:1024]))
    try:
        target_after = identity(pid)
        fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")",1)[1].split()
        before_ticks = int(fields[11]) + int(fields[12])
        time.sleep(1)
        after = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")",1)[1].split()
        target_state = {"state":after[0], "cpu_ticks_before":before_ticks, "cpu_ticks_after":int(after[11])+int(after[12])}
    except FileNotFoundError:
        target_after = None
        target_state = {"state":"exited"}
    report = {"validation_mode":"diagnostic_only_interrupted_debugger_is_not_successful_capture", "phase":phase,
              "wall_seconds":round(time.monotonic()-started,3), "returncode":process.returncode,
              "debugger_interruptions":interrupted, "checkpoint":checkpoint, "stage_diagnostics":selected[:64], "target_identity_unchanged":target_after==target,
              "target_after_checkpoint":target_state, "helper_cpu_stat_before":cpu_before, "helper_cpu_stat_after":Path("/sys/fs/cgroup/cpu.stat").read_text(),
              "helper_memory_events":Path("/sys/fs/cgroup/memory.events").read_text()}
    report["fixed_stage_markers"] = stage_names(output[:1048576])
    report["thread_debugging_libthread_db_enabled"] = "Thread debugging using libthread_db enabled" in output[:1048576]
    report["upstream_script_sha256"] = hashlib.sha256(original_bytes).hexdigest()
    report["diagnostic_script_sha256"] = hashlib.sha256(script.read_bytes()).hexdigest()
    report["diagnostic_call_timeout_seconds"] = 5
    report["diagnostic_unwind_on_timeout"] = True
    report["target_network_namespace_same"] = same_network_namespace
    if raw.exists():
        file = raw.lstat()
        if stat.S_ISREG(file.st_mode) and file.st_nlink == 1:
            report["partial_raw_size_bytes"] = file.st_size
    print(json.dumps(report))


if __name__ == "__main__":
    main()
