#!/usr/bin/env python3
"""Isolated, real Drop stack for local development. Python standard library only."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import http.cookiejar
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_IMAGE = "nginxinc/nginx-unprivileged:1.30.6-alpine"
APP_IMAGES = {"core": "drop-core", "api": "drop-api", "analyzer": "drop-analyzer", "web": "drop-web", "nginx": "drop-nginx"}


class DevError(Exception):
    pass


def workspace(instance: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,23}", instance):
        raise DevError("Instance must start with a letter and contain at most 24 lowercase letters, digits or hyphens.")
    root = ROOT / ".local" / "dev" / instance
    # Resolve every ancestor: an existing symlink must not redirect credentials.
    if root.resolve() != root.absolute():
        raise DevError("The development workspace must not contain a symlink.")
    return root


def settings(instance: str, port: int, images: dict[str, str] | None = None) -> dict:
    if not 1024 <= port <= 65535:
        raise DevError("Choose a local port between 1024 and 65535.")
    selected = {name: f"{repo}:dev-{instance}" for name, repo in APP_IMAGES.items()}
    for name, reference in (images or {}).items():
        if name not in selected or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:_@-]{1,199}", reference):
            raise DevError("Invalid image set.")
        selected[name] = reference
    return {"schema": 1, "instance": instance, "project": f"txdrop-dev-{instance}", "port": port, "images": selected}


def overlay(config: dict) -> dict:
    images = config["images"]
    services = {}
    for service, image, recipe in [
        ("api", "api", "api/Dockerfile"), ("migration", "api", "api/Dockerfile"),
        ("drop-server", "core", "core/Dockerfile"), ("agent", "core", "core/Dockerfile"),
        ("analyzer", "analyzer", "analyzer/Dockerfile"), ("web", "web", "web/Dockerfile"),
        ("minio", "nginx", "deploy/Dockerfile.nginx"),
    ]:
        services[service] = {"image": images[image], "build": {"context": str(ROOT), "dockerfile": recipe}}
    # The basic runtime deliberately needs no ptrace, BPF, privileged container,
    # host PID namespace or Docker socket. PROC_CPU uses a real controlled target.
    for service in ("drop-server", "agent"):
        services[service]["build"]["target"] = "runtime"
    services["agent"]["environment"] = {"AGENT_HOSTNAME": "local-development", "ALLOW_HOST_PROCESSES": "false"}
    services["api"]["environment"] = {"PUBLIC_URL": f"http://127.0.0.1:{config['port']}"}
    # Cached program images still use this checkout's authoritative catalog.
    # Copy it into the isolated instance; never widen the host-mount allowlist.
    services["api"]["volumes"] = [{
        "type": "bind", "source": str(workspace(config["instance"]) / "task-kinds.json"),
        "target": "/app/contracts/task-kinds.json", "read_only": True,
    }]
    services["storage-init"] = {"image": images["analyzer"]}
    services["backup-client"] = {"image": images["analyzer"]}
    services["analyzer"].update(mem_limit="768m", memswap_limit="768m", tmpfs=["/tmp:size=256m,mode=1777"])
    # Override YAML is used for ports because normal Compose list merging would
    # leave the production :80 listener in place.
    return {"services": services}


def prepare(config: dict) -> Path:
    path = workspace(config["instance"])
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(child.is_symlink() for child in path.rglob("*")):
        raise DevError("The development workspace must not contain symlinks.")
    for name in ("storage-gateway.conf", "db-permissions.sql", "bootstrap.sh"):
        shutil.copyfile(ROOT / "deploy" / name, path / name)
    shutil.copyfile(ROOT / "contracts" / "task-kinds.json", path / "task-kinds.json")
    (path / "task-kinds.json").chmod(0o644)
    (path / "settings.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    (path / ".env").write_text("# Development only; no production environment is loaded.\n", encoding="utf-8")
    (path / "overlay.json").write_text(json.dumps(overlay(config), indent=2) + "\n", encoding="utf-8")
    (path / "ports.yml").write_text(
        f"services:\n  web:\n    ports: !override ['127.0.0.1:{config['port']}:8080']\n", encoding="utf-8"
    )
    return path


def environment() -> dict[str, str]:
    # Never inherit a terminal's production image selection or Compose profile.
    return {key: value for key, value in os.environ.items()
            if not key.startswith(("COMPOSE_", "DROP_")) and key not in {
                "RELEASE_TAG", "API_RELEASE_TAG", "WEB_RELEASE_TAG", "ANALYZER_RELEASE_TAG", "NGINX_RELEASE_TAG", "PUBLIC_URL"}}


def run(argv: list[str], *, capture=False, timeout=240) -> str:
    try:
        result = subprocess.run(argv, cwd=ROOT, env=environment(), check=False,
                                stdout=subprocess.PIPE if capture else None,
                                stderr=subprocess.PIPE if capture else None,
                                text=True, timeout=timeout)
    except FileNotFoundError:
        raise DevError("Docker was not found. Install Docker Desktop (Linux containers) or Docker Engine with Compose.") from None
    except subprocess.TimeoutExpired:
        raise DevError("The development command timed out; existing data is retained.") from None
    if result.returncode:
        raise DevError("Development command failed. Use 'doctor' for prerequisites or 'logs' for service status.")
    return result.stdout if capture else ""


def doctor() -> None:
    try:
        kind = run(["docker", "info", "--format", "{{.OSType}}"], capture=True, timeout=15)
        if kind.strip() != "linux":
            raise DevError("Linux containers are required.")
    except DevError:
        raise DevError("Docker is unavailable. Start Docker Desktop in Linux-container mode, then run this command again.") from None
    version = run(["docker", "compose", "version", "--short"], capture=True, timeout=15)
    parts = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version.strip())
    if not parts or tuple(map(int, parts.groups())) < (2, 24, 4):
        raise DevError("Docker Compose 2.24.4 or newer is required for isolated port overrides.")
    print("Docker is ready. Local storage and credentials stay separate from production.")


def compose(config: dict, path: Path) -> list[str]:
    return ["docker", "compose", "--parallel", "1", "--project-directory", str(path),
            "--env-file", str(path / ".env"), "-p", config["project"],
            "-f", str(ROOT / "deploy" / "compose.yml"), "-f", str(path / "overlay.json"), "-f", str(path / "ports.yml")]


def validate_rendered(config: dict, value: dict) -> None:
    if value.get("name") != config["project"] or not config["project"].startswith("txdrop-dev-"):
        raise DevError("Refusing a non-development Compose project.")
    for name, volume in value.get("volumes", {}).items():
        if volume.get("external") or not volume.get("name", "").startswith(config["project"] + "_"):
            raise DevError("Refusing a shared or external volume.")
    root = workspace(config["instance"]).resolve()
    for secret in value.get("secrets", {}).values():
        source = Path(secret.get("file", "")).resolve()
        if not source.is_relative_to(root):
            raise DevError("Refusing credentials outside the isolated development workspace.")
    for name, service in value.get("services", {}).items():
        if service.get("privileged") or service.get("pid") == "host":
            raise DevError("Refusing privileged or host-PID development services.")
        for port in service.get("ports", []):
            if name != "web" or port.get("host_ip") != "127.0.0.1" or str(port.get("published")) != str(config["port"]):
                raise DevError("Refusing a non-loopback development listener.")
        for volume in service.get("volumes", []):
            if volume.get("type") == "bind" and not Path(volume["source"]).resolve().is_relative_to(root):
                raise DevError("Refusing host paths outside the isolated development workspace.")


def smoke(config: dict, path: Path) -> dict:
    """Create one real PROC_CPU diagnostic inside this isolated project only."""
    validate_live_web(config, path)
    origin = f"http://127.0.0.1:{config['port']}"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    csrf = ""
    def request(endpoint, body=None):
        headers = {"Content-Type":"application/json", "Origin":origin}
        if body is not None:
            headers.update({"X-CSRF-Token":csrf,"Idempotency-Key":str(uuid.uuid4())})
        req = urllib.request.Request(origin + "/api/v1" + endpoint,
            data=json.dumps(body).encode() if body is not None else None, headers=headers)
        with opener.open(req, timeout=20) as response:
            raw=response.read(2*1024**2+1)
            if len(raw)>2*1024**2: raise DevError("Local smoke response exceeded its bound.")
            return json.loads(raw)["data"]
    auth=request("/auth/login", {"username":(path/"secrets/admin_username").read_text().strip(),
                                  "password":(path/"secrets/admin_password").read_text().strip()})
    csrf=auth["csrf_token"]
    agents=request("/agents?page_size=20")["items"]
    agent=next((a for a in agents if a["id"]=="aliyun-local" and "PROC_CPU" in a.get("capabilities",[])),None)
    if not agent: raise DevError("The controlled local Agent is not ready for PROC_CPU.")
    processes=agent.get("resources",{}).get("processes",[])
    target=next((p for p in processes if isinstance(p.get("pid"),int) and p["pid"]>1),None)
    if not target: raise DevError("No controlled process is advertised by the local Agent.")
    task=request("/tasks", {"name":"Local development smoke "+uuid.uuid4().hex[:8], "kind":"PROC_CPU",
        "target_agent_id":agent["id"], "visibility":"private", "parameters":{"pid":target["pid"],"duration_seconds":2,"frequency_hz":10}})
    task_id=str(task["id"])
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        task=request("/tasks/"+task_id)
        if task["collection_status"] in ("FAILED","CANCELED") or task["analysis_status"]=="FAILED":
            raise DevError("The controlled local diagnostic failed. Inspect the local task's bounded failure evidence.")
        if task["collection_status"]=="COLLECTED" and task["analysis_status"]=="SUCCESS":
            return {"project":config["project"], "task_id":task_id, "collection_status":"COLLECTED", "analysis_status":"SUCCESS", "source":"real controlled PROC_CPU; not synthetic folded data"}
        time.sleep(1)
    raise DevError("Local smoke timed out; the task and its evidence are retained.")


def validate_live_web(config: dict, path: Path) -> None:
    container=run(compose(config,path)+["ps","-q","web"],capture=True).strip()
    if not re.fullmatch(r"[a-f0-9]{12,64}",container):
        raise DevError("Start this isolated development stack before running smoke.")
    template="{{json .Config.Labels}}\n{{json .NetworkSettings.Ports}}\n{{json .State.Running}}"
    lines=run(["docker","inspect","--format",template,container],capture=True).splitlines()
    if len(lines)!=3:
        raise DevError("Cannot verify the local Web container identity.")
    labels,ports,running=map(json.loads,lines)
    expected=[{"HostIp":"127.0.0.1","HostPort":str(config["port"])}]
    if (labels.get("com.docker.compose.project")!=config["project"] or
        labels.get("com.docker.compose.service")!="web" or not running or
        ports.get("8080/tcp")!=expected):
        raise DevError("Refusing smoke against an unverified listener or project.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["doctor", "plan", "up", "stop", "status", "logs", "check", "smoke"])
    parser.add_argument("--instance", default="local")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--image-set", type=Path, help="JSON map of prebuilt core/api/analyzer/web/nginx image references")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--bootstrap-image", help="Prebuilt image containing bash and openssl; defaults to Alpine bootstrap")
    args = parser.parse_args()
    if args.command == "doctor":
        doctor(); return 0
    path = workspace(args.instance)
    previous = path / "settings.json"
    if previous.exists():
        config = json.loads(previous.read_text(encoding="utf-8"))
        # Revalidate saved values rather than trusting editable local JSON.
        images = json.loads(args.image_set.read_text(encoding="utf-8")) if args.image_set else config["images"]
        config = settings(args.instance, config["port"], images)
    else:
        images = json.loads(args.image_set.read_text(encoding="utf-8")) if args.image_set else None
        config = settings(args.instance, args.port, images)
    bootstrap_image = args.bootstrap_image or BOOTSTRAP_IMAGE
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./:_@-]{1,199}", bootstrap_image):
        raise DevError("Invalid bootstrap image reference.")
    if args.command == "plan":
        print(json.dumps({"project":config["project"], "url":f"http://127.0.0.1:{config['port']}", "workspace":str(path), "images":config["images"], "production_access":False}, indent=2)); return 0
    doctor()
    path = prepare(config)
    base = compose(config, path)
    rendered = json.loads(run(base + ["config", "--format", "json"], capture=True))
    validate_rendered(config, rendered)
    if args.command == "check":
        print("Compose configuration passed: isolated volumes, private credentials, loopback-only ports."); return 0
    if args.command == "smoke":
        try:
            print(json.dumps(smoke(config,path),indent=2)); return 0
        except DevError:
            raise
        except Exception:
            raise DevError("Local smoke could not complete; no credentials or signed URLs were logged.") from None
    if args.command == "up":
        run(["docker", "run", "--rm", "--user", "0:0", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
             "--memory", "128m", "--memory-swap", "128m", "--cpus", "0.5", "--pids-limit", "32",
             "--mount", f"type=bind,src={path},dst=/workspace", "--workdir", "/workspace", "--entrypoint", "/bin/sh",
             bootstrap_image, "-ec", "if ! command -v bash >/dev/null || ! command -v openssl >/dev/null; then apk add --no-cache bash openssl >/dev/null; fi; exec bash ./bootstrap.sh"])
        if not args.no_build:
            run(base + ["build"], timeout=3600)
        run(base + ["up", "-d", "--no-build", "--wait", "--wait-timeout", "180"], timeout=240)
        # Nginx resolves static Docker upstream names at startup. Replacing only
        # API or storage can change an address while the proxy remains healthy.
        for service in ("minio", "web"):
            run(base + ["exec", "-T", service, "nginx", "-t"])
            run(base + ["exec", "-T", service, "nginx", "-s", "reload"])
        print(f"Drop is ready at http://127.0.0.1:{config['port']}")
        print(f"Local admin credentials: {path / 'secrets' / 'admin_username'} and {path / 'secrets' / 'admin_password'}")
        print("For frontend hot reload: cd web, then npm run dev:local. Backend and data stay in this isolated stack.")
    elif args.command == "stop":
        run(base + ["stop"])
        print("Development services stopped. Data volumes retained.")
    elif args.command == "status":
        run(base + ["ps"])
    elif args.command == "logs":
        run(base + ["logs", "--tail", "80", "api", "drop-server", "agent", "analyzer", "web"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DevError, ValueError, OSError, KeyError):
        # Do not print command stderr, configuration values or exception text:
        # Compose and storage errors may include credentials or signed URLs.
        exc = sys.exception()
        print(str(exc) if isinstance(exc, DevError) else "Invalid local development configuration.", file=sys.stderr)
        raise SystemExit(1) from None
