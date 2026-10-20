#!/usr/bin/env python3
"""Actual bounded-FS/helper capture of isolated UID10001 toy processes.

No production network, PID, credentials or volume is selected. The optional
uprobe_multi check records an actual count and verbose attachment diagnostics;
it never promotes zero samples or a failed attachment to supported collection.
"""
import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPOOL = pathlib.Path(os.environ["DROP_HELPER_SPOOL"]).resolve(strict=True)
RUN_ID = os.environ.get("DROP_CORE_EVIDENCE_RUN_ID", "")
if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", RUN_ID):
    raise RuntimeError("Use a fresh bounded evidence run ID")
OUTPUT = ROOT / "core/evidence" / ("helper-advanced-" + RUN_ID)
OUTPUT.mkdir()
OWNER = secrets.token_hex(8)
PREFIX = "drop-helper-advanced-" + str(os.getpid()) + "-" + OWNER
TOOLS = os.environ.get("DROP_CORE_TOOLS_IMAGE", "drop-core-full-tools-base:v2")
SECCOMP = pathlib.Path(os.environ.get("DROP_HELPER_SECCOMP_ROOT", str(ROOT / "deploy/seccomp")))
LIMITS = ["--memory", "512m", "--memory-swap", "512m", "--cpus", ".5", "--pids-limit", "96",
          "--device-read-bps", "/dev/nvme0n1:8mb", "--device-write-bps", "/dev/nvme0n1:4mb"]
CROSS_UID_SIGNALS = os.environ.get("DROP_HELPER_CROSS_UID_SIGNALS", "true")
if CROSS_UID_SIGNALS not in ("true", "false"):
    raise RuntimeError("Cross-UID signal fixture policy must be explicit")


