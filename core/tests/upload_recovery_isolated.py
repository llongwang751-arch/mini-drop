#!/usr/bin/env python3
"""真实 PROC_CPU、PUT 与 PostgreSQL 恢复回归，不接触生产数据。

在专用验证宿主上使用保留发布镜像和已构建的新二进制，六种情况在显式资源上限内串行执行。
"""
import hashlib
import json
import os
import pathlib
import re
import subprocess
import secrets
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
TLS = pathlib.Path(sys.argv[1]).resolve(strict=True)
TOOLS = os.environ.get("DROP_CORE_TOOLS_IMAGE", "drop-core-full-tools-base:v2")
RUNTIME = os.environ.get("DROP_V1_IMAGE", "drop-core:20261016.1")
HELPER = os.environ.get("DROP_HELPER_MODE", "0") == "1"
COOPERATIVE_KIND = os.environ.get("DROP_COOPERATIVE_KIND", "")
if COOPERATIVE_KIND not in ("", "JAVA_CPU", "JAVA_HEAP", "GPERF_CPU", "GPERF_HEAP", "JEPROF_HEAP"):
    raise RuntimeError("Unknown cooperative fixture kind")
if HELPER and COOPERATIVE_KIND:
    raise RuntimeError("Use one execution model per isolated fixture")
COOPERATIVE = bool(COOPERATIVE_KIND)
JAVA_EVENT = os.environ.get("DROP_JAVA_EVENT", "cpu")
if JAVA_EVENT not in ("cpu", "alloc", "lock"):
    raise RuntimeError("Unknown Java fixture event")
KIND = COOPERATIVE_KIND or ("PERF_CPU" if HELPER else "PROC_CPU")
RAW_NAME = {"JAVA_CPU":"java.folded", "JAVA_HEAP":"heap.hprof", "GPERF_CPU":"native.profile", "GPERF_HEAP":"native.profile", "JEPROF_HEAP":"native.profile"}.get(KIND, "profile.perf-script" if HELPER else "profile.folded")
SPOOL = pathlib.Path(os.environ["DROP_HELPER_SPOOL"]).resolve(strict=True) if HELPER or COOPERATIVE else None
SECCOMP = pathlib.Path(os.environ.get("DROP_HELPER_SECCOMP_ROOT", str(ROOT / "deploy/seccomp")))
output_name = "cooperative-upload-recovery-" + KIND.lower() + ("-" + JAVA_EVENT if KIND == "JAVA_CPU" else "") if COOPERATIVE else ("helper-upload-recovery" if HELPER else "upload-recovery")
RUN_ID = os.environ.get("DROP_CORE_EVIDENCE_RUN_ID", "")
if RUN_ID and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", RUN_ID):
    raise RuntimeError("Use a fresh bounded evidence run ID")
if RUN_ID: output_name += "-" + RUN_ID
OUTPUT = ROOT / "core/evidence" / output_name
if RUN_ID and OUTPUT.exists(): raise RuntimeError("Do not overwrite an earlier recovery evidence run")
OUTPUT.mkdir(parents=True, exist_ok=True)
BINARY_HASHES = {name:hashlib.sha256((ROOT / "build" / name).read_bytes()).hexdigest() for name in ("drop_agent", "drop_server", "drop_collector_helper")}
MODES = os.environ.get("DROP_RECOVERY_CASES", "mixed,permanent,restart,late_log,manifest_retry,manifest_restart").split(",")
if not MODES or any(value not in ("mixed", "permanent", "restart", "late_log", "manifest_retry", "manifest_restart") for value in MODES) or len(set(MODES)) != len(MODES):
    raise RuntimeError("Unknown or duplicate isolated recovery cases")


def command(arguments, check=True):
    result = subprocess.run(list(map(str, arguments)), capture_output=True, text=True, timeout=45)
    if check and result.returncode:
        # 只保留容器和工具诊断；命令文本可能含短期 PUT URL，不能直接输出。
        raise RuntimeError("Private recovery fixture command failed: " + result.stderr[-600:])
    return result.stdout.strip()


