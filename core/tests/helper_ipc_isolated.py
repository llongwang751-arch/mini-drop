#!/usr/bin/env python3
"""以导入的 profiler 夹具验证真实 Unix 身份、字节流和恢复行为。

确定性的 shim 不是原生采样证据；测试不选择生产服务、网络、PID 或数据卷，
应在验证宿主上串行执行。
"""
import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
IMAGE = os.environ.get("DROP_HELPER_TEST_IMAGE", "drop-core-full-tools-base:v2")
RUN_ID = os.environ.get("DROP_CORE_EVIDENCE_RUN_ID", "")
if RUN_ID and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", RUN_ID):
    raise RuntimeError("Unsafe isolated evidence run ID")
REPORT = ROOT / ("core/evidence/helper-ipc" + ("-" + RUN_ID if RUN_ID else "") + ".json")
OWNER = secrets.token_hex(8)
PREFIX = f"drop-helper-ipc-{os.getpid()}-{OWNER}"


def command(argv, *, check=True, timeout=30):
    result = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError("Isolated helper fixture command failed: " + result.stderr[-600:])
    return result


def wait_for(predicate, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            result = predicate()
            if result:
                return result
        except (OSError, ValueError, RuntimeError):
            pass
        time.sleep(.1)
    raise RuntimeError("Isolated helper fixture bounded wait expired")


def main():
    if REPORT.exists():
        raise RuntimeError("Use a fresh run ID so stale IPC evidence cannot mask a failed rerun")
    # 这是运维选择的测试容器 I/O 上限，不是挂载或 profiler 目标；
    # 云宿主与最初验证宿主的设备条件可能不同。
    block_device = pathlib.Path(os.environ.get("DROP_HELPER_TEST_BLOCK_DEVICE", "/dev/nvme0n1")).resolve(strict=True)
    if not block_device.is_relative_to("/dev") or not stat.S_ISBLK(block_device.stat().st_mode):
        raise RuntimeError("Isolated helper I/O limit requires a real block device")
    outcomes = []
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / ".local").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=PREFIX + "-", dir=ROOT / ".local") as temporary:
        directory = pathlib.Path(temporary)
        for name in ("data", "incoming", "socket", "requests", "outputs", "tools"):
            (directory / name).mkdir(mode=0o755)
        os.chown(directory / "outputs", 10001, 10001)
        fixture = directory / "tools/helper-fixture-perf"
        shutil.copy2(ROOT / "build/drop_helper_fixture", fixture)
        fixture.chmod(0o755)
        os.symlink("helper-fixture-perf", directory / "tools/helper-fixture-perf2bolt")
        fixture_sha = hashlib.sha256(fixture.read_bytes()).hexdigest()
        script = ROOT / "core/tests/helper_ipc_wire.py"
        mounts = ["-v", f"{ROOT}/build/drop_collector_helper:/fixture/helper:ro", "-v", f"{ROOT}/build/drop_helper_probe:/fixture/probe:ro",
                  "-v", f"{ROOT}/contracts/task-kinds.json:/catalog.json:ro", "-v", f"{script}:/fixture/wire.py:ro"]
        for name in ("data", "incoming", "socket", "requests", "outputs", "tools"):
            mounts += ["-v", f"{directory / name}:/{name}" + (":ro" if name in ("tools", "requests") else "")]
        try:
            command(["docker", "run", "-d", "--init", "--name", PREFIX, "--label", "io.drop.owner=" + OWNER, "--label", "io.drop.purpose=isolated-helper-ipc",
                     "--network", "none", "--read-only", "--user", "0:10001", "--cap-drop", "ALL", "--cap-add", "SYS_PTRACE",
                     "--security-opt", "no-new-privileges:true", "--memory", "512m", "--memory-swap", "512m", "--cpus", ".5", "--pids-limit", "64",
                     "--device-read-bps", str(block_device) + ":8mb", "--device-write-bps", str(block_device) + ":4mb", "--tmpfs", "/tmp:rw,size=16m,mode=1777",
                     "-e", "COLLECTOR_HELPER_SOCKET=/socket/control.sock", "-e", "COLLECTOR_HELPER_WORK_DIR=/data",
                     "-e", "COLLECTOR_HELPER_INCOMING_DIR=/incoming", "-e", "COLLECTOR_HELPER_REQUIRE_BOUNDED_FS=false",
                     "-e", "ALLOW_HOST_PROCESSES=true", "-e", "ALLOWED_TARGET_UIDS=10001", "-e", "TASK_KINDS_FILE=/catalog.json",
                     "-e", "PERF_EXECUTABLE=/tools/helper-fixture-perf", "-e", "PERF_SHA256=" + fixture_sha,
                     "-e", "PERF2BOLT_EXECUTABLE=/tools/helper-fixture-perf2bolt", "-e", "PERF2BOLT_SHA256=" + fixture_sha,
                     "--entrypoint", "/fixture/helper", *mounts, IMAGE])
            wait_for(lambda: (directory / "socket/control.sock").exists(), timeout=60)
            command(["docker", "exec", "-d", "--user", "10001:10001", PREFIX, "/bin/sh", "-c", "echo $$ >/tmp/fixture-target.pid; exec /usr/bin/sleep 300"])
            pid = int(wait_for(lambda: command(["docker", "exec", PREFIX, "cat", "/tmp/fixture-target.pid"]).stdout.strip()))

            def request(name, value):
                path = directory / "requests" / (name + ".json")
                path.write_text(json.dumps(value), encoding="utf-8")
                return "/requests/" + path.name

            def probe(name, value, *, uid="10001:10001", socket="/socket/control.sock", expected=None):
                path = request(name, value)
                result = command(["docker", "exec", "--user", uid, PREFIX, "/fixture/probe", "/catalog.json", socket, path, "/outputs/" + name], check=False, timeout=30)
                response = json.loads(result.stdout.strip())
                if expected:
                    if response.get("ok") or response.get("error_code") != expected:
                        raise AssertionError((name, response, expected))
                elif result.returncode or not response.get("ok"):
                    raise AssertionError((name, response))
                return response

            def capture(name, token, **extra):
                return {"token": token, "kind": "PERF_CPU", "parameters": {"pid": pid, "duration_seconds": 1, "frequency_hz": 9}, **extra}

            capabilities = probe("capabilities", {"op": "capabilities"})
            assert capabilities["agent_uid"] == 10001 and capabilities["helper_uid"] == 0
            assert capabilities["disk_limit_bytes"] == 2147483648
            assert capabilities["capabilities"]["PERF_CPU"]["available"], capabilities["capabilities"]["PERF_CPU"]
            # 同组权限可通过文件系统检查，但 SO_PEERCRED 仍应拒绝不同 UID。
            probe("wrong-uid", {"op": "capabilities"}, uid="10002:10001", expected="HELPER_PEER_DENIED")
            outcomes.append({"case": "real_peer_credentials_and_fresh_group_volume", "passed": True})

            token = "a" * 64
            first = probe("first", capture("first", token))
            raw = directory / "outputs/first/profile.perf-script"
            assert raw.read_bytes().startswith(b"imported-ipc-fixture")
            assert hashlib.sha256(raw.read_bytes()).hexdigest() == first["sha256"]
            cache = directory / "data" / token
            assert (cache / "fixture-captures.log").read_text().count("invocation") == 1
            second = probe("replay", capture("replay", token))
            assert second["sha256"] == first["sha256"]
            assert (cache / "fixture-captures.log").read_text().count("invocation") == 1
            changed = capture("conflict", token)
            changed["parameters"]["frequency_hz"] = 8
            probe("conflict", changed, expected="INVALID_HELPER_REQUEST")
            probe("tamper-identity", capture("tamper", "b" * 64, tamper_start_time=True), expected="TARGET_VALIDATION_FAILED")
            outcomes.append({"case": "stream_sha_identity_request_idempotency", "passed": True, "raw_sha256": first["sha256"]})

            # 在 READY 后重启；原目标已退出，仍应重放持久化字节，而非重新采样。
            command(["docker", "restart", PREFIX], timeout=20)
            wait_for(lambda: (directory / "socket/control.sock").exists(), timeout=60)
            resumed = capture("resume", token, op="resume", saved_request="/outputs/first/helper-request.json")
            replay = probe("resume", resumed)
            assert replay["sha256"] == first["sha256"]
            probe("ack", {"op": "ack", "token": token})
            probe("ack-again", {"op": "ack", "token": token})
            probe("after-ack", resumed, expected="HELPER_ACKNOWLEDGED")
            assert not (cache / "profile.perf-script").exists()
            assert json.loads((cache / "result.json").read_text())["state"] == "ACKED"
            outcomes.append({"case": "cold_ready_replay_without_target_and_durable_ack_tombstone", "passed": True})

            # 为后续请求启动新的同 UID 目标，避免复用已退出进程。
            command(["docker", "exec", "-d", "--user", "10001:10001", PREFIX, "/bin/sh", "-c", "echo $$ >/tmp/fixture-target.pid; exec /usr/bin/sleep 300"])
            pid = int(wait_for(lambda: command(["docker", "exec", PREFIX, "cat", "/tmp/fixture-target.pid"]).stdout.strip()))
            bolt = capture("binary", "c" * 64, kind="BOLT_PROFILE", binary_max=536870912)
            result = probe("binary", bolt)
            binary = directory / "outputs/binary/target.elf"
            assert binary.read_bytes().startswith(b"\x7fELF") and result["format"] == "bolt_fdata"
            assert result["metadata"]["collection_isolation"]["effective_file_limit_bytes"] == 268435456
            binary_manifest = json.loads((directory / "data" / ("c" * 64) / "result.json").read_text())["files"][1]
            assert binary_manifest["size"] == binary.stat().st_size and binary_manifest["sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
            # 显式导入失败响应，用于验证真实 Helper IPC 客户端白名单；
            # 它们不能证明实际 profiler 采样成功。
            result_path = directory / "data" / ("c" * 64) / "result.json"
            original = result_path.read_text()
            for index, code in enumerate(("BOLT_BINARY_SAMPLES_UNAVAILABLE", "BOLT_PERF_CONVERSION_FAILED", "BOLT_AGGREGATION_FAILED", "BOLT_TARGET_MAPPING_UNVERIFIED")):
                failure = json.loads(original)
                failure.update(ok=False, state="FAILED", error_code=code)
                result_path.write_text(json.dumps(failure))
                probe("bolt-error-" + str(index), capture("binary-error", "c" * 64, op="resume", kind="BOLT_PROFILE", binary_max=536870912, saved_request="/outputs/binary/helper-request.json"), expected=code)
            result_path.write_text(original)
            outcomes.append({"case": "imported_bolt_failure_codes_survive_real_helper_ipc", "passed": True})
            probe("binary-ack", {"op": "ack", "token": "c" * 64})
            outcomes.append({"case": "full_default_bolt_budget_and_bounded_raw_binary_stream", "passed": True})

            canceled_token = "d" * 64
            cancel = capture("cancel", canceled_token, cancel_after_ms=1200)
            cancel["parameters"]["duration_seconds"] = 5
            path = request("cancel", cancel)
            running = subprocess.Popen(["docker", "exec", "--user", "10001:10001", PREFIX, "/fixture/probe", "/catalog.json", "/socket/control.sock", path, "/outputs/cancel"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            wait_for(lambda: (directory / "data" / canceled_token / "result.json").exists())
            probe("busy", capture("busy", "e" * 64), expected="HELPER_BUSY")
            stdout, _ = running.communicate(timeout=15)
            assert running.returncode == 1 and json.loads(stdout)["error_code"] == "TASK_CANCELED"
            failed = wait_for(lambda: (lambda value: value if value["state"] == "FAILED" else None)(json.loads((directory / "data" / canceled_token / "result.json").read_text())))
            assert failed["error_code"] == "TASK_CANCELED"
            probe("after-cancel", capture("after-cancel", "e" * 64))
            outcomes.append({"case": "token_cancel_busy_fence_and_active_release", "passed": True})

            # 私有日志存储失败也必须释放准入槽，不能永久卡在 BUSY。
            fault_token = "f" * 64
            value = capture("journal-fault", fault_token)
            value["parameters"]["duration_seconds"] = 2
            path = request("journal-fault", value)
            running = subprocess.Popen(["docker", "exec", "--user", "10001:10001", PREFIX, "/fixture/probe", "/catalog.json", "/socket/control.sock", path, "/outputs/journal-fault"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            fault = directory / "data" / fault_token
            wait_for(lambda: (fault / "result.json").exists())
            (fault / "result.json.tmp").mkdir()
            stdout, _ = running.communicate(timeout=20)
            assert running.returncode == 1 and not json.loads(stdout)["ok"]
            (fault / "result.json.tmp").rmdir()
            probe("after-journal-fault", capture("after-fault", "0" * 64))
            outcomes.append({"case": "persist_failure_releases_active_without_recollect", "passed": True})

            # 同 UID 伪服务不能冒充 root；已鉴权但格式畸形的输出也不能提交字节。
            for name, user, mode, expected in (("fake-user", "10001:10001", "peer", "HELPER_PEER_DENIED"),
                                                ("fake-sha", "0:10001", "sha", "HELPER_PROTOCOL_ERROR"),
                                                ("fake-fifo", "0:10001", "fifo", "DISK_BUDGET_EXCEEDED")):
                socket = "/outputs/" + name + ".sock" if mode == "peer" else "/socket/" + name + ".sock"
                if mode == "fifo":
                    output = directory / "outputs" / name
                    output.mkdir(); os.chown(output, 10001, 10001)
                    os.mkfifo(output / ".helper-profile.perf-script.tmp", 0o600); os.chown(output / ".helper-profile.perf-script.tmp", 10001, 10001)
                command(["docker", "exec", "-d", "--user", user, PREFIX, "/usr/bin/python3", "/fixture/wire.py", "serve", socket, mode])
                host_socket = directory / ("outputs" if mode == "peer" else "socket") / (name + ".sock")
                wait_for(host_socket.exists)
                probe(name, capture(name, "1" * 64), socket=socket, expected=expected)
                assert not (directory / "outputs" / name / "profile.perf-script").exists()
            outcomes.append({"case": "bidirectional_peer_auth_bad_sha_fifo_rejection", "passed": True})

            report = {"schema_version": 1, "passed": True, "cases": outcomes,
                      "binary_sha256":{name:hashlib.sha256((ROOT / "build" / name).read_bytes()).hexdigest() for name in ("drop_collector_helper","drop_helper_probe","drop_helper_fixture")},
                      "catalog_sha256":hashlib.sha256((ROOT / "contracts/task-kinds.json").read_bytes()).hexdigest(),
                      "fixture_source": "Explicitly imported deterministic profiler bytes; real Linux UID/UDS/Agent client/fsync/cache execution. Does not establish real perf/JVM/Python collection.",
                      "hard_filesystem_limit_validated": False, "bounded_fs_override": "false only in this isolated software/IPC fixture", "production_mutations": False}
            report["io_throttle_block_device"] = str(block_device)
            REPORT.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps({"passed": True, "cases": len(outcomes), "report": str(REPORT)}), flush=True)
        finally:
            ownership = command(["docker", "inspect", "--format", '{{index .Config.Labels "io.drop.owner"}}', PREFIX], check=False)
            if ownership.stdout.strip() == OWNER:
                state = command(["docker", "inspect", "--format", "{{.State.Status}} {{.State.ExitCode}}", PREFIX], check=False)
                logs = command(["docker", "logs", "--tail", "12", PREFIX], check=False)
                if not REPORT.exists():
                    print(json.dumps({"isolated_helper_state": state.stdout.strip(), "fixed_helper_logs": (logs.stdout + logs.stderr)[-2000:]}), flush=True)
                command(["docker", "rm", "-f", PREFIX], check=False)


if __name__ == "__main__":
    main()
