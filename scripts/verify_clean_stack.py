#!/usr/bin/env python3
"""Build and verify an isolated core stack on a fresh Linux CI runner.

Only this invocation's Compose containers/network are removed. Data volumes
remain for runner teardown. Private configuration and keys never enter evidence.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib import error, request

import yaml


ROOT = Path(__file__).resolve().parents[1]
CORE = {
    "postgres", "minio", "migrate", "control-plane", "diagnosis-worker",
    "analyzer", "apiserver", "native-agent", "web", "python-hotspot",
}
BUILD = ("migrate", "control-plane", "native-agent", "apiserver", "web", "python-hotspot")
PYTHON = {"migrate", "diagnosis-worker", "analyzer"}
MEMORY_MB = {
    "postgres": 384, "minio": 384, "migrate": 768, "control-plane": 256,
    "diagnosis-worker": 1024, "analyzer": 768, "apiserver": 192,
    "native-agent": 384, "web": 128, "python-hotspot": 128,
}
SECRET_NAMES = {
    "POSTGRES_PASSWORD", "DATABASE_URL", "MINIO_SECRET_KEY",
    "MINI_DROP_API_KEY", "MINI_DROP_GRPC_TOKEN",
}
PROJECT_RE = re.compile(r"mini-drop-clean-[a-f0-9]{16}\Z")


class AcceptanceError(RuntimeError):
    pass


def require(condition: object, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_key_stat(info: os.stat_result, expected_uid: int) -> None:
    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_uid == expected_uid and info.st_nlink == 1,
            "private key must be a nonlinked regular 0600 file owned by its runtime UID")


def redact(text: str, secret_values: list[str]) -> str:
    for value in sorted((v for v in secret_values if v), key=len, reverse=True):
        text = text.replace(value, "<REDACTED>")
    text = re.sub(r"(?i)(X-Amz-[A-Za-z-]+)=([^&\s\"']+)", r"\1=<REDACTED>", text)
    return re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
                  "<REDACTED PRIVATE KEY>", text, flags=re.S)


def public_config(value: object, private: Path) -> object:
    """Deterministic evidence view; the runnable config stays mode 0600."""
    if isinstance(value, dict):
        return {k: "<REDACTED>" if k in SECRET_NAMES or "API_KEY" in k or "TOKEN" in k
                else public_config(v, private) for k, v in value.items()}
    if isinstance(value, list):
        return [public_config(v, private) for v in value]
    if isinstance(value, str):
        return value.replace(str(private), "<PRIVATE_RUNTIME>").replace(str(ROOT), "<EXACT_GIT_CHECKOUT>")
    return value


def prepare_compose(template: dict, private: Path, project: str) -> dict:
    require(PROJECT_RE.fullmatch(project), "unsafe Compose project identity")
    require(CORE <= set(template.get("services", {})), "canonical core services missing")
    result = copy.deepcopy(template)
    result["name"] = project
    result["services"] = {key: result["services"][key] for key in sorted(CORE)}
    result["volumes"] = {key: {} for key in ("pgdata", "miniodata", "agentoutbox")}
    result.pop("networks", None)
    for name, spec in result["services"].items():
        spec.pop("profiles", None)
        spec.pop("container_name", None)
        spec.pop("ports", None)
        spec["restart"] = "no"
        spec["mem_limit"] = f"{MEMORY_MB[name]}m"
        spec["cpus"] = 1.0
        spec["pids_limit"] = 256
        spec["security_opt"] = ["no-new-privileges:true"]
        spec["labels"] = {"mini-drop.clean-stack.owner": project}
        if "env_file" in spec:
            require(spec["env_file"] == [".env"], f"unexpected env source: {name}")
            spec["env_file"] = [str(private / "runtime.env")]
        if "build" in spec:
            build = spec["build"]
            context = (ROOT / build["context"]).resolve()
            require(context.is_relative_to(ROOT), "build context escaped checkout")
            build["context"] = str(context)
            args = build.setdefault("args", {})
            args.update({"NATIVE_BUILD_JOBS": "1", "UBUNTU_MIRROR": "http://archive.ubuntu.com/ubuntu",
                         "ALPINE_MIRROR": "https://dl-cdn.alpinelinux.org/alpine",
                         "GOPROXY": "https://proxy.golang.org,direct"})
        if name not in {"postgres", "minio"}:
            image_name = "python-worker" if name in PYTHON else name
            spec["image"] = f"{project}/{image_name}:exact-git"
            spec["pull_policy"] = "never"
        volumes = []
        for mount in spec.get("volumes", []):
            if mount == "./deploy/certs:/certs:ro":
                volumes.append(f"{private / 'certs'}:/certs:ro")
            elif mount == "${MINI_DROP_SOURCE_PATH:-.}:/workspace-source:ro":
                volumes.append(f"{ROOT}:/workspace-source:ro")
            elif mount.startswith(("/sys/kernel/tracing:", "/sys/kernel/debug:")) and name == "native-agent":
                continue  # sys_metrics does not require BPF or perf mount access.
            else:
                require(mount.split(":", 1)[0] in result["volumes"], f"unexpected mount: {name}")
                volumes.append(mount)
        if volumes:
            spec["volumes"] = volumes
        if name == "native-agent":
            spec["cap_add"] = []
            spec["cap_drop"] = ["ALL"]
        if name == "python-hotspot":
            # Equal empty capability sets let /proc identity checks observe
            # this owned target without granting the Agent SYS_PTRACE.
            spec["cap_drop"] = ["ALL"]
        if name == "web":
            spec["ports"] = [{"target": 80, "published": "0", "host_ip": "127.0.0.1", "protocol": "tcp"}]
    return result


def validate_runtime_config(config: dict, project: str, private: Path) -> None:
    """Reject attachment to existing storage, hosts, images or external services."""
    require(PROJECT_RE.fullmatch(project), "unsafe project")
    require(config.get("name") == project, "project mismatch")
    require(set(config.get("services", {})) == CORE, "scope changed")
    for kind in ("volumes", "networks"):
        for spec in config.get(kind, {}).values():
            require(not spec.get("external"), f"external {kind} forbidden")
            require(spec.get("name", "").startswith(project + "_"), f"unowned {kind} forbidden")
            require(not spec.get("driver_opts"), f"host-backed {kind} forbidden")
    for name, spec in config["services"].items():
        require(not spec.get("privileged"), "privileged service forbidden")
        require(not spec.get("container_name"), "fixed container name forbidden")
        require(spec.get("network_mode") is None, "host/external network forbidden")
        require(set(spec.get("depends_on", {})) <= CORE, "external dependency")
        require(int(spec.get("mem_limit", 0)) == MEMORY_MB[name] * 1024 ** 2, "unbounded memory")
        require(float(spec.get("cpus", 0)) <= 1.0 and float(spec.get("cpus", 0)) > 0, "unbounded CPU")
        require(spec.get("pids_limit") == 256, "unbounded PID count")
        if name not in {"postgres", "minio"}:
            require(spec.get("image", "").startswith(project + "/"), "prebuilt/deployment image forbidden")
        else:
            require(spec.get("image") == {"postgres": "postgres:16", "minio": "quay.io/minio/minio:RELEASE.2025-04-08T15-41-24Z"}[name],
                    "noncanonical infrastructure image")
        if name == "native-agent":
            require(spec.get("pid") == "host", "Agent PID scope changed")
            require(not spec.get("cap_add") and spec.get("cap_drop") == ["ALL"], "unexpected collector privilege")
        else:
            require(spec.get("pid") == ("host" if name == "python-hotspot" else None)
                    and not spec.get("cap_add"), "unexpected privilege")
            if name == "python-hotspot":
                require(spec.get("cap_drop") == ["ALL"], "target capabilities must match Agent")
        for port in spec.get("ports", []):
            require(name == "web" and port.get("host_ip") == "127.0.0.1"
                    and str(port.get("published")) == "0" and port.get("target") == 80,
                    "nonisolated host publication")
        for mount in spec.get("volumes", []):
            if mount["type"] == "bind":
                source = Path(mount["source"]).resolve()
                require(source in {private / "certs", ROOT} and mount.get("read_only"), "unsafe bind mount")
            else:
                require(mount["type"] == "volume" and mount["source"] in config["volumes"], "unowned data mount")
        if "build" in spec:
            context = Path(spec["build"]["context"]).resolve()
            require(context.is_relative_to(ROOT), "escaped build context")
            dockerfile = (context / spec["build"].get("dockerfile", "Dockerfile")).resolve()
            require(dockerfile.is_relative_to(ROOT) and dockerfile.is_file(), "noncanonical Dockerfile")
    api = config["services"]["apiserver"]["environment"]
    require(api.get("MINI_DROP_API_AUTH_ENABLED") == "1" and api.get("MINI_DROP_CONTROL_GRPC_TLS") == "1", "API/TLS disabled")
    for name in ("control-plane", "diagnosis-worker"):
        env = config["services"][name]["environment"]
        require(all(env.get(key) == "1" for key in ("MINI_DROP_GRPC_SECURE", "MINI_DROP_GRPC_AUTH_ENABLED", "MINI_DROP_GRPC_REQUIRE_CLIENT_CERT")),
                "private hop mTLS/auth disabled")
    require(config["services"]["native-agent"]["environment"].get("AGENT_GRPC_SECURE") == "1", "Agent TLS disabled")
    env = config["services"]["diagnosis-worker"]["environment"]
    require(env.get("MINI_DROP_AI_ENABLED") == "none" and not env.get("MINI_DROP_AI_API_KEY"), "external model credentials forbidden")
    control = config["services"]["control-plane"]["environment"]
    require(control.get("MINI_DROP_GRPC_CLIENT_CERT_FILE") == "/certs/client.crt"
            and control.get("MINI_DROP_GRPC_CLIENT_KEY_FILE") == "/certs/client.key", "Control mTLS healthcheck client identity missing")


def validate_artifact(artifact: dict, raw: bytes, pid: int, start_ticks: int) -> dict:
    require(artifact.get("integrity_status") == "VERIFIED", "artifact integrity not verified")
    require(len(raw) == artifact.get("size_bytes") and sha(raw) == artifact.get("sha256"), "download SHA/size mismatch")
    document = json.loads(raw)
    require(document.get("schema_version") == "sys_metrics.v2" and document.get("pid") == pid, "raw artifact target/schema mismatch")
    samples = document.get("samples", [])
    require(len(samples) >= 5 and all(sample.get("process_start_ticks") == start_ticks for sample in samples), "raw samples missing or PID reused")
    times = [sample.get("captured_at_unix_ms") for sample in samples]
    require(all(isinstance(t, int) for t in times) and all(a < b for a, b in zip(times, times[1:]))
            and times[-1] - times[0] >= 4000, "raw measurement window invalid")
    for sample in samples:
        require(all(isinstance(sample.get(key), (int, float)) for key in ("process_cpu_ticks", "rss_kb", "threads", "fd_count")),
                "required OS measurements missing")
    metadata = artifact.get("metadata", {})
    identity = metadata.get("process_identity", {})
    require(metadata.get("schema_version") == "sys_metrics_analysis.v2"
            and metadata.get("analyzer_type") == "collector.sys_metrics"
            and metadata.get("sample_count") == len(samples), "Analyzer output missing/mismatched")
    require(identity.get("verified") is True and identity.get("pid") == pid
            and identity.get("start_ticks") == start_ticks, "Analyzer identity mismatch")
    require(isinstance(metadata.get("summary"), dict) and metadata["summary"], "Analyzer summary missing")
    return {"sha256": sha(raw), "size_bytes": len(raw), "sample_count": len(samples),
            "window_ms": times[-1] - times[0], "target_pid": pid, "process_start_ticks": start_ticks,
            "analyzer_type": metadata["analyzer_type"], "analysis_schema": metadata["schema_version"]}


def owned_container_ids(inspected: list[dict], project: str) -> list[str]:
    require(PROJECT_RE.fullmatch(project), "unsafe cleanup project")
    result = []
    for item in inspected:
        labels = item.get("Config", {}).get("Labels", {})
        require(labels.get("com.docker.compose.project") == project
                and labels.get("mini-drop.clean-stack.owner") == project
                and labels.get("com.docker.compose.service") in CORE, "cleanup refuses foreign container")
        require(re.fullmatch(r"[a-f0-9]{64}", item.get("Id", "")), "invalid container ID")
        result.append(item["Id"])
    return result


def source_migration_heads() -> list[str]:
    revisions, parents = set(), set()
    for path in (ROOT / "server/migrations/versions").glob("*.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                names = [target.id for target in node.targets if isinstance(target, ast.Name)] if isinstance(node, ast.Assign) else [getattr(node.target, "id", "")]
                if not ({"revision", "down_revision"} & set(names)):
                    continue
                value = ast.literal_eval(node.value)
                if "revision" in names:
                    revisions.add(value)
                elif value:
                    parents.update(value if isinstance(value, (tuple, list)) else [value])
    return sorted(revisions - parents)


class Runner:
    def __init__(self, output: Path, expected: str):
        require(not output.exists(), "output already exists; preserve previous attempt")
        output.mkdir(parents=True)
        self.output, self.expected = output, expected
        self.project = "mini-drop-clean-" + secrets.token_hex(8)
        self.private = Path(tempfile.mkdtemp(prefix=self.project + "-"))
        os.chmod(self.private, 0o700)
        self.values: list[str] = []
        self.command_count = 0
        self.deadline = time.monotonic() + 55 * 60
        self.in_cleanup = False
        self.cleanup_authorized = False
        self.report = {"schema_version": "mini-drop.clean-stack-acceptance.v1", "status": "RUNNING",
                       "source_head": expected, "project": self.project, "started_at": datetime.now(timezone.utc).isoformat(),
                       "scope": "FRESH_CHECKOUT_CORE_PLATFORM_REAL_SYS_METRICS", "stages": [],
                       "excluded": ["Office", "Go/Java/C++ workload deployment", "perf/BPF/other collectors", "external model quality", "OS installation", "airgap", "hour endurance"],
                       "security": {"private_grpc_mtls": True, "grpc_token_auth": True, "api_key_auth": True,
                                    "public_http": "loopback ephemeral port only", "model_mode": "none", "production_credentials": False},
                       "cleanup": {"status": "PENDING", "volumes_deleted": False, "global_prune": False}}
        self.save()

    def save(self) -> None:
        (self.output / "report.json").write_text(json.dumps(self.report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def evidence(self, name: str, payload: object) -> None:
        data = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        (self.output / name).write_text(redact(data, self.values), encoding="utf-8")

    def run(self, args: list[str], *, timeout: int = 120, record: bool = True) -> str:
        # No shell interpolation and no secrets in argv. Build/runtime output is sanitized.
        self.command_count += 1
        started = time.monotonic()
        if not self.in_cleanup:
            remaining = int(self.deadline - started)
            require(remaining > 0, "55-minute total execution budget exhausted")
            timeout = min(timeout, remaining)
        try:
            result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=timeout, env={**os.environ, "COMPOSE_PROJECT_NAME": self.project})
            if record:
                text = redact(result.stdout + "\n" + result.stderr, self.values)
                (self.output / f"command-{self.command_count:03}.log").write_text(text, encoding="utf-8")
            require(result.returncode == 0, f"command failed ({result.returncode}): {args[:3]}")
            return result.stdout
        except subprocess.TimeoutExpired as exc:
            if record:
                raw = (exc.stdout or b"") + (exc.stderr or b"")
                text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                (self.output / f"command-{self.command_count:03}.log").write_text(redact(text, self.values), encoding="utf-8")
            raise AcceptanceError(f"command timeout after {timeout}s: {args[:3]}") from exc
        finally:
            print(f"clean-stack command {self.command_count} completed in {time.monotonic()-started:.1f}s", flush=True)

    def compose(self, *args: str, timeout: int = 120, record: bool = True) -> str:
        return self.run(["docker", "compose", "--project-name", self.project, "--env-file", str(self.private / "runtime.env"),
                         "-f", str(self.private / "runtime.json"), *args], timeout=timeout, record=record)

    def stage(self, name: str, action) -> None:
        row = {"name": name, "status": "RUNNING"}
        self.report["stages"].append(row)
        self.save()
        print(f"clean-stack: {name}", flush=True)
        started = time.monotonic()
        try:
            action()
            row["status"] = "PASSED"
        except BaseException:
            row["status"] = "FAILED"
            raise
        finally:
            row["elapsed_seconds"] = round(time.monotonic() - started, 3)
            self.save()

    def preflight(self) -> None:
        require(sys.platform == "linux", "real acceptance requires Linux")
        require(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted", "only fresh GitHub-hosted runners authorized")
        require(re.fullmatch(r"[a-f0-9]{40}", self.expected), "expected exact Git SHA required")
        head = self.run(["git", "rev-parse", "HEAD"]).strip()
        require(head == self.expected, "checkout differs from expected Git")
        require(not self.run(["git", "status", "--porcelain"]).strip(), "dirty/untracked checkout")
        self.report["source_tree"] = self.run(["git", "rev-parse", "HEAD^{tree}"]).strip()
        docker = json.loads(self.run(["docker", "info", "--format", "{{json .}}"], record=False))
        require(docker.get("OSType") == "linux" and docker.get("NCPU", 0) >= 2, "Linux Docker with >=2 CPUs required")
        require(docker.get("MemTotal", 0) >= 6 * 1024 ** 3, "runner memory below 6GiB")
        free = shutil.disk_usage(ROOT).free
        require(free >= 16 * 1024 ** 3, "runner disk below 16GiB; no prune fallback")
        self.report["runner"] = {"docker_version": docker.get("ServerVersion"), "cpus": docker["NCPU"],
                                 "memory_bytes": docker["MemTotal"], "disk_free_bytes": free, "runtime_memory_ceiling_mb": sum(MEMORY_MB.values()),
                                 "native_build_jobs": 1, "build_mode": "sequential_no_cache_official_base_pull"}
        self.run(["docker", "compose", "version"])
        for kind in ("container", "network", "volume"):
            require(not self.run(["docker", kind, "ls", "-q", "--filter", f"label=com.docker.compose.project={self.project}"]).strip(), "project resource collision")
        self.cleanup_authorized = True
        self.report["canonical_sources"] = {str(p.relative_to(ROOT)).replace("\\", "/"): sha(p.read_bytes())
                                             for p in [ROOT / "docker-compose.yml", *sorted((ROOT / "deploy/dockerfiles").glob("*.Dockerfile")),
                                                       ROOT / "demo/python-hotspot/Dockerfile", ROOT / "deploy/scripts/generate-dev-certs.sh"]}

    def configuration(self) -> None:
        password, minio_secret, api_key, token = (secrets.token_hex(24) for _ in range(4))
        agent_id = "clean_agent_" + self.project[-16:]
        self.agent_id = agent_id
        env = {"POSTGRES_DB": "mini_drop", "POSTGRES_USER": "mini_drop", "POSTGRES_PASSWORD": password,
               "DATABASE_URL": f"postgresql+psycopg://mini_drop:{password}@postgres:5432/mini_drop",
               "MINIO_ENDPOINT": "minio:9000", "MINIO_AGENT_ENDPOINT": "minio:9000", "MINIO_ACCESS_KEY": "clean_stack",
               "MINIO_SECRET_KEY": minio_secret, "MINIO_BUCKET": "clean-stack", "MINIO_SECURE": "0", "MINIO_AUTO_CREATE_BUCKET": "1",
               "MINI_DROP_API_AUTH_ENABLED": "1", "MINI_DROP_API_KEY": api_key,
               "MINI_DROP_GRPC_SECURE": "1", "MINI_DROP_GRPC_AUTH_ENABLED": "1", "MINI_DROP_GRPC_TOKEN": token,
               "MINI_DROP_GRPC_REQUIRE_CLIENT_CERT": "1", "MINI_DROP_GRPC_CERT_FILE": "/certs/server.crt",
               "MINI_DROP_GRPC_KEY_FILE": "/certs/server.key", "MINI_DROP_GRPC_CA_FILE": "/certs/ca.crt",
               "MINI_DROP_GRPC_CLIENT_CERT_FILE": "/certs/client.crt", "MINI_DROP_GRPC_CLIENT_KEY_FILE": "/certs/client.key",
               "NATIVE_AGENT_ID": agent_id, "AGENT_HEARTBEAT_INTERVAL_SEC": "2", "MINI_DROP_AI_ENABLED": "none",
               "MINI_DROP_AI_API_KEY": "", "MINI_DROP_AGENT_FRAMEWORK": "langgraph", "MINI_DROP_AGENT_CHECKPOINT_BACKEND": "postgres"}
        self.values = [password, minio_secret, api_key, token, env["DATABASE_URL"]]
        self.api_key = api_key
        env_path = self.private / "runtime.env"
        env_path.write_text("".join(f"{key}={value}\n" for key, value in env.items()), encoding="utf-8")
        os.chmod(env_path, 0o600)
        self.run(["bash", "deploy/scripts/generate-dev-certs.sh", "control-plane", str(self.private / "certs"), agent_id])
        self.run(["sudo", "-n", "chown", "65532:65532", str(self.private / "certs/client.key")])
        self.run(["sudo", "-n", "chmod", "600", str(self.private / "certs/client.key")])
        self.run(["sudo", "-n", "chown", "0:0", str(self.private / "certs/agent.key")])
        self.run(["sudo", "-n", "chmod", "600", str(self.private / "certs/agent.key")])
        for filename, uid in (("client.key", 65532), ("agent.key", 0)):
            validate_key_stat((self.private / "certs" / filename).lstat(), uid)
        os.chmod(self.private / "certs", 0o755)
        template = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        config = prepare_compose(template, self.private, self.project)
        runtime = self.private / "runtime.json"
        runtime.write_text(json.dumps(config), encoding="utf-8")
        os.chmod(runtime, 0o600)
        config = json.loads(self.compose("config", "--format", "json", record=False))
        validate_runtime_config(config, self.project, self.private)
        runtime.write_text(json.dumps(config), encoding="utf-8")
        self.evidence("compose-sanitized.json", public_config(config, self.private))
        self.config = config
        self.report["configuration"] = {"sanitized_sha256": sha((self.output / "compose-sanitized.json").read_bytes()),
            "template": "docker-compose.yml", "services": sorted(CORE), "image_tags_unique": True,
            "private_key_permissions": {"client.key": {"uid": 65532, "mode": "0600"}, "agent.key": {"uid": 0, "mode": "0600"}},
            "permitted_overrides": ["unique project/images", "private PKI/env/source absolute paths", "official build mirrors and serial native build", "bounded runtime resources and no restart", "loopback ephemeral Web port", "single idle Python target", "sys_metrics-only Agent capabilities/mounts"]}

    def build(self) -> None:
        for name in BUILD:
            image = self.config["services"][name]["image"]
            require(not self.run(["docker", "image", "ls", "-q", image]).strip(), "image tag already exists")
            self.compose("build", "--pull", "--no-cache", name, timeout=1500)
        self.compose("pull", "postgres", "minio", timeout=300)
        images = {}
        for name, spec in self.config["services"].items():
            info = json.loads(self.run(["docker", "image", "inspect", spec["image"]], record=False))[0]
            images[name] = {"tag": spec["image"], "image_id": info["Id"], "repo_digests": info.get("RepoDigests", [])}
        self.evidence("built-images.json", images)
        self.built_images = images

    def container_state(self) -> list[dict]:
        ids = self.run(["docker", "container", "ls", "-aq", "--filter", f"label=com.docker.compose.project={self.project}"], record=False).split()
        return json.loads(self.run(["docker", "inspect", *ids], record=False)) if ids else []

    def start(self) -> None:
        self.started_at = time.time()
        self.compose("up", "-d", "--no-build", timeout=240)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            states = self.container_state()
            owned_container_ids(states, self.project)
            by_service = {item["Config"]["Labels"]["com.docker.compose.service"]: item for item in states}
            if set(by_service) == CORE:
                failed = [name for name, item in by_service.items() if item["State"]["Status"] == "exited" and (name != "migrate" or item["State"]["ExitCode"] != 0)]
                require(not failed, f"services exited: {failed}")
                ready = all(item["State"]["Status"] == "exited" and item["State"]["ExitCode"] == 0 if name == "migrate"
                            else item["State"]["Status"] == "running" and (name == "native-agent" or item["State"].get("Health", {}).get("Status") == "healthy")
                            for name, item in by_service.items())
                if ready:
                    require(all(item["Image"] == self.built_images[name]["image_id"] for name, item in by_service.items()),
                            "runtime containers do not use this run's built images")
                    self.target_pid = by_service["python-hotspot"]["State"]["Pid"]
                    self.target_container = by_service["python-hotspot"]["Id"]
                    self.evidence("service-readiness.json", {name: {"container_id": item["Id"], "state": item["State"]["Status"],
                        "exit_code": item["State"]["ExitCode"], "health": item["State"].get("Health", {}).get("Status")} for name, item in by_service.items()})
                    break
            time.sleep(3)
        else:
            raise AcceptanceError("bounded core service readiness timed out")
        port = self.compose("port", "web", "80").strip()
        require(re.fullmatch(r"127\.0\.0\.1:[0-9]+", port), "Web published beyond loopback")
        self.base_url = "http://" + port
        permissions = json.loads(self.compose("exec", "-T", "native-agent", "python3", "-c",
            "import json,os,pathlib; s=dict(x.split(':',1) for x in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in x); print(json.dumps({'uid':os.geteuid(),'agent_key_readable':os.access('/certs/agent.key',os.R_OK),'cap_effective':s['CapEff'].strip(),'cap_permitted':s['CapPrm'].strip()}))"))
        require(permissions["uid"] == 0 and permissions["agent_key_readable"]
                and int(permissions["cap_effective"], 16) == 0 and int(permissions["cap_permitted"], 16) == 0,
                "unprivileged Agent cannot read isolated key or has extra capabilities")
        self.evidence("agent-runtime-permissions.json", permissions)
        self.report["services_ready"] = {"healthy": 8, "agent_running_pending_heartbeat": 1, "migration_exit_zero": 1}

    def sql(self, query: str) -> str:
        return self.compose("exec", "-T", "postgres", "psql", "-U", "mini_drop", "-d", "mini_drop", "-At", "-c", query).strip()

    def migration(self) -> None:
        actual = sorted(self.sql("SELECT version_num FROM alembic_version;").splitlines())
        expected = source_migration_heads()
        require(actual == expected and expected, "migration did not reach exact source heads")
        self.report["migration"] = {"source_heads": expected, "database_heads": actual}

    def http(self, path: str, payload: dict | None = None, *, authenticated: bool = True) -> bytes:
        require(path.startswith("/") and not path.startswith("//"), "unsafe HTTP path")
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers["X-API-Key"] = self.api_key
        data = json.dumps(payload).encode() if payload is not None else None
        req = request.Request(self.base_url + path, data=data, headers=headers)
        # Ignore ambient HTTP_PROXY; this test contacts its owned loopback Web.
        with request.build_opener(request.ProxyHandler({})).open(req, timeout=20) as response:
            require(response.status == 200, "HTTP response not successful")
            raw = response.read(32 * 1024 ** 2 + 1)
            require(len(raw) <= 32 * 1024 ** 2, "HTTP evidence exceeds bound")
            return raw

    def api(self, path: str, payload: dict | None = None) -> object:
        response = json.loads(self.http(path, payload))
        require(response.get("code") == 0 and "data" in response, "API contract error")
        return response["data"]

    def heartbeat(self) -> None:
        deadline = time.monotonic() + 90
        previous = None
        while time.monotonic() < deadline:
            agents = self.api("/api/agents?limit=100")["items"]
            require(all(a["id"] == self.agent_id for a in agents), "fresh database contains foreign Agent")
            if agents:
                agent = agents[0]
                heartbeat = datetime.fromisoformat(agent["last_heartbeat_at"].replace("Z", "+00:00")).timestamp()
                if agent["status"] == "ONLINE" and heartbeat >= self.started_at and "sys_metrics" in agent["capabilities"]:
                    if previous is not None and heartbeat > previous:
                        snapshot = self.api(f"/api/top-processes?agent_id={self.agent_id}&limit=100")
                        matches = [p for p in snapshot["items"] if p.get("pid") == self.target_pid]
                        if snapshot.get("authoritative") is True and snapshot.get("fresh") is True and snapshot.get("snapshot_id") and len(matches) == 1:
                            self.evidence("agent-heartbeat.json", agent)
                            self.evidence("process-snapshot.json", snapshot)
                            self.report["agent"] = {"id": self.agent_id, "fresh_heartbeats_observed": 2,
                                "snapshot_id": snapshot["snapshot_id"], "owned_target_container_id": self.target_container, "owned_target_host_pid": self.target_pid}
                            return
                    previous = heartbeat
            time.sleep(3)
        raise AcceptanceError("fresh heartbeats and authoritative owned-PID snapshot unavailable")

    def task(self) -> None:
        # Read the owned container's host identity independently of API before submission.
        identity = json.loads(self.run(["docker", "inspect", self.target_container], record=False))[0]
        owned_container_ids([identity], self.project)
        require(identity["State"]["Pid"] == self.target_pid and identity["State"]["Running"], "target identity changed")
        stat = Path(f"/proc/{self.target_pid}/stat").read_text()
        self.start_ticks = int(stat[stat.rfind(")") + 2:].split()[19])
        data = self.api("/api/tasks", {"name": "clean-stack-real-sys-metrics", "agent_id": self.agent_id,
                "target_pid": self.target_pid, "collector_type": "sys_metrics", "duration_sec": 6, "sample_rate": 11,
                "options": {"interval_ms": 1000}, "resource_budget": {"max_cpu_percent": 50, "max_memory_mb": 128, "max_output_mb": 8, "max_duration_sec": 6}})
        task_id = data.get("task_id")
        require(isinstance(task_id, str) and re.fullmatch(r"task_[A-Za-z0-9_]+", task_id), "Task admission missing ID")
        self.task_id = task_id
        self.evidence("task-admission.json", data)
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            current = self.api(f"/api/tasks/{task_id}")
            require(current.get("status") not in {"FAILED", "CANCELLED", "TIMEOUT"}, "real collection/analysis failed")
            if current.get("status") == "DONE":
                require(current.get("collection_status") == "COLLECTED" and current.get("analysis_status") == "SUCCESS", "task DONE without real analyzed collection")
                self.evidence("task-final.json", current)
                return
            time.sleep(3)
        raise AcceptanceError("real Task did not reach DONE within budget")

    def artifacts(self) -> None:
        artifacts = self.api(f"/api/tasks/{self.task_id}/artifacts")
        self.evidence("task-artifacts.json", artifacts)
        selected = [a for a in artifacts if a.get("artifact_type") == "sys_metrics"]
        require(len(selected) == 1, "exactly one raw sys_metrics artifact required")
        artifact = selected[0]
        require(artifact.get("task_id") == self.task_id and artifact.get("bucket") == "clean-stack" and artifact.get("object_key"), "unowned object-store evidence")
        raw = self.http(f"/api/tasks/{self.task_id}/artifacts/sys_metrics/download")
        proof = validate_artifact(artifact, raw, self.target_pid, self.start_ticks)
        (self.output / "sys_metrics.raw.json").write_bytes(raw)
        proof["task_id"] = self.task_id
        self.report["artifact"] = proof
        query = f"SELECT json_build_object('process_binding',process_binding_json,'snapshot_id',process_snapshot_id) FROM tasks WHERE id='{self.task_id}';"
        binding = json.loads(self.sql(query))
        b = binding["process_binding"]
        require(b.get("agent_id") == self.agent_id and b.get("pid") == self.target_pid and b.get("process_start_ticks") == self.start_ticks
                and b.get("boot_id") == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                and b.get("pid_namespace_inode") == os.stat(f"/proc/{self.target_pid}/ns/pid").st_ino
                and binding.get("snapshot_id"), "persisted attestation mismatch")
        jobs = json.loads(self.sql(f"SELECT COALESCE(json_agg(json_build_object('id',id,'task_id',task_id,'status',status,'analyzer_type',analyzer_type,'input_artifact_ids',input_artifact_ids_json)), '[]'::json) FROM analysis_jobs WHERE task_id='{self.task_id}';"))
        require(len(jobs) == 1 and jobs[0]["status"] == "SUCCEEDED" and jobs[0]["analyzer_type"] == "collector.sys_metrics"
                and artifact["id"] in jobs[0]["input_artifact_ids"], "independent persisted Analyzer job not SUCCEEDED")
        self.evidence("persisted-chain.json", {"task_binding": binding, "analysis_jobs": jobs})

    def readonly(self) -> None:
        try:
            self.http("/api/agents", authenticated=False)
        except error.HTTPError as exc:
            require(exc.code == 401, "unauthenticated API not rejected with 401")
        else:
            raise AcceptanceError("API authentication unexpectedly bypassed")
        health = self.api("/api/healthz")
        require(health.get("dependencies") == {"database": "healthy", "control_plane": "healthy", "diagnostic_ai": "healthy"}, "API dependency readiness incomplete")
        html = self.http("/", authenticated=False)
        require(b'<div id="root"></div>' in html, "built React entry missing")
        assets = re.findall(rb'(?:src|href)="(/assets/[^\"]+)"', html)
        require(any(a.endswith(b".js") for a in assets) and any(a.endswith(b".css") for a in assets), "compiled Web assets missing")
        self.evidence("web-http-contract.json", {"index_sha256": sha(html), "assets": [{"path": a.decode(), "sha256": sha(self.http(a.decode(), authenticated=False))} for a in assets],
            "scope": "REAL_NGINX_STATIC_ASSETS_AND_AUTHENTICATED_API_PROXY_NOT_BROWSER_INTERACTION", "unauthenticated_api_status": 401, "readiness": health})
        self.evidence("task-events.json", self.api(f"/api/tasks/{self.task_id}/events"))

    def cleanup(self) -> None:
        self.in_cleanup = True
        states = self.container_state()
        ids = owned_container_ids(states, self.project)
        if ids:
            self.run(["docker", "stop", "--time", "15", *ids], timeout=90)
            self.run(["docker", "rm", *ids], timeout=60)
        networks = self.run(["docker", "network", "ls", "-q", "--filter", f"label=com.docker.compose.project={self.project}"], record=False).split()
        for network in networks:
            value = json.loads(self.run(["docker", "network", "inspect", network], record=False))[0]
            require(value.get("Labels", {}).get("com.docker.compose.project") == self.project and not value.get("Containers"), "cleanup refuses foreign/in-use network")
            self.run(["docker", "network", "rm", network])
        require(not self.container_state(), "owned containers remain after cleanup")
        volumes = self.run(["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={self.project}"], record=False).split()
        require(self.private.name.startswith(self.project + "-") and self.private.parent == Path(tempfile.gettempdir()).resolve(), "unsafe private cleanup path")
        self.run(["sudo", "-n", "rm", "-rf", "--", str(self.private)], timeout=30, record=False)
        self.report["cleanup"] = {"status": "PASSED", "removed_container_ids": ids, "removed_network_ids": networks,
            "retained_own_volumes_for_runner_teardown": volumes, "volumes_deleted": False, "global_prune": False}

    def execute(self) -> int:
        failed = None
        try:
            for name in ("preflight", "configuration", "build", "start", "migration", "heartbeat", "task", "artifacts", "readonly"):
                self.stage(name, getattr(self, name))
            require(self.run(["git", "rev-parse", "HEAD"]).strip() == self.expected
                    and not self.run(["git", "status", "--porcelain"]).strip(), "SOURCE_CHANGED during acceptance")
        except BaseException as exc:
            failed = redact(f"{type(exc).__name__}: {exc}", self.values)
            self.report["failure"] = failed
        finally:
            if self.cleanup_authorized:
                self.in_cleanup = True
                try:
                    if (self.private / "runtime.json").exists():
                        self.compose("logs", "--no-color", "--tail", "300", timeout=45)
                except BaseException as exc:
                    self.report["runtime_log_failure"] = redact(str(exc), self.values)
                    failed = failed or "runtime logs could not be preserved"
                    self.report["failure"] = failed
                try:
                    self.stage("cleanup", self.cleanup)
                except BaseException as exc:
                    failed = failed or redact(f"cleanup: {exc}", self.values)
                    self.report["cleanup"]["status"] = "FAILED"
                    self.report["failure"] = failed
            self.report["status"] = "FAILED" if failed else "PASSED"
            self.report["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.report["evidence_files"] = {p.name: {"sha256": sha(p.read_bytes()), "size_bytes": p.stat().st_size}
                                             for p in sorted(self.output.iterdir()) if p.is_file() and p.name != "report.json"}
            self.save()
        print(json.dumps({"status": self.report["status"], "report": str(self.output / "report.json"), "failure": failed}), flush=True)
        return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    args = parser.parse_args()
    return Runner(args.output.resolve(), args.expected_head).execute()


if __name__ == "__main__":
    raise SystemExit(main())
