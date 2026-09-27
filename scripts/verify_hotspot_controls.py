"""Independent CPU controls on disposable Linux Python/Go demo processes.

This does not create AI evidence, mark cloud diagnoses VERIFIED, or prove a
code fix: stopping an injected CPU loop intentionally changes its workload.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import os
import platform
import re
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen

import psutil

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def api(base, path, payload=None):
    request = Request(base + path, data=None if payload is None else json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def pprof_result(text):
    samples = re.search(r"Total samples = ([\d.]+)(ms|s)", text)
    seconds = float(samples[1]) / (1000 if samples[2] == "ms" else 1) if samples else None
    share = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[-1] == "main.goCPUHotFunction":
            share = float(parts[4].rstrip("%"))
    return {"function": "main.goCPUHotFunction", "cumulative_percent": share,
            "sample_cpu_seconds": seconds}


def evaluate(case):
    reasons = []
    windows = case["windows"]
    baseline, fault, recovery = (windows[n] for n in ("baseline", "fault", "recovery"))
    if len({(w["pid"], w["create_time"]) for w in windows.values()}) != 1:
        reasons.append("TARGET_CHANGED")
    def finite(value):
        return type(value) in (int, float) and math.isfinite(value)

    if any(not finite(w["duration_seconds"]) or not finite(w["cpu_percent"])
           or w["duration_seconds"] < 3 or w["cpu_percent"] < 0 for w in windows.values()):
        reasons.append("INVALID_CPU_WINDOW")
    if fault["cpu_percent"] < 25 or fault["cpu_percent"] - baseline["cpu_percent"] < 20:
        reasons.append("CPU_EFFECT_NOT_OBSERVED")
    if recovery["cpu_percent"] > max(10, baseline["cpu_percent"] + 5):
        reasons.append("CPU_NOT_RECOVERED")
    if case.get("injection_active_through_fault_window") is not True:
        reasons.append("INJECTION_NOT_CONFIRMED")
    if case.get("cleanup_verified") is not True:
        reasons.append("CLEANUP_NOT_CONFIRMED")
    profile = case["profile"]
    if case["runtime"] == "python":
        if (not finite(profile.get("new_hot_samples")) or not finite(profile.get("new_total_samples"))
                or profile.get("function") != "source_hot_function" or profile.get("new_hot_samples", 0) < 20
                or profile.get("new_total_samples", 0) < profile.get("new_hot_samples", 0)):
            reasons.append("SOURCE_SAMPLES_INSUFFICIENT")
    elif case["runtime"] == "go":
        if (profile.get("function") != "main.goCPUHotFunction"
                or not finite(profile.get("sample_cpu_seconds")) or profile["sample_cpu_seconds"] < 1
                or not finite(profile.get("cumulative_percent")) or not 50 <= profile["cumulative_percent"] <= 100):
            reasons.append("PPROF_HOTSPOT_INSUFFICIENT")
    else:
        reasons.append("UNKNOWN_RUNTIME")
    return {"status": "CONTROL_VERIFIED" if not reasons else "REJECTED", "reasons": reasons,
            "ai_root_cause_verified": False, "fix_verified": False}


def cpu_window(process, base, seconds):
    identity = process.create_time()
    if api(base, "/snapshot")["pid"] != process.pid:
        raise RuntimeError("HTTP target does not belong to the started child")
    before = process.cpu_times()
    started = time.perf_counter()
    time.sleep(seconds)
    after = process.cpu_times()
    duration = time.perf_counter() - started
    if not process.is_running() or process.create_time() != identity:
        raise RuntimeError("target changed during observation")
    return {"pid": process.pid, "create_time": identity, "duration_seconds": duration,
            "cpu_start_seconds": before.user + before.system, "cpu_end_seconds": after.user + after.system,
            "cpu_percent": 100 * (after.user + after.system - before.user - before.system) / duration,
            "observer": "OS process CPU times / monotonic wall time; one-core percent"}


@contextmanager
def target(runtime, output):
    port = 8081 if runtime == "python" else 6060
    # Refuse an occupied port. Startup additionally checks the HTTP-reported PID.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    if runtime == "python":
        command = [sys.executable, str(Path(__file__).resolve()), "--serve-python"]
    else:
        binary = output / "go-hotspot"
        subprocess.run(["go", "build", "-o", str(binary), "."], cwd=ROOT / "demo/go-hotspot",
                       check=True, timeout=120)
        command = [str(binary)]
    env = dict(os.environ, MINI_DROP_APP_METRICS_PATH=str(output / "app-metrics.json"))
    with (output / "target.log").open("x", encoding="utf-8") as log:
        child = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log, env=env)
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 20
            while True:
                if child.poll() is not None:
                    raise RuntimeError("child exited during startup")
                try:
                    snapshot = api(base, "/snapshot")
                except Exception:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("child readiness timeout")
                    time.sleep(.1)
                    continue
                if snapshot["pid"] != child.pid:
                    raise RuntimeError("port belongs to a different process")
                break
            yield base, psutil.Process(child.pid)
        finally:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def run_case(runtime, output):
    output.mkdir(exist_ok=False)
    switch = "source" if runtime == "python" else "cpu"
    active_key = "source_fault_active" if runtime == "python" else "cpu_fault_active"
    case = {"runtime": runtime, "status": "RUNNING", "windows": {}, "profile": {},
            "scope": "ISOLATED_LINUX_DEMO; INJECTION_WITHDRAWAL_CONTROL; NOT_SAME_LOAD_CODE_FIX",
            "profile_observer": "internal cooperative stack sampler" if runtime == "python" else "Go CPU pprof",
            "cleanup_verified": False}
    path = output / "report.json"
    write(path, case)
    try:
        with target(runtime, output) as (base, process):
            try:
                case["windows"]["baseline"] = cpu_window(process, base, 4)
                before = api(base, f"/faults/{switch}/start", {"duration_seconds": 60})
                if runtime == "go":
                    def profile():
                        with urlopen(base + "/debug/pprof/profile?seconds=5", timeout=12) as response:
                            (output / "cpu.pprof").write_bytes(response.read())
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        pending = pool.submit(profile)
                        case["windows"]["fault"] = cpu_window(process, base, 8)
                        pending.result()
                    text = subprocess.check_output(
                        ["go", "tool", "pprof", "-top", "-nodefraction=0", str(output / "go-hotspot"),
                         str(output / "cpu.pprof")], text=True, stderr=subprocess.STDOUT, timeout=30)
                    (output / "pprof-top.txt").write_text(text, encoding="utf-8")
                    case["profile"] = pprof_result(text)
                else:
                    case["windows"]["fault"] = cpu_window(process, base, 8)
                after = api(base, "/snapshot")
                case["injection_active_through_fault_window"] = bool(before[active_key] and after[active_key])
                if runtime == "python":
                    case["profile"] = {"function": after["hot_function"],
                                       "source_file": after["source_file"], "source_line": after["source_line"],
                                       "new_hot_samples": after["hot_function_samples"] - before["hot_function_samples"],
                                       "new_total_samples": after["source_profile_samples"] - before["source_profile_samples"]}
                write(output / "fault-snapshots.json", {"before": before, "after": after})
            finally:
                if not process.is_running() or api(base, "/snapshot")["pid"] != process.pid:
                    raise RuntimeError("target identity lost before cleanup; refusing control request")
                stopped = api(base, f"/faults/{switch}/stop", {})
                write(output / "cleanup-snapshot.json", stopped)
                case["cleanup_verified"] = stopped.get(active_key) is False
                write(path, case)
            time.sleep(.5)
            case["windows"]["recovery"] = cpu_window(process, base, 4)
            recovered = api(base, "/snapshot")
            write(output / "recovery-snapshot.json", recovered)
            case["cleanup_verified"] = (case["cleanup_verified"] and recovered.get(active_key) is False
                                         and recovered.get("pid") == process.pid)
            case.update(evaluate(case))
    except Exception as exc:
        case.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
    finally:
        case["artifact_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in output.iterdir() if p.is_file() and p.name not in {"report.json", "report.pending"}}
        write(path, case)
    print(json.dumps({"runtime": runtime, "status": case["status"], "reasons": case.get("reasons"),
                      "error": case.get("error")}), flush=True)
    return case


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--serve-python", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve_python:
        spec = importlib.util.spec_from_file_location("fixture_python_hotspot", ROOT / "demo/python-hotspot/app.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        module.ThreadingHTTPServer(("127.0.0.1", 8081), module.ControlHandler).serve_forever()
    else:
        if args.output is None or platform.system() != "Linux":
            parser.error("requires --output and isolated Linux with Go installed")
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=False)
        source_paths = ["scripts/verify_hotspot_controls.py", "demo/python-hotspot/app.py", "demo/go-hotspot/main.go"]
        hashes = {p:hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in source_paths}
        write(output / "source.json", {"git_head": subprocess.check_output(["git", "rev-parse", "HEAD"],
              cwd=ROOT, text=True).strip(), "source_sha256": hashes, "python": platform.python_version()})
        cases = [run_case(runtime, output / runtime) for runtime in ("python", "go")]
        changed = any(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != h for p, h in hashes.items())
        write(output / "report.json", {"status": "PASSED" if not changed and all(c["status"] == "CONTROL_VERIFIED" for c in cases) else "FAILED",
              "cases": cases, "source_changed": changed, "ai_root_cause_verified": False, "fix_verified": False})
        raise SystemExit(0 if not changed and all(c["status"] == "CONTROL_VERIFIED" for c in cases) else 1)
