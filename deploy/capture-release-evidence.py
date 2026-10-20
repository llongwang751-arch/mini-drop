#!/usr/bin/env python3
"""按严格白名单读取现有发布状态，生成只读快照。

只运行 compose config --services、compose ps、容器 inspect 及可选的固定只读 PostgreSQL SELECT；
不启动、构建、停止或修改服务。Docker/psql 原始输出和异常正文即使在失败时也不输出。
stdout 可重定向为报告，本脚本不写应用文件。
"""

import argparse
import hashlib
import ipaddress
import json
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

MAX_OUTPUT_BYTES = 8 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 30
MAX_CONTAINERS = 128
MAX_SERVICES = 128
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
SERVICE = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")
CONTAINER_ID = re.compile(r"[a-f0-9]{64}\Z")
IMAGE_ID = re.compile(r"sha256:[a-f0-9]{64}\Z")
RELEASE_IMAGE = re.compile(
    r"drop-(?:core|agent|api|analyzer|web|ops|nginx):[0-9]{8}\.[0-9]+(?:-rc[0-9]+)?\Z"
)
PLATFORM_IMAGES = frozenset(
    {
        "postgres:17.11-alpine",
        "minio/minio:RELEASE.2025-07-23T15-54-02Z",
        "nginxinc/nginx-unprivileged:1.30.6-alpine",
        "ubuntu:24.04",
        "prom/prometheus:v3.15.0",
        "prom/alertmanager:v0.34.1",
        "grafana/grafana:13.2.4",
        "grafana/tempo:3.1.0",
        "otel/opentelemetry-collector:0.162.0",
    }
)
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z\Z")
USER = re.compile(
    r"(?:[0-9]{1,10}|[a-z_][a-z0-9_-]{0,31})(?::(?:[0-9]{1,10}|[a-z_][a-z0-9_-]{0,31}))?\Z"
)
CAPABILITIES = frozenset(
    [
        "ALL",
        "AUDIT_CONTROL",
        "AUDIT_READ",
        "AUDIT_WRITE",
        "BLOCK_SUSPEND",
        "BPF",
        "CHECKPOINT_RESTORE",
        "CHOWN",
        "DAC_OVERRIDE",
        "DAC_READ_SEARCH",
        "FOWNER",
        "FSETID",
        "IPC_LOCK",
        "IPC_OWNER",
        "KILL",
        "LEASE",
        "LINUX_IMMUTABLE",
        "MAC_ADMIN",
        "MAC_OVERRIDE",
        "MKNOD",
        "NET_ADMIN",
        "NET_BIND_SERVICE",
        "NET_BROADCAST",
        "NET_RAW",
        "PERFMON",
        "SETFCAP",
        "SETGID",
        "SETPCAP",
        "SETUID",
        "SYS_ADMIN",
        "SYS_BOOT",
        "SYS_CHROOT",
        "SYS_MODULE",
        "SYS_NICE",
        "SYS_PACCT",
        "SYS_PTRACE",
        "SYS_RAWIO",
        "SYS_RESOURCE",
        "SYS_TIME",
        "SYS_TTY_CONFIG",
        "SYSLOG",
        "WAKE_ALARM",
    ]
)
TABLES = frozenset(
    [
        "schema_migrations",
        "users",
        "sessions",
        "agents",
        "tasks",
        "task_attempts",
        "task_events",
        "artifacts",
        "analysis_jobs",
        "suggestions",
        "outbox",
        "schedules",
        "schedule_records",
        "audit_logs",
        "agent_samples",
        "groups",
        "group_members",
        "group_agents",
        "task_shares",
        "agent_events",
        "composite_runs",
        "composite_nodes",
        "diagnostic_rules",
        "analysis_job_runs",
        "suggestion_feedback",
        "ai_requests",
        "oidc_states",
        "oidc_identities",
        "rate_limits",
        "storage_maintenance_state",
    ]
)
POSTGRES_COMMAND = 'export PGPASSWORD="$(cat /run/secrets/database_password)"; exec psql -X --no-password --quiet --tuples-only --no-align -v ON_ERROR_STOP=1 -U drop -d drop --file=-'


class CaptureError(Exception):
    """只携带固定公开错误码，不带底层工具的敏感诊断正文。"""


def require(condition, code="INVALID_RUNTIME_METADATA"):
    if not condition:
        raise CaptureError(code)


def identifier(value, pattern=IDENTIFIER):
    require(isinstance(value, str) and pattern.fullmatch(value) is not None)
    return value