def run_case(mode):
    owner = secrets.token_hex(8)
    prefix = f"drop-core-recovery-{os.getpid()}-{owner}-{mode}"
    network = prefix + "-network"
    containers = []
    directory = OUTPUT / mode
    directory.mkdir(exist_ok=True)
    journal = directory / "journal"
    journal.mkdir(mode=0o700, exist_ok=True)
    if any(journal.iterdir()):
        raise RuntimeError("Recovery fixture journal must start empty")
    os.chown(journal, 10001, 10001)
    helper = None
    perf_options = []
    if HELPER:
        # 保留的 tools base 早于 perf manifest 条目；应像发布的 --manifest-only 构建
        # 一样显式固定该不可变镜像中的二进制，而不是禁用完整性检查。
        script = "import pathlib,hashlib,json; p=next(pathlib.Path('/usr/lib/linux-tools').glob('*/perf')); print(json.dumps({'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))"
        perf = json.loads(command(["docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL", "--read-only", "--user", "10001:10001", "--memory", "96m", "--cpus", ".2", "--entrypoint", "/usr/bin/python3", TOOLS, "-c", script]))
        perf_options = ["-e", "PERF_EXECUTABLE=" + perf["path"], "-e", "PERF_SHA256=" + perf["sha256"]]
    helper_directories = []
    if HELPER or COOPERATIVE:
        for suffix in ("cache", "incoming", "socket"):
            path = SPOOL / (prefix + "-" + suffix)
            path.mkdir(mode=0o700)
            helper_directories.append(path)
        helper_cache, helper_incoming, helper_socket = helper_directories
        # 先让 Agent 登记，再启动 Helper；宿主 spool 准备器只拥有固定接收根目录，
        # 各任务目录仍为 0700。
        os.chown(helper_incoming, 0, 10001)
        helper_incoming.chmod(0o1770)

    def start(suffix, options, image, arguments):
        container = prefix + "-" + suffix
        containers.append(container)
        command(["docker", "run", "-d", "--name", container, "--label", "io.drop.purpose=isolated-core-recovery", "--label", "io.drop.owner=" + owner, "--network", network, *options, image, *arguments])
        return container

    def sql(statement):
        return command(["docker", "exec", pg, "psql", "-U", "postgres", "-d", "drop", "-v", "ON_ERROR_STOP=1", "-qtAc", statement])

    def wait_value(get, predicate=bool, timeout=90):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                value = get()
                if predicate(value):
                    return value
            except (RuntimeError, ValueError):
                pass
            time.sleep(.3)
        raise RuntimeError("Private recovery fixture bounded wait expired")

    def certs(role):
        return ["-v", f"{TLS}/ca.crt:/tls/ca.crt:ro", "-v", f"{TLS}/{role}.crt:/tls/identity.crt:ro", "-v", f"{TLS}/{role}.key:/tls/identity.key:ro", "-e", "TLS_CA_FILE=/tls/ca.crt", "-e", "TLS_CERT_FILE=/tls/identity.crt", "-e", "TLS_KEY_FILE=/tls/identity.key"]

    def sink(path="/stats", method="GET"):
        script = 'import urllib.request; print(urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:9000' + path + '",method="' + method + '")).read().decode())'
        result = command(["docker", "exec", uploads, "python3", "-c", script])
        return json.loads(result) if result else {}

    try:
        command(["docker", "network", "create", "--internal", "--label", "io.drop.owner=" + owner, network])
        migrations = sorted((ROOT / "db/migrations").glob("*.sql"))
        mounts = [item for file in migrations for item in ["-v", f"{file}:/docker-entrypoint-initdb.d/{file.name}:ro"]]
        schema = max(int(file.name.split("_", 1)[0]) for file in migrations)
        pg = start("pg", ["--network-alias", "postgres", "--memory", "192m", "--cpus", ".2", "--tmpfs", "/var/lib/postgresql/data:rw,size=128m", "-e", "POSTGRES_DB=drop", "-e", "POSTGRES_PASSWORD=isolated-fixture", *mounts], "postgres:17.11-alpine", ["postgres", "-c", "shared_buffers=16MB", "-c", "max_connections=12"])
        wait_value(lambda: sql(f"SELECT 1 FROM schema_migrations WHERE version={schema}"))
        time.sleep(2)
        server = start("server", ["--network-alias", "drop-server", "--read-only", "--cap-drop", "ALL", "--user", "10001:10001", "--memory", "96m", "--cpus", ".2", "--tmpfs", "/tmp:rw,size=16m", "-e", "DATABASE_URL=postgresql://postgres:isolated-fixture@postgres/drop", "-e", "METRICS_LISTEN=off", "-e", "TASK_KINDS_FILE=/catalog.json", "-v", f"{ROOT}/contracts/task-kinds.json:/catalog.json:ro", "-v", f"{ROOT}/build/drop_server:/usr/local/bin/drop_server:ro", *certs("server")], RUNTIME, [])
        uploads = start("uploads", ["--network-alias", "uploads", "--read-only", "--cap-drop", "ALL", "--user", "10001:10001", "--memory", "128m" if COOPERATIVE else "48m", "--cpus", ".1", "--tmpfs", "/tmp:rw,size=64m,mode=1777" if COOPERATIVE else "/tmp:rw,size=4m,mode=1777", "--entrypoint", "/usr/bin/python3", "-e", "FIXTURE_MODE=" + mode, "-e", "FIXTURE_RAW_NAME=" + RAW_NAME, "-v", f"{ROOT}/core/tests/upload_recovery_sink.py:/sink.py:ro"], TOOLS, ["/sink.py"])
        helper_agent_options = []
        if HELPER or COOPERATIVE:
            helper_agent_options = ["-e", "COLLECTOR_HELPER_SOCKET=/run/drop-helper/control.sock", "-v", f"{helper_socket}:/run/drop-helper:ro", "-v", f"{helper_incoming}:/run/drop-helper-incoming"]
        if COOPERATIVE:
            control = helper_incoming / "fixture-control"
            control.mkdir(mode=0o700)
            os.chown(control, 10001, 10001)
            helper_agent_options += ["-e", "ALLOW_HOST_PROCESSES=true", "-e", "ALLOWED_TARGET_UIDS=10001", "-e", "NATIVE_CONTROL_ROOT=/run/drop-fixture-control", "-v", f"{control}:/run/drop-fixture-control"]
        agent_memory = "192m" if HELPER or COOPERATIVE else "128m"
        agent = start("agent", ["--init", *helper_agent_options,"--read-only", "--cap-drop", "ALL", "--user", "10001:10001", "--memory", agent_memory, "--memory-swap", agent_memory, "--cpus", ".25", "--tmpfs", "/tmp:rw,size=16m,mode=1777", "--entrypoint", "/usr/local/bin/drop_agent", "-e", "AGENT_ID=aliyun-local", "-e", "RUN_CONTROLLED_WORKLOAD=" + ("false" if COOPERATIVE else "true"), "-e", "DROP_SERVER_ADDRESS=drop-server:50051", "-e", "RAW_RECOVERY_RETENTION_SECONDS=300", "-e", "RAW_RECOVERY_DISK_BYTES=536870912" if COOPERATIVE else "RAW_RECOVERY_DISK_BYTES=134217728", "-v", f"{ROOT}/build/drop_agent:/usr/local/bin/drop_agent:ro", "-v", f"{ROOT}/build/drop_workload:/usr/local/bin/drop_workload:ro", "-v", f"{ROOT}/contracts/task-kinds.json:/etc/drop/task-kinds.json:ro", "-v", f"{journal}:/var/lib/drop-agent", *certs("agent")], TOOLS, [])
        if COOPERATIVE:
            target_options = ["--pid", "container:" + agent, "--read-only", "--cap-drop", "ALL", "--user", "10001:10001", "--memory", "192m", "--memory-swap", "192m", "--cpus", ".2", "--pids-limit", "64", "--tmpfs", "/tmp:rw,size=64m,mode=1777", "-v", f"{helper_incoming}:/run/drop-helper-incoming", "-v", f"{control}:/run/drop-fixture-control"]
            if KIND.startswith("JAVA"):
                # 源码模式只在本夹具独立业务 cgroup 内编译这个小程序；
                # 实际 profiler 写入不是模拟输出。
                target_options += ["--entrypoint", "/usr/bin/java", "-v", f"{ROOT}/core/fixtures/DropJvmFixture.java:/fixture/DropJvmFixture.java:ro"]
                target_arguments = ["-Xmx48m", "-Xms16m", "-XX:ReservedCodeCacheSize=32m", "-XX:MaxMetaspaceSize=48m", "-XX:+StartAttachListener", "/fixture/DropJvmFixture.java"]
                process_name = "java"
            else:
                library = {"GPERF_CPU":"libprofiler.so", "GPERF_HEAP":"libtcmalloc.so", "JEPROF_HEAP":"libjemalloc.so"}[KIND]
                target_options += ["--entrypoint", "/fixture/drop_native_workload", "-e", "NATIVE_CONTROL_ROOT=/run/drop-fixture-control", "-e", "LD_PRELOAD=" + library, "-e", "LD_LIBRARY_PATH=/fixture", "-e", "MALLOC_CONF=prof:true,prof_active:false,lg_prof_sample:0", "-v", f"{ROOT}/build/drop_native_workload:/fixture/drop_native_workload:ro", "-v", f"{ROOT}/build/libdrop_native_profiler.so:/fixture/libdrop_native_profiler.so:ro"]
                target_arguments = []
                process_name = "drop_native_workload"
            target = start("target", target_options, TOOLS, target_arguments)
            process_sql = "SELECT p->>'pid' FROM agents,jsonb_array_elements(resources->'processes') p WHERE id='aliyun-local' AND p->>'name'='" + process_name + "'"
            pid = int(wait_value(lambda: sql(process_sql), timeout=50))
            if KIND.startswith("JAVA"):
                # 看到启动器 PID 不代表源码编译已经完成，也不能证明实际业务方法正在运行。
                wait_value(lambda: command(["docker", "logs", "--tail", "8", target]),
                           lambda value: "DROP_JVM_FIXTURE_READY" in value, timeout=90)
            else:
                wait_value(lambda: (control / (str(pid) + ".sock")).is_socket(), timeout=20)
            actual_uid = command(["docker", "exec", target, "id", "-u"])
            if actual_uid != "10001": raise RuntimeError("Business target fixture did not use real UID10001")
        else:
            pid = int(wait_value(lambda: sql("SELECT resources->'processes'->0->>'pid' FROM agents WHERE id='aliyun-local' AND resources->'processes'->0->>'pid' IS NOT NULL")))
        capability_transitions = []
        if HELPER:
            initial = sql("SELECT capabilities ? 'PERF_CPU' FROM agents WHERE id='aliyun-local'")
            if initial != "f": raise RuntimeError("Delayed helper fixture falsely advertised PERF before helper startup")
            capability_transitions.append("helper_absent_perf_unavailable")
            helper = prefix + "-helper"
            containers.append(helper)
            command(["docker", "run", "-d", "--init", "--name", helper, "--label", "io.drop.purpose=isolated-helper-upload-recovery", "--label", "io.drop.owner=" + owner,
                     "--pid", "container:" + agent, "--network", "container:" + agent, "--read-only", "--user", "0:10001",
                     "--cap-drop", "ALL", "--cap-add", "PERFMON", "--cap-add", "SYS_PTRACE", "--cap-add", "DAC_READ_SEARCH",
                     "--security-opt", "no-new-privileges:true", "--security-opt", f"seccomp={SECCOMP}/agent-perfmon.json",
                     "--memory", "512m", "--memory-swap", "512m", "--cpus", ".5", "--pids-limit", "96",
                     "--device-read-bps", "/dev/nvme0n1:8mb", "--device-write-bps", "/dev/nvme0n1:4mb",
                     "--tmpfs", "/tmp:rw,size=64m,mode=1777", "--entrypoint", "/usr/local/bin/drop_collector_helper", *perf_options,
                     "-e", "COLLECTOR_HELPER_SOCKET=/run/drop-helper/control.sock", "-e", "COLLECTOR_HELPER_REQUIRE_BOUNDED_FS=true",
                     "-e", "COLLECTOR_HELPER_WORK_DIR=/var/lib/drop-helper", "-e", "COLLECTOR_HELPER_INCOMING_DIR=/run/drop-helper-incoming",
                     "-e", "ALLOW_HOST_PROCESSES=false", "-e", "ALLOWED_TARGET_UIDS=10001",
                     "-v", f"{helper_cache}:/var/lib/drop-helper", "-v", f"{helper_socket}:/run/drop-helper",
                     "-v", f"{helper_incoming}:/run/drop-helper-incoming", "-v", f"{ROOT}/build/drop_collector_helper:/usr/local/bin/drop_collector_helper:ro",
                     "-v", f"{ROOT}/contracts/task-kinds.json:/etc/drop/task-kinds.json:ro", TOOLS])
            wait_value(lambda: sql("SELECT 1 FROM agents WHERE id='aliyun-local' AND capabilities ? 'PERF_CPU' AND capability_details->'PERF_CPU'->>'isolation'='dedicated_root_helper'"), timeout=40)
            capability_transitions.append("helper_started_perf_registered")
        sql("INSERT INTO users(id,username,display_name,role) VALUES('recovery-fixture','recovery-fixture','Isolated recovery fixture','viewer')")
        parameters_object = {"pid":pid, "scope":"process", "duration_seconds":2 if COOPERATIVE else 1, "frequency_hz":99 if HELPER or COOPERATIVE else 10}
        if KIND == "JAVA_CPU": parameters_object["options"] = {"event":JAVA_EVENT}
        if KIND == "JAVA_HEAP": parameters_object["options"] = {"live":True}
        parameters = json.dumps(parameters_object)
        task = int(sql(f"INSERT INTO tasks(name,kind,target_agent_id,creator_id,request_id,params,collection_status,collection_deadline) VALUES('Isolated recovery fixture','{KIND}','aliyun-local','recovery-fixture','recovery-fixture','{parameters}'::jsonb,'QUEUED',now()+interval '120 seconds') RETURNING id"))
        grants = json.dumps([
            {"name":"runner.log","kind":"LOG","object_key":f"raw/tasks/{task}/attempts/1/runner.log","put_url":"http://uploads:9000/log","max_file_bytes":16384,"format":"log_jsonl","content_type":"text/plain"},
            {"name":"collection-manifest.json","kind":"MANIFEST","object_key":f"manifest/tasks/{task}/attempts/1/collection-manifest.json","put_url":"http://uploads:9000/manifest","max_file_bytes":65536,"format":"collection_manifest_v1","content_type":"application/json"},
        ])
        attempt = int(sql(f"INSERT INTO task_attempts(task_id,attempt,agent_id,status,raw_object_key,raw_upload_url,upload_targets) VALUES({task},1,'aliyun-local','QUEUED','fixture/recovery/profile.folded','http://uploads:9000/raw','{grants}'::jsonb) RETURNING id"))
        private_attempt = journal / f"attempt-{attempt}"
        def captured_raw():
            if not COOPERATIVE: return private_attempt / RAW_NAME
            marker_file = private_attempt / "cooperative-output.json"
            if not marker_file.is_file(): return private_attempt / "not-ready"
            saved = json.loads(marker_file.read_text())
            token = saved.get("token", "")
            if len(token) != 64 or any(character not in "0123456789abcdef" for character in token):
                raise RuntimeError("Unsafe cooperative fixture marker")
            return helper_incoming / ("agent-aliyun-local-" + token) / RAW_NAME
        raw_file = captured_raw()
        before_restart = None
        before_manifest = None
        if mode in ("restart", "manifest_restart"):
            stage = "manifest_attempts" if mode == "manifest_restart" else "raw_attempts"
            wait_value(lambda: sink(), lambda stats: stats[stage] >= 1)
            wait_value(lambda: captured_raw().is_file())
            raw_file = captured_raw()
            before_restart = hashlib.sha256(raw_file.read_bytes()).hexdigest()
            manifest_file = private_attempt / "collection-manifest.json"
            if mode == "manifest_restart":
                before_manifest = hashlib.sha256(manifest_file.read_bytes()).hexdigest()
            # 先确认首个事件已进入 SQL，再终止进程内传输缓冲；
            # 重启后的事件数量才能检测是否发生重复采样。
            wait_value(lambda: sql(f"SELECT count(*) FROM agent_events WHERE type='RUNNER_ACCEPTED' AND (payload->>'attempt_id')::bigint={attempt}"), lambda count: count == "1", 20)
            command(["docker", "stop", "--time", "25", agent])
            if not raw_file.is_file() or hashlib.sha256(raw_file.read_bytes()).hexdigest() != before_restart:
                raise RuntimeError("Shutdown discarded or changed captured RAW")
            if before_manifest and hashlib.sha256(manifest_file.read_bytes()).hexdigest() != before_manifest:
                raise RuntimeError("Shutdown changed immutable collection manifest")
            sink("/allow", "POST")
            if COOPERATIVE:
                # 采集已经完成，只停止本次拥有的玩具进程；避免恢复路径意外重新采样仍存活的业务 PID。
                command(["docker", "stop", "--time", "10", target])
            command(["docker", "start", agent])
            if HELPER:
                command(["docker", "restart", helper])
        result = json.loads(wait_value(lambda: sql(f"SELECT json_build_object('status',collection_status,'error_code',error_code) FROM tasks WHERE id={task} AND collection_status IN ('COLLECTED','FAILED')")))
        expected = "FAILED" if mode == "permanent" else "COLLECTED"
        if result["status"] != expected:
            raise RuntimeError("Unexpected recovery terminal outcome: " + str(result))
        if COOPERATIVE and expected == "COLLECTED":
            details = json.loads(sql("SELECT capability_details->'" + KIND + "' FROM agents WHERE id='aliyun-local'"))
            if details.get("execution_model") != "same_uid_agent_cooperation" or details.get("required_target_uid") != 10001 or details.get("bounded_receive_filesystem") is not True:
                raise RuntimeError("Cooperative capability did not publish the actual capless UID/quota execution boundary")
        wait_value(lambda: sql(f"SELECT count(*) FROM artifacts WHERE task_id={task} AND kind='LOG'"), lambda count: count == "1")
        stats = sink()
        if mode == "mixed" and stats["raw_attempts"] != 3:
            raise RuntimeError("503/429 retry classification did not execute exactly three PUTs")
        if mode == "permanent" and stats["raw_attempts"] != 1:
            raise RuntimeError("Permanent 403 retried its RAW upload")
        if len(set(stats["raw_sha256s"])) != 1:
            raise RuntimeError("Retry/restart sampled new RAW instead of reusing captured bytes")
        if mode == "late_log" and (stats["raw_attempts"] != 1 or stats["log_attempts"] != 4):
            raise RuntimeError("Late LOG recovery reuploaded RAW or did not recover LOG")
        if mode == "manifest_retry" and (stats["raw_attempts"] != 1 or stats["manifest_attempts"] != 2):
            raise RuntimeError("Manifest retry unexpectedly repeated RAW PUT")
        if mode == "manifest_restart" and stats["raw_attempts"] != 1:
            raise RuntimeError("Manifest restart repeated a completed RAW PUT")
        if expected == "COLLECTED" and (stats["manifest_attempts"] < 1 or len(set(stats["manifest_sha256s"])) != 1):
            raise RuntimeError("Manifest retry mutated persisted manifest bytes")
        rows = json.loads(sql(f"SELECT json_agg(json_build_object('kind',kind,'size',size,'sha256',sha256,'name',name,'format',format,'object_key',object_key,'metadata',metadata)) FROM artifacts WHERE task_id={task}"))
        for artifact in rows:
            file = directory / artifact["name"]
            captured = subprocess.run(["docker", "exec", uploads, "cat", "/tmp/" + artifact["name"]], capture_output=True, check=True, timeout=45)
            file.write_bytes(captured.stdout)
            content = file.read_bytes()
            if len(content) != artifact["size"] or hashlib.sha256(content).hexdigest() != artifact["sha256"]:
                raise RuntimeError("Private PUT bytes differ from committed artifact manifest")
            if artifact["kind"] == "LOG":
                diagnostic = json.loads(content)
                if artifact["format"] != "log_jsonl" or len(content) > 16384 or diagnostic["redaction"].startswith("fixed fields") is False:
                    raise RuntimeError("Unsafe public diagnostic content")
                if any(secret in content for secret in [b"http://", b"https://", b"isolated-fixture", b"drop_workload;"]):
                    raise RuntimeError("Public diagnostic leaked transport/profile content")
            if artifact["kind"] == "RAW":
                receipt = artifact["metadata"]["put_receipt"]
                if receipt["etag"] != stats["etags"][artifact["name"]] or receipt["completed_unix_ms"] <= 0:
                    raise RuntimeError("RAW PUT ETag receipt was lost or treated as SHA")
                if HELPER:
                    isolation = artifact["metadata"].get("collection_isolation", {})
                    if isolation.get("agent_uid") != 10001 or isolation.get("helper_uid") != 0 or isolation.get("bounded_filesystem") is not True or artifact["format"] != "perf_script":
                        raise RuntimeError("Actual helper RAW lost its distinct UIDs, hard quota or real perf format")
                if COOPERATIVE:
                    isolation = artifact["metadata"].get("collection_isolation", {})
                    if isolation.get("execution_model") != "same_uid_agent_cooperation" or isolation.get("bounded_filesystem") is not True:
                        raise RuntimeError("Cooperative RAW metadata lost its actual isolation boundary")
                    if KIND == "JAVA_CPU" and JAVA_EVENT == "cpu" and artifact["metadata"].get("sampling_engine") != "async_profiler_ctimer":
                        raise RuntimeError("Capless Java CPU collection falsely claimed perf sampling")
                    if KIND == "JAVA_HEAP" and not content.startswith(b"JAVA PROFILE 1.0"):
                        raise RuntimeError("Java heap fixture did not produce a real HPROF")
                    if KIND == "JAVA_CPU" and (b"DropJvmFixture" not in content or not any(line.rsplit(b" ", 1)[-1].isdigit() for line in content.splitlines())):
                        raise RuntimeError("Java fixture produced no actual business stack counts")
            if artifact["kind"] == "MANIFEST":
                manifest = json.loads(content)
                files = manifest["files"]
                raw = next(item for item in rows if item["kind"] == "RAW")
                if (artifact["object_key"] != f"manifest/tasks/{task}/attempts/1/collection-manifest.json"
                    or manifest["task_id"] != task or manifest["attempt_id"] != attempt
                    or manifest["task_kind"] != KIND or manifest["agent"]["id"] != "aliyun-local"
                    or not 0 < manifest["collection_started_unix_ms"] <= manifest["collection_finished_unix_ms"] <= manifest["data_upload_completed_unix_ms"]
                    or len(files) != 1 or files[0]["sha256"] != raw["sha256"] or files[0]["size"] != raw["size"]
                    or files[0]["etag"] != stats["etags"][RAW_NAME]
                    or any(secret in content for secret in (b"http://", b"https://", b"put_url", b"isolated-fixture", b"/usr/", b"/tmp/"))):
                    raise RuntimeError("Collection manifest failed provenance, receipt or redaction assertions")
                if before_manifest and artifact["sha256"] != before_manifest:
                    raise RuntimeError("Manifest restart regenerated different bytes")
        if mode == "permanent":
            raw_file = captured_raw()
            wait_value(lambda: (private_attempt / "terminal.json").is_file())
            if not raw_file.is_file():
                raise RuntimeError("Terminal upload failure discarded recoverable RAW")
            if result["error_code"] != "ARTIFACT_UPLOAD_FAILED":
                raise RuntimeError("Permanent upload failure lost its fixed error code")
        if mode in ("restart", "manifest_restart") and stats["raw_sha256s"][-1] != before_restart:
            raise RuntimeError("Restart upload did not preserve original RAW SHA")
        # 等待一个心跳，让 Agent 的有界事件缓冲写入 SQL。
        wait_value(lambda: sql(f"SELECT count(*) FROM agent_events WHERE type='RUNNER_ACCEPTED' AND (payload->>'attempt_id')::bigint={attempt}"), lambda count: count == "1", 20)
        if HELPER:
            command(["docker", "stop", "--time", "10", helper])
            wait_value(lambda: sql("SELECT 1 FROM agents WHERE id='aliyun-local' AND NOT (capabilities ? 'PERF_CPU') AND capability_details->'PERF_CPU'->>'available'='false'"), timeout=30)
            capability_transitions.append("helper_stopped_perf_deregistered")
            command(["docker", "start", helper])
            wait_value(lambda: sql("SELECT 1 FROM agents WHERE id='aliyun-local' AND capabilities ? 'PERF_CPU'"), timeout=40)
            capability_transitions.append("helper_restarted_perf_reregistered")
        report = {"binary_sha256":BINARY_HASHES,"capability_transitions":capability_transitions,"case":mode,"kind":KIND,"event":JAVA_EVENT if KIND == "JAVA_CPU" else None,"success":True,"terminal":result,"raw_attempts":stats["raw_attempts"],"log_attempts":stats["log_attempts"],"manifest_attempts":stats["manifest_attempts"],"one_captured_raw_sha256":stats["raw_sha256s"][0],"restart_sha256":before_restart,"restart_manifest_sha256":before_manifest,"artifacts":rows,"fixture_source":f"actual isolated {KIND} Agent/Server/PUT/PG execution; actual capless UID10001 cooperative target and bounded filesystem when cooperative; helper+Agent actual different UIDs when helper; no production mutation"}
        (directory / "evidence.json").write_text(json.dumps(report, indent=2) + "\n")
        # 保留失败 RAW 证据，但删除含敏感值的私有任务日志。
        if mode == "permanent":
            (directory / ("retained-" + RAW_NAME)).write_bytes(raw_file.read_bytes())
        return report
    except Exception as failure:
        # 服务日志使用生产脱敏序列化器；此处不采集 Docker inspect 的 Env/argv、
        # 任务日志、授权值或 profiler RAW。
        import re
        diagnostics = {"success":False, "kind":KIND, "case":mode, "failure":str(failure)[:1000], "services":{}}
        try:
            diagnostics["registered_perf"] = json.loads(sql("SELECT capability_details->'PERF_CPU' FROM agents WHERE id='aliyun-local'"))
        except (RuntimeError, ValueError, UnboundLocalError):
            pass
        for container in containers:
            if container.endswith(("-agent", "-server", "-helper", "-target")):
                logs = subprocess.run(["docker", "logs", "--tail", "20", container], capture_output=True, text=True, timeout=10)
                value = logs.stdout + logs.stderr
                value = re.sub(r"https?://[^\s\"']+", "[redacted-url]", value)
                value = value.replace("isolated-fixture", "[fixture-credential]")
                diagnostics["services"][container.rsplit("-", 1)[-1]] = value[-5000:]
        (directory / "failure.json").write_text(json.dumps(diagnostics, indent=2) + "\n")
        raise
    finally:
        for container in reversed(containers):
            ownership = command(["docker", "inspect", "--format", '{{index .Config.Labels "io.drop.owner"}}', container], False)
            if ownership == owner:
                command(["docker", "rm", "-f", container], False)
        network_owner = command(["docker", "network", "inspect", "--format", '{{index .Labels "io.drop.owner"}}', network], False)
        if network_owner == owner:
            command(["docker", "network", "rm", network], False)
        # 证据复制后只删除本次自建的精确日志；其中含夹具 URL，不属于发布制品。
        import shutil
        if journal.resolve().parent == directory.resolve():
            shutil.rmtree(journal)
        for path in helper_directories:
            if path.resolve().parent == SPOOL and path.name.startswith(prefix + "-"):
                shutil.rmtree(path)


results = []
for mode in MODES:
    result = run_case(mode)
    results.append(result)
    print(json.dumps({"case":result["case"],"success":True,"raw_attempts":result["raw_attempts"],"log_attempts":result["log_attempts"]}), flush=True)
(OUTPUT / "evidence.json").write_text(json.dumps(results, indent=2) + "\n")