def command(arguments, timeout=40, check=True):
    result = subprocess.run(list(map(str, arguments)), capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError("Isolated helper command failed: " + (result.stdout + result.stderr)[-2000:])
    return result


def wait_for(predicate, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            value = predicate()
            if value:
                return value
        except (RuntimeError, ValueError, FileNotFoundError):
            pass
        time.sleep(.2)
    raise RuntimeError("Isolated helper fixture exceeded its readiness deadline")


def execute(arguments, **options):
    return command(["docker", "exec", PREFIX, *arguments], **options)


def remove_owned(name):
    for _ in range(3):
        try:
            owner = command(["docker", "inspect", "--format", '{{index .Config.Labels "io.drop.owner"}}', name], check=False)
            if owner.returncode != 0:
                message = (owner.stdout + owner.stderr).lower()
                return "no such object" in message or "no such container" in message
            if owner.stdout.strip() != OWNER:
                return False
            if command(["docker", "rm", "-f", name], check=False).returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
    return False


def cleanup_owned(names):
    # Evaluate every result before all(): one failed removal cannot skip others.
    results = [remove_owned(name) for name in names]
    return all(results)


private = []
for suffix in ("cache", "incoming", "socket"):
    path = SPOOL / (PREFIX + "-" + suffix)
    path.mkdir(mode=0o700)
    private.append(path)
cache, incoming, socket = private
for name in ("requests", "outputs", "assets", "target-data"):
    (OUTPUT / name).mkdir()
for name in ("outputs", "target-data"):
    os.chown(OUTPUT / name, 10001, 10001)
control = incoming / "native-control"
control.mkdir(mode=0o700)
os.chown(control, 10001, 10001)
os.chown(incoming, 0, 10001)
incoming.chmod(0o1770)
reports = []
container_started = False
targets = []
target_names = {}


def probe(name, value):
    request = OUTPUT / "requests" / (name + ".json")
    request.write_text(json.dumps(value))
    response = command(["docker", "exec", "--user", "10001:10001", PREFIX, "/fixture/drop_helper_probe",
                        "/catalog.json", "/run/drop-helper/control.sock", "/requests/" + request.name,
                        "/outputs/" + name], timeout=150, check=False)
    try:
        result = json.loads(response.stdout)
    except ValueError:
        raise RuntimeError("Isolated helper client returned no bounded JSON result") from None
    return result


def start_target(name, arguments, environment=()):
    # Fixed fixture-only shell creates the pinned PID marker, then exec replaces
    # that shell. Profiler production execution continues to use argv directly.
    script = "echo $$ >/target-data/" + name + ".pid; exec " + " ".join(arguments)
    target = PREFIX + "-target-" + name
    targets.append(target)
    command(["docker", "run", "-d", "--name", target, "--label", "io.drop.owner=" + OWNER,
             "--label", "io.drop.purpose=isolated-helper-business-toy", "--pid", "container:" + PREFIX,
             "--network", "container:" + PREFIX, "--read-only", "--user", "10001:10001", "--cap-drop", "ALL",
             "--security-opt", "no-new-privileges:true", "--memory", "192m" if name == "python" else "96m", "--memory-swap", "192m" if name == "python" else "96m", "--cpus", ".1", "--pids-limit", "32",
             "--tmpfs", "/tmp:rw,size=32m,mode=1777", "-v", f"{incoming}:/run/drop-helper-incoming",
             "-v", f"{OUTPUT / 'assets'}:/assets:ro", "-v", f"{OUTPUT / 'target-data'}:/target-data",
             "-v", f"{ROOT}/core/fixtures/python_workload.py:/fixture/python-workload.py:ro",
             "-v", f"{ROOT}/build/drop_native_workload:/fixture/drop_native_workload:ro",
             "-v", f"{ROOT}/build/libdrop_native_profiler.so:/fixture/libdrop_native_profiler.so:ro",
             *environment, "--entrypoint", "/bin/sh", TOOLS, "-c", script])
    pid = int(wait_for(lambda: (OUTPUT / "target-data" / (name + ".pid")).read_text().strip()))
    target_names[pid] = target
    return pid


def diagnose_gdb(pid):
    """Read-only toy attachment: retain only fixed metadata and tool errors."""
    metadata = (
        "python import os,pathlib; print('DROP_GDB_UID='+str(os.geteuid())); "
        "print('DROP_GDB_CAPEFFECTIVE='+next(x.split(':',1)[1].strip() for x in pathlib.Path('/proc/self/status').read_text().splitlines() if x.startswith('CapEff:')))"
    )
    def safe_check(source, label):
        script = "try:\n " + source + "\nexcept Exception as error:\n print('DROP_GDB_CHECK_ERROR=" + label + ":'+type(error).__name__+':'+str(error))"
        return "python exec(" + repr(script) + ")"
    readable = safe_check("import gdb; p=gdb.parse_and_eval('(void*)&PyMem_Malloc'); gdb.selected_inferior().read_memory(int(p),1); print('DROP_GDB_SYMBOL_READABLE=true')", "read")
    printable = safe_check("import gdb; [gdb.execute('p '+n,to_string=True) for n in ('PyMem_Malloc','PyMem_Calloc','PyMem_Realloc','PyMem_Free')]; print('DROP_GDB_FOUR_SYMBOLS_PRINTABLE=true')", "print")
    shared = safe_check("import gdb; gdb.execute('sharedlibrary libpython',to_string=True); print('DROP_GDB_SHARED_LIBRARY_COMMAND=true')", "sharedlibrary")
    metadata = safe_check(metadata.removeprefix("python ") + "; print('DROP_GDB_MOUNTNS_SAME='+str(os.stat('/proc/self/ns/mnt').st_ino==os.stat('/proc/" + str(pid) + "/ns/mnt').st_ino)); f=os.open('/proc/" + str(pid) + "/ns/mnt',os.O_RDONLY); os.close(f); print('DROP_GDB_TARGET_MOUNTNS_READABLE=true')", "metadata")
    snapshot = "import pathlib,json,os; p=pathlib.Path('/proc/" + str(pid) + "'); s=p.joinpath('stat').read_text().rsplit(')',1)[1].split(); t=p.joinpath('status').read_text().splitlines(); e=p.joinpath('exe').stat(); print(json.dumps({'pid':" + str(pid) + ",'uid':int(next(x.split(':',1)[1].split()[0] for x in t if x.startswith('Uid:'))),'start_time_ticks':int(s[19]),'pid_namespace':os.readlink(p/'ns/pid'),'namespace_pid':int(next(x.split(':',1)[1].split()[-1] for x in t if x.startswith('NSpid:'))),'executable_device':e.st_dev,'executable_inode':e.st_ino}))"
    identity = json.loads(execute(["/usr/bin/python3", "-c", snapshot]).stdout)
    result = {}
    variants = [("ptrace_launcher", "/opt/drop-tools/gdb-bin/gdb", []),
                ("unfiltered_control", "/usr/bin/gdb", []),
                ("ptrace_runner_limits", "/opt/drop-tools/gdb-bin/gdb", ["/usr/bin/prlimit", "--nofile=64:64", "--nproc=32:32", "--as=8589934592:8589934592", "--cpu=120:120", "--fsize=268435456:268435456"])]
    explicit = os.environ.get("DROP_HELPER_GDB_EXPLICIT_PROC_CONTROL", "false")
    if explicit not in ("true", "false"): raise RuntimeError("Explicit proc control must be true or false")
    if explicit == "true": variants.append(("ptrace_explicit_proc_elf", "/opt/drop-tools/gdb-bin/gdb", []))
    for name, path, prefix in variants:
        fixed = ["-iex", "set sysroot /proc/" + str(pid) + "/root", "--exec=/proc/" + str(pid) + "/exe", "--symbols=/proc/" + str(pid) + "/exe"] if name == "ptrace_explicit_proc_elf" else []
        sampled = execute(["/usr/bin/env", "-i", "PATH=/opt/drop-tools/gdb-bin:/usr/bin:/bin", "LC_ALL=C", "DROP_GDB_TARGET_IDENTITY=" + json.dumps(identity), *prefix,
                           path, *fixed, "--batch", "--nx", "--nh", "--pid", str(pid),
                           "-ex=set pagination off", "-ex=" + shared, "-ex=show data-directory", "-ex=" + metadata,
                           "-ex=" + printable, "-ex=" + readable, "-ex=detach"],
                          timeout=30, check=False)
        selected = []
        for line in (sampled.stdout + sampled.stderr).splitlines():
            # No backtrace, evaluated pointers, source lines or memory bytes are
            # retained. This process and every path belong to the explicit toy.
            if line.startswith(("DROP_GDB_", "Symbol \"PyMem_Malloc\"", "GDB's data directory", "warning:", "Warning:", "Error:", "error:", "BFD:", "Cannot ", "No symbol ", "Python Exception", "Traceback ", "PermissionError:", "OSError:", "FileNotFoundError:", "gdb.MemoryError:", "gdb.error:")):
                selected.append(re.sub(r"0x[0-9a-fA-F]+", "<address>", line[:2000]))
        result[name] = {"returncode":sampled.returncode, "diagnostics":selected[:64],
                        "symbol_readable": "DROP_GDB_SYMBOL_READABLE=true" in sampled.stdout,
                        "four_symbols_printable": "DROP_GDB_FOUR_SYMBOLS_PRINTABLE=true" in sampled.stdout}
    state = json.loads(command(["docker", "inspect", "--format", "{{json .State}}", target_names[pid]]).stdout)
    result["toy_after_detach"] = {"running":state["Running"], "oom_killed":state["OOMKilled"], "exit_code":state["ExitCode"]}
    (OUTPUT / "gdb-readonly-diagnostic.json").write_text(json.dumps(result, indent=2) + "\n")


try:
    alias = OUTPUT / "assets/gdb-bin"
    alias.mkdir(mode=0o755)
    (alias / "gdb").symlink_to("/usr/local/bin/drop_gdb_ptrace")
    snapshot = "import runpy,sys,pathlib; sys.argv=['tools-install.py','--manifest-only']; runpy.run_path('/fixture/tools-install.py',run_name='__main__'); print(pathlib.Path('/opt/drop-tools/manifest.json').read_text())"
    manifest = json.loads(command(["docker", "run", "--rm", "--name", PREFIX + "-manifest", "--label", "io.drop.owner=" + OWNER, "--label", "io.drop.purpose=isolated-helper-tool-manifest", "--network", "none", "--cap-drop", "ALL", "--user", "0:10001", "--memory", "192m", "--memory-swap", "192m", "--cpus", ".5", "--pids-limit", "32", "--device-read-bps", "/dev/nvme0n1:8mb", "--device-write-bps", "/dev/nvme0n1:4mb", "-v", f"{ROOT}/core/tools-install.py:/fixture/tools-install.py:ro", "-v", f"{ROOT}/build/drop_gdb_ptrace:/usr/local/bin/drop_gdb_ptrace:ro", "--entrypoint", "/usr/bin/python3", TOOLS, "-c", snapshot], timeout=90).stdout)
    (OUTPUT / "assets/tool-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    perf_script = "import pathlib,hashlib,json; p=next(pathlib.Path('/usr/lib/linux-tools').glob('*/perf')); print(json.dumps({'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))"
    perf = json.loads(command(["docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL", "--read-only", "--user", "10001:10001", "--memory", "96m", "--cpus", ".2", "--entrypoint", "/usr/bin/python3", TOOLS, "-c", perf_script]).stdout)
    command(["docker", "run", "--rm", "--name", PREFIX + "-compile", "--label", "io.drop.purpose=isolated-helper-toy-build", "--label", "io.drop.owner=" + OWNER,
             "--network", "none", "--cap-drop", "ALL", *LIMITS, "-v", f"{ROOT}:/src:ro", "-v", f"{OUTPUT / 'assets'}:/out",
             "--entrypoint", "/usr/bin/g++", "drop-core-toolchain:v2", "-std=c++20", "-O1", "-fno-builtin-malloc", "-fno-builtin-free",
             "-g", "-fno-omit-frame-pointer", "/src/core/fixtures/io_workload.cpp", "-o", "/out/io-workload"], timeout=90)
    arguments = ["docker", "run", "-d", "--init", "--name", PREFIX, "--label", "io.drop.purpose=isolated-helper-advanced", "--label", "io.drop.owner=" + OWNER,
                 "--network", "none", "--read-only", "--user", "0:10001", "--cap-drop", "ALL", "--cap-add", "PERFMON",
                 "--cap-add", "SYS_PTRACE", "--cap-add", "BPF", "--cap-add", "DAC_READ_SEARCH",
                 *(["--cap-add", "KILL"] if CROSS_UID_SIGNALS == "true" else []), *LIMITS,
                 "--security-opt", "no-new-privileges:true", "--security-opt", f"seccomp={SECCOMP}/agent-ebpf.json",
                 "--ulimit", "memlock=67108864:67108864", "--tmpfs", "/tmp:rw,size=128m,mode=1777",
                 "-e", "COLLECTOR_HELPER_SOCKET=/run/drop-helper/control.sock", "-e", "COLLECTOR_HELPER_WORK_DIR=/var/lib/drop-helper",
                 "-e", "COLLECTOR_HELPER_INCOMING_DIR=/run/drop-helper-incoming", "-e", "COLLECTOR_HELPER_REQUIRE_BOUNDED_FS=true",
                 "-e", "ALLOW_HOST_PROCESSES=true", "-e", "ALLOWED_TARGET_UIDS=10001", "-e", "TASK_KINDS_FILE=/catalog.json",
                 "-e", "PERF_EXECUTABLE=" + perf["path"], "-e", "PERF_SHA256=" + perf["sha256"],
                 "-v", f"{OUTPUT / 'assets/tool-manifest.json'}:/opt/drop-tools/manifest.json:ro",
                 "-v", f"{ROOT}/build/drop_gdb_ptrace:/usr/local/bin/drop_gdb_ptrace:ro", "-v", f"{alias}:/opt/drop-tools/gdb-bin:ro",
                 "-v", f"{cache}:/var/lib/drop-helper", "-v", f"{incoming}:/run/drop-helper-incoming", "-v", f"{socket}:/run/drop-helper",
                 "-v", f"{OUTPUT / 'outputs'}:/outputs", "-v", f"{OUTPUT / 'requests'}:/requests:ro", "-v", f"{OUTPUT / 'assets'}:/assets:ro",
                 "-v", f"{OUTPUT / 'target-data'}:/target-data", "-v", f"{ROOT}/contracts/task-kinds.json:/catalog.json:ro",
                 "-v", "/sys/kernel/tracing:/sys/kernel/tracing:ro", "-v", "/sys/kernel/btf:/sys/kernel/btf:ro",
                 "-v", f"{ROOT}/core/fixtures/python_workload.py:/fixture/python-workload.py:ro",
                 "-v", f"{ROOT}/core/tests/memray_stage_diagnostic.py:/fixture/memray-stage-diagnostic.py:ro",
                 "-v", f"{ROOT}/core/tests/attach_stages.py:/fixture/attach_stages.py:ro",
                 "-v", f"{ROOT}/core/tests/diagnostic_state.py:/fixture/diagnostic_state.py:ro",
                 "-v", f"{ROOT}/core/tests/stack_controls.py:/fixture/stack_controls.py:ro",
                 "-v", f"{ROOT}/core/tests/allocator_stack_diagnostic.py:/fixture/allocator-stack-diagnostic.py:ro"]
    for binary in ("drop_collector_helper", "drop_helper_probe", "drop_runner_probe", "drop_native_workload", "libdrop_native_profiler.so"):
        arguments += ["-v", f"{ROOT}/build/{binary}:/fixture/{binary}:ro"]
    arguments += ["--entrypoint", "/fixture/drop_collector_helper", TOOLS]
    command(arguments)
    container_started = True
    wait_for(lambda: (socket / "control.sock").is_socket())
    capabilities = probe("capabilities", {"op":"capabilities"})
    (OUTPUT / "capabilities.json").write_text(json.dumps(capabilities, indent=2) + "\n")
    environment = execute(["/usr/bin/python3", "-c", "import json,pathlib,resource; s=pathlib.Path('/proc/self/status').read_text(); print(json.dumps({'status':s,'memlock':resource.getrlimit(resource.RLIMIT_MEMLOCK),'memory_max':pathlib.Path('/sys/fs/cgroup/memory.max').read_text().strip(),'cpu_max':pathlib.Path('/sys/fs/cgroup/cpu.max').read_text().strip()}))"]).stdout
    environment = json.loads(environment)
    environment["filesystem_capacity_bytes"] = os.statvfs(cache).f_frsize * os.statvfs(cache).f_blocks
    environment["filesystem_same_device"] = cache.stat().st_dev == incoming.stat().st_dev
    environment["incoming_mode"] = oct(incoming.stat().st_mode & 0o7777)
    environment["validation_mode"] = "allocator_stack_diagnostic" if os.environ.get("DROP_HELPER_STACK_DIAGNOSTIC_ONLY") == "true" else ("memray_stage_diagnostic" if os.environ.get("DROP_HELPER_MEMRAY_DIAGNOSTIC_ONLY") == "true" else ("read_only_gdb_diagnostic" if os.environ.get("DROP_HELPER_GDB_DIAGNOSTIC_ONLY") == "true" else "actual_helper_capture"))
    environment["fixture_source"] = "Explicit UID10001 toys; diagnostic-only mode is not successful profile collection evidence; no credentials or production mutations"
    environment["image_id"] = command(["docker", "image", "inspect", "--format", "{{.Id}}", TOOLS]).stdout.strip()
    environment["binary_sha256"] = {name:hashlib.sha256((ROOT / "build" / name).read_bytes()).hexdigest() for name in ("drop_collector_helper", "drop_helper_probe")}
    environment["tool_manifest_sha256"] = hashlib.sha256((OUTPUT / "assets/tool-manifest.json").read_bytes()).hexdigest()
    environment["catalog_sha256"] = hashlib.sha256((ROOT / "contracts/task-kinds.json").read_bytes()).hexdigest()
    environment["memory_events_before"] = execute(["cat", "/sys/fs/cgroup/memory.events"]).stdout
    (OUTPUT / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")
    native = start_target("native", ["/fixture/drop_native_workload"], ["-e", "NATIVE_CONTROL_ROOT=/run/drop-helper-incoming/native-control", "-e", "LD_LIBRARY_PATH=/fixture"])
    io = start_target("io", ["/assets/io-workload"], ["-e", "DROP_IO_FIXTURE_DIRECTORY=/target-data"])
    python = start_target("python", ["/opt/drop-tools/venv/bin/python", "/fixture/python-workload.py"])
    time.sleep(2)
    diagnostic_only = os.environ.get("DROP_HELPER_GDB_DIAGNOSTIC_ONLY", "false")
    if diagnostic_only not in ("true", "false"):
        raise RuntimeError("GDB diagnostic-only switch must be true or false")
    memray_diagnostic_only = os.environ.get("DROP_HELPER_MEMRAY_DIAGNOSTIC_ONLY", "false")
    stack_diagnostic_only = os.environ.get("DROP_HELPER_STACK_DIAGNOSTIC_ONLY", "false")
    stack_stop_gate_only = os.environ.get("DROP_HELPER_STACK_STOP_GATE_ONLY", "false")
    if (memray_diagnostic_only not in ("true", "false") or stack_diagnostic_only not in ("true", "false") or
        stack_stop_gate_only not in ("true", "false") or (stack_stop_gate_only=="true" and stack_diagnostic_only!="true") or
        sum(value == "true" for value in (diagnostic_only, memray_diagnostic_only, stack_diagnostic_only)) > 1):
        raise RuntimeError("Choose at most one literal diagnostic-only mode")
    if diagnostic_only == "true":
        diagnose_gdb(python)
    if memray_diagnostic_only == "true":
        result = execute(["/usr/bin/python3", "/fixture/memray-stage-diagnostic.py", str(python), "stage-" + secrets.token_hex(16)], timeout=40)
        (OUTPUT / "memray-stage-diagnostic.json").write_text(json.dumps(json.loads(result.stdout), indent=2) + "\n")
    if stack_diagnostic_only == "true":
        result = execute(["/usr/bin/python3", "/fixture/allocator-stack-diagnostic.py", str(io), "stack-" + secrets.token_hex(16), *(["--stop-gate-only"] if stack_stop_gate_only=="true" else [])], timeout=60)
        (OUTPUT / "allocator-stack-diagnostic.json").write_text(json.dumps(json.loads(result.stdout), indent=2) + "\n")
    cases = [("BOLT_PROFILE", native, {})] + [("IO_TRACE", io, {"category":category}) for category in ("syscalls", "vfs", "block")] + [("MEMORY_TRACE", io, {"category":category}) for category in ("pagefault", "alloc", "mmap")] + [("PYTHON_CPU", python, {}), ("PYTHON_MEMORY", python, {})]
    selected = os.environ.get("DROP_HELPER_ADVANCED_CASES", "")
    if selected:
        names = selected.split(",")
        allowed = {kind + (":" + options["category"] if "category" in options else "") for kind, _, options in cases}
        if not names or len(set(names)) != len(names) or any(name not in allowed for name in names):
            raise RuntimeError("Unknown or duplicate advanced fixture cases")
        cases = [case for case in cases if case[0] + (":" + case[2]["category"] if "category" in case[2] else "") in names]
    if "true" in (diagnostic_only, memray_diagnostic_only, stack_diagnostic_only):
        cases = []
    for kind, pid, options in cases:
        category = options.get("category")
        name = kind.lower() + ("-" + category if category else "")
        token = secrets.token_hex(32)
        if kind in ("IO_TRACE", "MEMORY_TRACE"):
            options.update(max_events=10000, stack_depth=16)
        value = {"op":"collect", "token":token, "kind":kind, "parameters":{"pid":pid, "duration_seconds":2, "frequency_hz":99, "options":options}}
        if kind == "BOLT_PROFILE": value["binary_max"] = 536870912
        started = time.monotonic()
        result = probe(name, value)
        result["wall_seconds"] = round(time.monotonic() - started, 3)
        result.update(kind=kind, category=category, fixture_target_uid=10001)
        target_state = json.loads(command(["docker", "inspect", "--format", "{{json .State}}", target_names[pid]]).stdout)
        result["fixture_target"] = {"running":target_state["Running"], "oom_killed":target_state["OOMKilled"], "exit_code":target_state["ExitCode"], "memory_limit_bytes":201326592 if kind.startswith("PYTHON") else 100663296}
        private_result = cache / token / "runner-result.json"
        if private_result.is_file(): result["tool_process"] = json.loads(private_result.read_text())
        failure_detail = cache / token / "failure-detail.json"
        if failure_detail.is_file(): result["private_fixture_failure"] = json.loads(failure_detail.read_text())
        result["memory_events_after"] = execute(["cat", "/sys/fs/cgroup/memory.events"]).stdout
        if kind == "PYTHON_MEMORY":
            observed = execute(["/usr/bin/python3", "-c", "import pathlib,json,time; p=pathlib.Path('/proc/" + str(pid) + "'); a=(p/'stat').read_text().rsplit(')',1)[1].split(); time.sleep(1); b=(p/'stat').read_text().rsplit(')',1)[1].split(); status=dict(l.split(':',1) for l in (p/'status').read_text().splitlines() if ':' in l); print(json.dumps({'state':b[0],'tracer_pid':int(status['TracerPid']),'cpu_ticks_before':int(a[11])+int(a[12]),'cpu_ticks_after':int(b[11])+int(b[12])}))"]).stdout
            result["target_resumption"] = json.loads(observed)
            resumed = result["target_resumption"]
            if resumed["state"] in ("T", "t", "Z", "X") or resumed["tracer_pid"] != 0 or resumed["cpu_ticks_after"] <= resumed["cpu_ticks_before"]:
                raise RuntimeError("Memory attach permission/capture did not preserve target progress and detachment")
        if not result.get("ok") and kind in ("PYTHON_MEMORY", "MEMORY_TRACE"):
            diagnostic = cache / token / "runner.log"
            if diagnostic.is_file():
                text = diagnostic.read_text(errors="replace")
                if kind == "MEMORY_TRACE":
                    # execute merges private trace stdout/stderr. Keep only
                    # errors in the diagnostic artifact, never stack/RAW rows.
                    text = "\n".join(line for line in text.splitlines() if any(word in line.lower() for word in ("error", "failed", "permission", "denied", "warning", "not permitted", "read-only")))
                (OUTPUT / (name + "-tool-diagnostic.txt")).write_text(text[-5000:])
        if result.get("ok"):
            isolation = result.get("metadata", {}).get("collection_isolation", {})
            if isolation.get("agent_uid") != 10001 or isolation.get("helper_uid") != 0 or isolation.get("bounded_filesystem") is not True:
                raise RuntimeError("Actual helper metadata lost its different UIDs or bounded physical filesystem")
            raw = OUTPUT / "outputs" / name / result["name"]
            if raw.stat().st_size != result["size"] or hashlib.sha256(raw.read_bytes()).hexdigest() != result["sha256"]:
                raise RuntimeError("Actual helper RAW stream SHA/size mismatch")
            if kind in ("IO_TRACE", "MEMORY_TRACE") and not raw.read_bytes().strip():
                raise RuntimeError("Trace helper returned an empty success")
            if kind == "MEMORY_TRACE" and category == "alloc":
                observed = execute(["/usr/bin/python3", "-c", "import pathlib,json,time; p=pathlib.Path('/proc/" + str(pid) + "'); a=(p/'stat').read_text().rsplit(')',1)[1].split(); x=dict(l.split(':',1) for l in (p/'io').read_text().splitlines()); time.sleep(1); b=(p/'stat').read_text().rsplit(')',1)[1].split(); y=dict(l.split(':',1) for l in (p/'io').read_text().splitlines()); print(json.dumps({'state':b[0],'cpu_ticks_before':int(a[11])+int(a[12]),'cpu_ticks_after':int(b[11])+int(b[12]),'write_calls_before':int(x['syscw']),'write_calls_after':int(y['syscw'])}))"]).stdout
                result["target_resumption"] = json.loads(observed)
                state = result["target_resumption"]
                if state["state"] in ("T", "t", "Z", "X") or not (state["cpu_ticks_after"] > state["cpu_ticks_before"] or state["write_calls_after"] > state["write_calls_before"]):
                    raise RuntimeError("Allocator trace left the business toy stopped or unable to progress")
            if kind == "PYTHON_MEMORY":
                if not result["fixture_target"]["running"] or result["fixture_target"]["oom_killed"]:
                    raise RuntimeError("Memory attachment did not preserve the live toy target")
                reader = "import memray,json; r=memray.FileReader('/raw/allocations.bin'); n=0;\nfor record in r.get_allocation_records():\n n+=1\n if n>1000000: raise RuntimeError('Reader fixture record budget exceeded')\nprint(json.dumps({'records':n,'total_allocations':r.metadata.total_allocations,'fully_read':True}))"
                verified = command(["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--user", "10001:10001", "--security-opt", "no-new-privileges:true", "--memory", "192m", "--memory-swap", "192m", "--cpus", ".2", "-v", f"{raw.parent}:/raw:ro", "--entrypoint", "/opt/drop-tools/venv/bin/python", TOOLS, "-c", reader], timeout=60)
                result["memray_reader"] = json.loads(verified.stdout)
                if result["memray_reader"]["records"] <= 0: raise RuntimeError("Actual memray RAW contains no allocation records")
                if hashlib.sha256(raw.read_bytes()).hexdigest() != result["sha256"]:
                    raise RuntimeError("Memory RAW changed after full FileReader validation")
            result["acknowledged"] = probe(name + "-ack", {"op":"ack", "token":token}).get("ok") is True
            if not result["acknowledged"]: raise RuntimeError("Actual helper cache ACK failed")
        reports.append(result)
        print(json.dumps({key:result[key] for key in ("kind", "category", "ok", "size", "error_code") if key in result}), flush=True)
        (OUTPUT / "evidence.json").write_text(json.dumps(reports, indent=2) + "\n")
    if diagnostic_only == memray_diagnostic_only == stack_diagnostic_only == "false" and ("MEMORY_TRACE", "alloc") in cases:
        # Diagnose multi-uprobe support separately from the production Runner. The
        # glob list must resolve to exactly the requested allocator symbol first.
        maps = execute(["cat", "/proc/" + str(io) + "/maps"]).stdout.splitlines()
        libc = next(line[line.index("/"):] for line in maps if "libc.so" in line and "/" in line)
        library = "/proc/" + str(io) + "/root" + libc
        multi = {"library_basename":pathlib.Path(libc).name, "glob_matches":{}, "positive_hits":False}
        for symbol, pattern in (("malloc", "ma*lloc"), ("free", "fr*ee")):
            listing = execute(["/usr/bin/bpftrace", "-l", "uprobe:" + library + ":" + pattern], timeout=30, check=False)
            names = [line.strip() for line in listing.stdout.splitlines() if line.strip().startswith("uprobe:")]
            multi["glob_matches"][symbol] = names
            multi.setdefault("glob_diagnostics", {})[symbol] = {"returncode":listing.returncode, "stderr":listing.stderr[-3000:]}
            if listing.returncode or names != ["uprobe:" + library + ":" + symbol]:
                multi["error"] = "Allocator glob did not resolve exactly one intended symbol"
                break
        else:
            program = "uprobe:" + library + ":ma*lloc { @hits=count(); } interval:s:2 { exit(); }"
            captured = execute(["/usr/bin/bpftrace", "-v", "-p", str(io), "-e", program], timeout=40, check=False)
            multi.update(returncode=captured.returncode, stdout=captured.stdout[-5000:], stderr=captured.stderr[-5000:])
            multi["positive_hits"] = captured.returncode == 0 and any(int(value) > 0 for value in re.findall(r"@hits:\s*(\d+)", captured.stdout))
        (OUTPUT / "uprobe-multi-diagnostic.json").write_text(json.dumps(multi, indent=2) + "\n")
finally:
    if container_started:
        try:
            logs = command(["docker", "logs", "--tail", "12", PREFIX], check=False, timeout=15)
            (OUTPUT / "service.log").write_text(logs.stdout + logs.stderr)
        except (OSError, subprocess.TimeoutExpired):
            (OUTPUT / "service.log").write_text("Fixture log collection timed out; no native diagnostic exported.\n")
    if not cleanup_owned([*reversed(targets), PREFIX, PREFIX + "-compile", PREFIX + "-manifest"]):
        raise RuntimeError("Fixture cleanup incomplete; private bounded spool retained for owned recovery")
    for path in private:
        if path.parent == SPOOL and path.name.startswith(PREFIX + "-"):
            shutil.rmtree(path)
if not all(result.get("ok") for result in reports) or len(reports) != len(cases):
    raise SystemExit(1)