def integer(value, minimum=0, maximum=2**63 - 1):
    require(type(value) is int and minimum <= value <= maximum)
    return value


def boolean(value):
    require(type(value) is bool)
    return value


def validate_public_url(value):
    try:
        require(isinstance(value, str) and 1 <= len(value) <= 512)
        require(value.isascii() and not any(c.isspace() or ord(c) < 32 for c in value))
        parsed = urlsplit(value)
        require(parsed.scheme in {"http", "https"} and bool(parsed.hostname))
        require(parsed.username is None and parsed.password is None)
        require(parsed.path in {"", "/"} and not parsed.query and not parsed.fragment)
        require("?" not in value and "#" not in value and "\\" not in value)
        host = parsed.hostname
        try:
            ipaddress.ip_address(host)
        except ValueError:
            require(
                len(host) <= 253
                and all(
                    re.fullmatch(
                        r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label
                    )
                    for label in host.split(".")
                )
            )
        if parsed.port is not None:
            require(1 <= parsed.port <= 65535)
        return parsed.scheme + "://" + parsed.netloc.lower()
    except (ValueError, TypeError, CaptureError):
        raise CaptureError("INVALID_PUBLIC_URL") from None


def validate_deploy_dir(value):
    try:
        path = Path(value)
        require(path.is_absolute(), "INVALID_DEPLOY_DIRECTORY")
        require(not any(ord(c) < 32 for c in str(path)), "INVALID_DEPLOY_DIRECTORY")
        resolved = path.resolve(strict=True)
        require(
            resolved.is_dir() and resolved != Path(resolved.anchor),
            "INVALID_DEPLOY_DIRECTORY",
        )
        require((resolved / "compose.yml").is_file(), "MISSING_COMPOSE_FILE")
        return resolved
    except (OSError, ValueError, TypeError):
        raise CaptureError("INVALID_DEPLOY_DIRECTORY") from None


