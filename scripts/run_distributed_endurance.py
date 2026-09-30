"""Two-host fixture experiment over SSH; network/tunnel cost is part of latency."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import queue
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo.rag_service.app import DATASET_SHA, QUESTIONS, KnowledgeService, Settings, serve
from scripts.process_resource_monitor import ResourceMonitor, summarize_resources
from scripts.run_load_endurance import Plan, measure, request, campaign_status, capacity_summary, write_json, source_hashes


def machine_identity():
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            value = winreg.QueryValueEx(key, "MachineGuid")[0]
    else:
        value = Path("/etc/machine-id").read_text().strip()
    if not value:
        raise RuntimeError("stable host identity unavailable")
    return hashlib.sha256(value.encode()).hexdigest()


def sources():
    return {**source_hashes(), "scripts/run_distributed_endurance.py":
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def validate_identity(ready, local_machine, expected_sources, run_id):
    if ready.get("run_id") != run_id:
        raise ValueError("remote campaign identity mismatch")
    remote = ready.get("machine_id_sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", remote) or remote == local_machine:
        raise ValueError("two distinct host identities are required")
    if ready.get("source_hashes") != expected_sources or ready.get("dataset_sha256") != DATASET_SHA:
        raise ValueError("remote fixture source or dataset mismatch")
    if type(ready.get("pid")) is not int or ready["pid"] < 1:
        raise ValueError("invalid remote PID")
    if type(ready.get("port")) is not int or not 1 <= ready["port"] <= 65535:
        raise ValueError("invalid remote port")



def validate_windows(windows, phases):
    import math
    if len(windows) != len(phases):
        raise ValueError("remote phase coverage incomplete")
    previous = None
    for window, (name, rate, duration) in zip(windows, phases):
        start, end = window.get("started"), window.get("ended")
        if window.get("phase") != name or any(type(x) not in (int, float) or not math.isfinite(x) for x in (start, end)):
            raise ValueError("invalid remote phase window")
        if end - start < duration or (previous is not None and start != previous):
            raise ValueError("remote phase window does not cover load")
        previous = end


def verify_distributed(path, require_pass=False):
    from scripts.verify_load_report import verify
    result = verify(path, require_pass)
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    if report.get("scope") != "TWO_HOST_SSH_HTTP_FIXTURE; NETWORK_AND_TUNNEL_INCLUDED; NO_LIVE_LLM":
        raise ValueError("not a distributed fixture report")
    validate_identity(report["remote"], report["environment"]["machine_id_sha256"], report["source_hashes"], report["run_id"])
    if report.get("remote_cleanup_confirmed") is not True or report["plan"]["p95_limit_ms"] != 200:
        raise ValueError("cleanup or fixed SLO missing")
    transport = report.get("transport")
    if transport is not None:
        if not isinstance(transport, dict):
            raise ValueError("invalid transport metadata")
        count = transport.get("ssh_tunnel_count")
        routing = "THREAD_AFFINITY" if report["plan"].get("reuse_connections", False) else "ARRIVAL_ROUND_ROBIN"
        if (type(count) is not int or not 1 <= count <= 8 or transport.get("routing") != routing
                or transport.get("tunnel_cleanup_confirmed") is not True):
            raise ValueError("invalid transport identity or cleanup")
        for stage in report["stages"]:
            for line in (Path(path).parent / stage["raw_file"]).read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row["sent"] and (type(row.get("transport_index")) is not int or not 0 <= row["transport_index"] < count):
                    raise ValueError("invalid request transport index")
    phases = [(s["name"], s["rate"], s["duration_seconds"]) for s in report["stages"]]
    validate_windows(report["remote_phase_windows"], phases)
    for line in (Path(path).parent / "resources.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["pid"] != report["remote"]["pid"] or row["create_time"] != report["remote"]["create_time"]:
            raise ValueError("remote resource identity mismatch")
    return {**result, "distinct_host_identities": True, "remote_cleanup_confirmed": True}

def emit(value):
    print(json.dumps(value, allow_nan=False), flush=True)


def serve_remote(output, run_id, dependency_latency_ms, cache_rerank=False):
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("invalid campaign ID")
    if not 0 <= dependency_latency_ms <= 100:
        raise ValueError("invalid dependency latency")
    output.mkdir(parents=True, exist_ok=False)
    # Loss of control stdin ends the fixture; hard TTL also bounds orphan lifetime.
    watchdog = threading.Timer(4500, lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    frozen = sources()
    service = KnowledgeService(Settings(dependency_latency_ms=dependency_latency_ms, cache_rerank=cache_rerank))
    http = serve(service, port=0)
    server = threading.Thread(target=http.serve_forever, daemon=True)
    server.start()
    try:
        with ResourceMonitor(os.getpid(), output / "resources.jsonl") as monitor:
            ready = {"run_id": run_id, "machine_id_sha256": machine_identity(),
                     "pid": os.getpid(), "create_time": monitor.created, "port": http.server_port,
                     "source_hashes": frozen, "dataset_sha256": DATASET_SHA,
                     "platform": platform.platform(), "python": platform.python_version(),
                     "cpu_count": os.cpu_count(), "dependency_latency_ms": dependency_latency_ms}
            write_json(output / "identity.json", ready)
            emit(ready)
            phase_windows = []
            for line in sys.stdin:
                command = json.loads(line)
                if command == {"finish": True}:
                    if phase_windows:
                        phase_windows[-1]["ended"] = time.perf_counter()
                    break
                phase = command.get("phase", "")
                if not re.fullmatch(r"step-[1-9][0-9]*|recovery|soak", phase):
                    raise ValueError("invalid phase")
                now = time.perf_counter()
                if phase_windows:
                    phase_windows[-1]["ended"] = now
                phase_windows.append({"phase": phase, "started": now})
                monitor.phase = phase
                emit({"phase": phase, "run_id": run_id, "pid": os.getpid()})
        if sources() != frozen:
            raise RuntimeError("remote source changed during measurement")
        payload = {"run_id": run_id, "pid": os.getpid(), "resources": monitor.rows,
                   "source_hashes": frozen, "create_time": monitor.created, "phase_windows": phase_windows}
        write_json(output / "completed.json", {k: v for k, v in payload.items() if k != "resources"})
        emit(payload)
    finally:
        http.shutdown()
        http.server_close()
        service.close()
        watchdog.cancel()


class Channel:
    def __init__(self, process):
        self.process = process
        self.lines = queue.Queue()
        def read():
            for line in process.stdout:
                self.lines.put(line)
            self.lines.put(None)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def receive(self, timeout=30):
        line = self.lines.get(timeout=timeout)
        if line is None:
            raise RuntimeError("remote controller exited before completion")
        return json.loads(line)

    def send(self, value):
        self.process.stdin.write(json.dumps(value) + "\n")
        self.process.stdin.flush()


def ssh_base(host, identity=None):
    if not re.fullmatch(r"[a-zA-Z0-9_.@:-]+", host) or host.startswith("-"):
        raise ValueError("invalid SSH host or alias")
    args = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=2"]
    if identity:
        args += ["-i", str(identity)]
    return args, host


def run(output, plan, host, remote_root, remote_output, python="python3", identity=None, tunnel_count=4):
    plan.validate()
    if type(tunnel_count) is not int or not 1 <= tunnel_count <= 8:
        raise ValueError("SSH tunnel count must be an integer from 1 to 8")
    if len(plan.rates) * (plan.step_seconds + plan.timeout_seconds) + plan.recovery_seconds + plan.soak_seconds + 120 > 4400:
        raise ValueError("campaign exceeds remote watchdog budget")
    if plan.p95_limit_ms != 200:
        raise ValueError("distributed acceptance requires unchanged 200ms SLO")
    if not remote_root.startswith("/") or not remote_output.startswith("/"):
        raise ValueError("remote source and evidence paths must be absolute")
    ssh, destination = ssh_base(host, identity)
    output.mkdir(parents=True, exist_ok=False)
    run_id, frozen, machine = uuid.uuid4().hex, sources(), machine_identity()
    report = {"schema": "mini-drop.load-endurance.v1", "status": "RUNNING", "run_id": run_id,
              "started_at": datetime.now(timezone.utc).isoformat(), "plan": asdict(plan),
              "source_hashes": frozen, "dataset_sha256": DATASET_SHA,
              "question_set_sha256": hashlib.sha256(json.dumps(QUESTIONS).encode()).hexdigest(),
              "transport": {"ssh_tunnel_count": tunnel_count, "routing": "THREAD_AFFINITY" if plan.reuse_connections else "ARRIVAL_ROUND_ROBIN"},
              "environment": {"platform": platform.platform(), "python": platform.python_version(),
                              "machine_id_sha256": machine, "cpu_count": os.cpu_count()},
              "scope": "TWO_HOST_SSH_HTTP_FIXTURE; NETWORK_AND_TUNNEL_INCLUDED; NO_LIVE_LLM",
              "limitations": ["Dedicated fixture, not production capacity", "SSH and network cost included",
                              "Distinct OS identities do not establish distinct physical hardware",
                              "Resource growth screen is not proof of absence of leaks"], "stages": []}
    report["git_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    path = output / "report.json"
    write_json(path, report)
    command = "cd " + shlex.quote(remote_root) + " && exec " + shlex.join([
        python, "-u", "scripts/run_distributed_endurance.py", "--serve", "--output", remote_output,
        "--run-id", run_id, "--dependency-latency-ms", str(plan.dependency_latency_ms),
        *(['--cache-rerank'] if plan.cache_rerank else [])])
    child, tunnels = None, []
    try:
        with (output / "ssh.stderr.log").open("x", encoding="utf-8") as errors:
            child = subprocess.Popen(ssh + [destination, command], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8")
            channel = Channel(child)
            ready = channel.receive()
            validate_identity(ready, machine, frozen, run_id)
            report["remote"] = ready
            report["fixture_pid"] = ready["pid"]
            endpoints = []
            for _ in range(tunnel_count):
                with socket.socket() as reserve:
                    reserve.bind(("127.0.0.1", 0))
                    port = reserve.getsockname()[1]
                tunnel = subprocess.Popen(ssh + ["-o", "ExitOnForwardFailure=yes", "-N", "-L",
                    f"127.0.0.1:{port}:127.0.0.1:{ready['port']}", destination],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors)
                tunnels.append(tunnel)
                for _ in range(100):
                    if tunnel.poll() is not None:
                        raise RuntimeError("SSH forward failed")
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=.1):
                            break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError("SSH forward did not become ready")
                endpoints.append(f"http://127.0.0.1:{port}/query")
            endpoint = tuple(endpoints)
            warmup = []
            for index in range(4):
                now = time.perf_counter()
                warmup.append(request(endpoint[index % len(endpoint)], index, now, now, plan.timeout_seconds))
            report["warmup"] = warmup
            if not all(row["success"] and row["quality_passed"] for row in warmup):
                raise RuntimeError("remote warmup failed")
            phases = [(f"step-{i+1}", rate, plan.step_seconds) for i, rate in enumerate(plan.rates)]
            phases += [("recovery", plan.soak_rate, plan.recovery_seconds), ("soak", plan.soak_rate, plan.soak_seconds)]
            for name, rate, duration in phases:
                channel.send({"phase": name})
                if channel.receive() != {"phase": name, "run_id": run_id, "pid": ready["pid"]}:
                    raise RuntimeError("remote phase acknowledgment mismatch")
                report["stages"].append(measure(endpoint, name, rate, duration, plan, output))
                write_json(path, report)
                emit({"phase": name, "summary": report["stages"][-1]["summary"]})
            channel.send({"finish": True})
            final = channel.receive()
            if any(final.get(key) != ready[key] for key in ("run_id", "pid", "create_time", "source_hashes")):
                raise RuntimeError("remote identity or source changed")
            if child.wait(timeout=35) != 0:
                raise RuntimeError("remote fixture cleanup failed")
            validate_windows(final["phase_windows"], phases)
            report["remote_phase_windows"] = final["phase_windows"]
            report["remote_cleanup_confirmed"] = True
            rows = final["resources"]
            if any(r["pid"] != ready["pid"] or r["create_time"] != ready["create_time"] for r in rows):
                raise RuntimeError("remote resource target changed")
            with (output / "resources.jsonl").open("x", encoding="utf-8") as stream:
                for row in rows:
                    stream.write(json.dumps(row, allow_nan=False) + "\n")
            report["resources"] = summarize_resources(rows, plan.soak_seconds)
            report["capacity"] = capacity_summary(report["stages"][:-2])
            report["recovery_status"] = report["stages"][-2]["summary"]["status"]
            report["soak_status"] = ("PASSED" if report["stages"][-1]["summary"]["status"] == "PASSED" and
                                      all(b["summary"]["status"] == "PASSED" for b in report["stages"][-1]["buckets"]) else "FAILED")
            report["status"] = campaign_status(report["stages"])
            if report["resources"]["status"] != "PASSED":
                report["status"] = "INVALID" if report["resources"]["status"] == "INVALID" else "FAILED"
            if sources() != frozen:
                raise RuntimeError("local measurement source changed")
    except BaseException as exc:
        report["status"] = "FAILED"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if child and child.poll() is None:
            child.stdin.close()  # EOF requests cleanup; no remote kill of unrelated processes.
            try:
                child.wait(timeout=35)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=10)
        for tunnel in tunnels:
            if tunnel.poll() is None:
                tunnel.terminate()
                try:
                    tunnel.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    tunnel.kill()
                    tunnel.wait(timeout=10)
        report["transport"]["tunnel_cleanup_confirmed"] = len(tunnels) == tunnel_count and all(t.poll() is not None for t in tunnels)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["artifact_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob("*.jsonl")}
        report["sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()
        write_json(path, report)
    verify_distributed(path)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host")
    parser.add_argument("--remote-root")
    parser.add_argument("--remote-output")
    parser.add_argument("--remote-python", default="python3")
    parser.add_argument("--identity", type=Path)
    parser.add_argument("--ssh-tunnels", type=int, default=4, help="Independent transports (1 to 8); no retries or omitted slow samples")
    parser.add_argument("--soak-seconds", type=int, default=3600)
    parser.add_argument("--rates", type=int, nargs="+", default=[5, 20, 40, 60])
    parser.add_argument("--step-seconds", type=int, default=15)
    parser.add_argument("--concurrency", type=int, default=128)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--run-id", help=argparse.SUPPRESS)
    parser.add_argument("--dependency-latency-ms", type=int, default=10, help=argparse.SUPPRESS)
    parser.add_argument('--cache-rerank', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--no-connection-reuse', action='store_true')
    parser.add_argument('--no-rerank-cache', action='store_true')
    args = parser.parse_args()
    if args.serve:
        serve_remote(args.output, args.run_id, args.dependency_latency_ms, args.cache_rerank)
    else:
        if not all((args.host, args.remote_root, args.remote_output)):
            parser.error("--host, --remote-root and --remote-output are required")
        result = run(args.output, Plan(rates=tuple(args.rates), step_seconds=args.step_seconds,
                 soak_seconds=args.soak_seconds, concurrency=args.concurrency,
                 reuse_connections=not args.no_connection_reuse, cache_rerank=not args.no_rerank_cache), args.host,
                     args.remote_root, args.remote_output, args.remote_python, args.identity, args.ssh_tunnels)
        raise SystemExit(0 if result["status"] == "PASSED" else 1)