def run_read_only(argv, cwd, stdin=None):
    """分别限制子进程输出流的内存占用，并隐藏所有原始诊断内容。"""
    require(
        isinstance(argv, list) and all(isinstance(v, str) for v in argv),
        "INVALID_COMMAND",
    )
    if stdin is not None:
        require(isinstance(stdin, str) and len(stdin.encode()) <= 65536, "INPUT_LIMIT")
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except (OSError, ValueError):
        raise CaptureError("READ_ONLY_COMMAND_UNAVAILABLE") from None
    output = [bytearray(), bytearray()]
    exceeded = threading.Event()
    reader_failed = threading.Event()

    def kill_child():
        try:
            process.kill()
        except OSError:
            # 进程退出与输出超限检测可能竞态；读取线程也不能泄露原始诊断文本。
            pass

    def drain(stream, index):
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                if len(output[index]) + len(chunk) > MAX_OUTPUT_BYTES:
                    exceeded.set()
                    kill_child()
                    return
                output[index].extend(chunk)
        except OSError:
            reader_failed.set()
            kill_child()
        finally:
            stream.close()

    readers = [
        threading.Thread(target=drain, args=(stream, index), daemon=True)
        for index, stream in enumerate((process.stdout, process.stderr))
    ]
    for reader in readers:
        reader.start()
    try:
        if stdin is not None:
            try:
                process.stdin.write(stdin.encode("utf-8"))
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        process.wait(timeout=COMMAND_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        kill_child()
        process.wait(timeout=5)
        raise CaptureError("READ_ONLY_COMMAND_TIMEOUT") from None
    finally:
        for reader in readers:
            reader.join(timeout=5)
    require(not exceeded.is_set(), "OUTPUT_LIMIT")
    require(not reader_failed.is_set(), "OUTPUT_READ_FAILED")
    require(not any(reader.is_alive() for reader in readers), "OUTPUT_DRAIN_TIMEOUT")
    require(process.returncode == 0, "READ_ONLY_COMMAND_FAILED")
    try:
        return output[0].decode("utf-8")
    except UnicodeDecodeError:
        raise CaptureError("INVALID_TOOL_ENCODING") from None


def decode_json(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError, RecursionError):
        raise CaptureError("INVALID_TOOL_JSON") from None


def parse_compose_ps(text, services):
    if not text.strip():
        return []
    if text.lstrip().startswith("["):
        records = decode_json(text)
    else:
        records = [decode_json(line) for line in text.splitlines() if line.strip()]
    require(
        isinstance(records, list) and len(records) <= MAX_CONTAINERS, "CONTAINER_LIMIT"
    )
    result = []
    for record in records:
        require(isinstance(record, dict))
        container = identifier(record.get("ID"), CONTAINER_ID)
        service = identifier(record.get("Service"), SERVICE)
        require(service in services, "UNEXPECTED_COMPOSE_SERVICE")
        result.append((container, service))
    require(len({value[0] for value in result}) == len(result), "DUPLICATE_CONTAINER")
    return result


def capabilities(values):
    require(values is None or isinstance(values, list))
    values = values or []
    require(len(values) <= len(CAPABILITIES))
    for value in values:
        require(isinstance(value, str) and value.removeprefix("CAP_") in CAPABILITIES)
    return sorted({value.removeprefix("CAP_") for value in values})


def security_summary(options):
    require(options is None or isinstance(options, list))
    options = options or []
    require(len(options) <= 16)
    result = {"no_new_privileges": False, "seccomp": {"mode": "runtime_default"}}
    for option in options:
        require(isinstance(option, str) and len(option) <= 262144)
        if option in {"no-new-privileges", "no-new-privileges:true"}:
            result["no_new_privileges"] = True
        elif option.startswith(("seccomp=", "seccomp:")):
            value = option[8:]
            result["seccomp"] = (
                {"mode": "unconfined"}
                if value == "unconfined"
                else {
                    "mode": "custom",
                    "option_sha256": hashlib.sha256(value.encode()).hexdigest(),
                }
            )
        # AppArmor/SELinux 选项值可能包含节点路径或标签；
        # 只记录是否存在，不复制其原文。
        elif option.startswith(("apparmor=", "apparmor:")):
            result["apparmor_configured"] = True
        elif option.startswith(("label=", "label:")):
            result["selinux_configured"] = True
        else:
            raise CaptureError("UNRECOGNIZED_SECURITY_OPTION")
    return result


def network_summary(host, network, project):
    mode = host.get("NetworkMode", "default")
    require(isinstance(mode, str))
    if mode.startswith("container:"):
        identifier(mode.removeprefix("container:"), CONTAINER_ID)
        mode = "container"
    elif mode not in {"default", "bridge", "host", "none"}:
        require(
            mode in {project + "_" + name for name in ("control", "storage", "default")}
        )
        mode = "project_network"
    networks = network.get("Networks") or {}
    require(isinstance(networks, dict) and len(networks) <= 8)
    allowed = {project + "_" + name for name in ("control", "storage", "default")}
    require(
        all(name in allowed or name in {"bridge", "host", "none"} for name in networks)
    )
    return {"mode": mode, "names": sorted(networks)}


def sanitize_container(container, project, service):
    """从 Docker inspect 只提取审查允许的固定身份与权限信息，不保留 Env、argv 或原始路径。"""
    require(isinstance(container, dict))
    config, state, host = (
        container.get(name) for name in ("Config", "State", "HostConfig")
    )
    require(all(isinstance(value, dict) for value in (config, state, host)))
    labels = config.get("Labels")
    require(
        isinstance(labels, dict)
        and labels.get("com.docker.compose.project") == project
        and labels.get("com.docker.compose.service") == service,
        "CONTAINER_SCOPE_MISMATCH",
    )
    reference = config.get("Image")
    require(
        isinstance(reference, str)
        and (reference in PLATFORM_IMAGES or RELEASE_IMAGE.fullmatch(reference))
    )
    user = config.get("User") or "0"
    identifier(user, USER)
    status = state.get("Status")
    require(
        status
        in {"created", "running", "paused", "restarting", "removing", "exited", "dead"}
    )
    health = state.get("Health", {}).get("Status", "not_configured")
    require(health in {"starting", "healthy", "unhealthy", "not_configured"})
    started = identifier(state.get("StartedAt"), TIMESTAMP)
    try:
        datetime.fromisoformat(started.replace("Z", "+00:00"))
    except ValueError:
        raise CaptureError("INVALID_RUNTIME_METADATA") from None
    pid = host.get("PidMode", "")
    if pid == "":
        pid = "private"
    elif (
        isinstance(pid, str)
        and pid.startswith("container:")
        and CONTAINER_ID.fullmatch(pid.removeprefix("container:"))
    ):
        pid = "container"
    else:
        require(pid == "host")
    mounts = container.get("Mounts", [])
    require(isinstance(mounts, list) and len(mounts) <= 128)
    volumes = []
    for mount in mounts:
        require(isinstance(mount, dict))
        if mount.get("Type") == "volume":
            volumes.append(
                {
                    "name": identifier(mount.get("Name")),
                    "read_only": not boolean(mount.get("RW")),
                }
            )
    network = container.get("NetworkSettings", {})
    require(isinstance(network, dict))
    bindings = network.get("Ports") or {}
    require(isinstance(bindings, dict) and len(bindings) <= 128)
    ports = []
    for port, values in bindings.items():
        match = re.fullmatch(r"([0-9]{1,5})/(tcp|udp|sctp)", port)
        require(match is not None)
        internal = integer(int(match[1]), 1, 65535)
        require(values is None or isinstance(values, list))
        for binding in values or []:
            require(isinstance(binding, dict))
            ip = binding.get("HostIp")
            try:
                require(isinstance(ip, str))
                ip = str(ipaddress.ip_address(ip))
            except ValueError:
                raise CaptureError("INVALID_RUNTIME_METADATA") from None
            published = binding.get("HostPort")
            require(
                isinstance(published, str)
                and re.fullmatch(r"[0-9]{1,5}", published) is not None
            )
            ports.append(
                {
                    "container_port": internal,
                    "protocol": match[2],
                    "host_ip": ip,
                    "host_port": integer(int(published), 1, 65535),
                }
            )
    return {
        "service": service,
        "container_id": identifier(container.get("Id"), CONTAINER_ID),
        "image_id": identifier(container.get("Image"), IMAGE_ID),
        "image_reference": reference,
        "state": status,
        "health": health,
        "restart_count": integer(container.get("RestartCount", 0)),
        "oom_killed": boolean(state.get("OOMKilled", False)),
        "started_at": started,
        "configured_user": user,
        "cap_add": capabilities(host.get("CapAdd")),
        "cap_drop": capabilities(host.get("CapDrop")),
        "read_only_rootfs": boolean(host.get("ReadonlyRootfs")),
        "privileged": boolean(host.get("Privileged")),
        "pid_mode": pid,
        "security": security_summary(host.get("SecurityOpt")),
        "network": network_summary(host, network, project),
        "limits": {
            "memory_bytes": integer(host.get("Memory", 0)),
            "memory_swap_bytes": integer(host.get("MemorySwap", 0), -1),
            "cpu_nano": integer(host.get("NanoCpus", 0)),
            "cpu_quota": integer(host.get("CpuQuota", 0), -1),
            "cpu_period": integer(host.get("CpuPeriod", 0)),
            "pids": integer(
                host.get("PidsLimit") if host.get("PidsLimit") is not None else 0, -1
            ),
        },
        "named_volumes": sorted(volumes, key=lambda value: value["name"]),
        "published_ports": sorted(
            ports,
            key=lambda value: (
                value["container_port"],
                value["protocol"],
                value["host_ip"],
                value["host_port"],
            ),
        ),
    }


def read_only_sql(query):
    """为内部生成的查询拼接只读事务、语句与锁等待超时，返回 SQL 文本而不执行。

    查询由下方固定生成器构造；此函数本身不审查任意 SQL，也不接收用户查询。
    """
    return (
        "BEGIN TRANSACTION READ ONLY;\nSET LOCAL statement_timeout='5s';\nSET LOCAL lock_timeout='1s';\n"
        + query
        + ";\nCOMMIT;\n"
    )


def database_inventory_query():
    allowlist = ",".join("'" + name + "'" for name in sorted(TABLES))
    return read_only_sql(
        "SELECT json_build_object('schema_version',(SELECT COALESCE(max(version),0) FROM public.schema_migrations),'tables',(SELECT COALESCE(json_agg(tablename ORDER BY tablename),'[]'::json) FROM pg_tables WHERE schemaname='public' AND tablename IN ("
        + allowlist
        + ")))"
    )


def counts_query(tables):
    require(
        isinstance(tables, list)
        and 1 <= len(tables) <= len(TABLES)
        and all(isinstance(table, str) and table in TABLES for table in tables),
        "INVALID_DATABASE_TABLES",
    )
    require(len(set(tables)) == len(tables), "INVALID_DATABASE_TABLES")
    counts = ",".join(
        "'" + name + "',(SELECT count(*) FROM public." + name + ")"
        for name in sorted(tables)
    )
    return read_only_sql(
        "SELECT json_build_object('schema_version',(SELECT COALESCE(max(version),0) FROM public.schema_migrations),'table_counts',json_build_object("
        + counts
        + "))"
    )


def sanitize_database(value, tables):
    require(isinstance(value, dict), "INVALID_DATABASE_RESULT")
    version = integer(value.get("schema_version"), 0, 1000000)
    counts = value.get("table_counts")
    require(
        isinstance(counts, dict)
        and set(counts) == set(tables)
        and set(tables) <= TABLES,
        "INVALID_DATABASE_RESULT",
    )
    return {
        "schema_version": version,
        "table_counts": {table: integer(counts[table]) for table in sorted(tables)},
    }


def capture(
    deploy_dir, project, release, public_url, include_database=False, runner=None
):
    """读取一次已存在发布的白名单状态，生成可审计快照；没有构建、重启或更新服务的操作。"""
    runner = runner or run_read_only
    directory = validate_deploy_dir(deploy_dir)
    identifier(project, SERVICE)
    identifier(release)
    public_url = validate_public_url(public_url)
    compose = ["docker", "compose", "--project-name", project]
    services = runner(compose + ["config", "--services"], directory).splitlines()
    require(
        1 <= len(services) <= MAX_SERVICES and len(set(services)) == len(services),
        "INVALID_COMPOSE_SERVICES",
    )
    for service in services:
        identifier(service, SERVICE)
    entries = parse_compose_ps(
        runner(
            compose
            + ["ps", "--all", "--no-trunc", "--orphans=false", "--format", "json"],
            directory,
        ),
        set(services),
    )
    containers = []
    for container_id, service in entries:
        inspected = decode_json(
            runner(["docker", "inspect", "--type=container", container_id], directory)
        )
        require(
            isinstance(inspected, list) and len(inspected) == 1,
            "INVALID_INSPECT_RESULT",
        )
        container = sanitize_container(inspected[0], project, service)
        require(container["container_id"] == container_id, "CONTAINER_SCOPE_MISMATCH")
        containers.append(container)
    database = {"captured": False}
    if include_database:
        require(
            any(
                container["service"] == "postgres" and container["state"] == "running"
                for container in containers
            ),
            "POSTGRES_NOT_RUNNING",
        )
        command = compose + ["exec", "-T", "postgres", "sh", "-ec", POSTGRES_COMMAND]
        inventory = decode_json(runner(command, directory, database_inventory_query()))
        require(isinstance(inventory, dict), "INVALID_DATABASE_RESULT")
        tables = inventory.get("tables")
        query = counts_query(tables)
        database = sanitize_database(
            decode_json(runner(command, directory, query)), tables
        )
        require(
            database["schema_version"] == inventory.get("schema_version"),
            "SCHEMA_CHANGED_DURING_CAPTURE",
        )
        database["captured"] = True
        database["consistency"] = (
            "Exact table counts share a read-only statement snapshot; runtime inspect is a sequential observation."
        )
    return {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "release": release,
        "compose_project": project,
        "public_url": public_url,
        "read_only_capture": True,
        "configured_user_is_runtime_identity_proof": False,
        "services": sorted(
            containers, key=lambda value: (value["service"], value["container_id"])
        ),
        "missing_services": sorted(
            set(services) - {container["service"] for container in containers}
        ),
        "database": database,
        "limitations": [
            "No public HTTP/TLS, artifact, backup, or functional verification is performed.",
            "Image IDs identify actual local images; release is a caller-provided label, not a verified signature.",
            "Configured user and container limits are Docker configuration, not measured process UID, CPU or RSS.",
        ],
    }


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise CaptureError("INVALID_ARGUMENTS")


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--deploy-dir", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--public-url", required=True)
    parser.add_argument(
        "--include-database",
        action="store_true",
        help="Optional bounded read-only exact table counts; no rows or credentials are emitted.",
    )
    try:
        args = parser.parse_args(argv)
        try:
            directory = validate_deploy_dir(args.deploy_dir)
            identifier(args.project, SERVICE)
            identifier(args.release)
            validate_public_url(args.public_url)
        except CaptureError:
            raise CaptureError("INVALID_ARGUMENTS") from None
    except CaptureError:
        print(json.dumps({"error": "INVALID_ARGUMENTS"}), file=sys.stderr)
        return 2
    try:
        result = capture(
            directory,
            args.project,
            args.release,
            args.public_url,
            args.include_database,
        )
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except CaptureError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - CLI 边界不得输出工具中的敏感值。
        print(json.dumps({"error": "CAPTURE_FAILED"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
