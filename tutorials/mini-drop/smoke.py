"""Exercise the real local collector/control/analyzer chain in an owned temp directory.

No imported profiles, business services, production database or external model calls.
Build the two executables first; this script only starts the executable it is given.
"""
import argparse
import hashlib
import http.client
import json
import pathlib
import socket
import subprocess
import tempfile
import time


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("control", "collector", "python", "analyzer"):
        parser.add_argument(f"--{name}", required=True, type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args(argv)
    paths = {}
    for name in ("control", "collector", "python", "analyzer"):
        value = getattr(args, name)
        if not value.is_absolute() or not value.is_file():
            parser.error(f"--{name} must name an existing absolute file")
        paths[name] = str(value.resolve())
    checks = []

    def check(value, name):
        if not value:
            raise AssertionError(name)
        checks.append(name)

    with tempfile.TemporaryDirectory(prefix="drop-teaching-smoke-") as owned:
        workdir = pathlib.Path(owned) / "work"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        command = [paths["control"], "--listen", f"127.0.0.1:{port}",
                   "--collector", paths["collector"], "--python", paths["python"],
                   "--analyzer", paths["analyzer"], "--workdir", str(workdir)]
        process = None

        def start():
            nonlocal process
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("teaching control exited before becoming ready")
                try:
                    if request("GET", "/tasks")[0] == 200:
                        return
                except (OSError, http.client.HTTPException):
                    pass
                time.sleep(0.05)
            raise TimeoutError("teaching control startup exceeded 8s")

        def stop():
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        def request(method, path, body=None, key=None, origin=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Idempotency-Key"] = key
            if origin:
                headers["Origin"] = origin
            try:
                connection.request(method, path, body=body, headers=headers)
                response = connection.getresponse()
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ValueError("teaching response exceeded 1MiB")
                return response.status, json.loads(raw)
            finally:
                connection.close()

        def wait_success(task_id, attempt):
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                status, payload = request("GET", f"/tasks/{task_id}")
                if status != 200:
                    raise AssertionError("created task could not be read")
                task = payload["data"]
                if task["attempt"] == attempt and task["status"] in ("SUCCESS", "FAILED"):
                    check(task["status"] == "SUCCESS", "real child processes complete successfully")
                    return task
                time.sleep(0.05)
            raise TimeoutError("teaching task did not finish within 25s")

        try:
            start()
            status, payload = request("POST", "/tasks", '{"iterations":10000000}', "smoke-create-001")
            check(status == 201, "first request creates a task")
            task_id = payload["data"]["id"]
            status, replay = request("POST", "/tasks", '{"iterations":10000000}', "smoke-create-001")
            check(status == 200 and replay["data"]["id"] == task_id, "same request replays the same task")
            check(request("POST", "/tasks", '{"iterations":2000}', "smoke-create-001")[0] == 409,
                  "changed parameters conflict with an existing key")
            for body in ('{"iterations":1000,"iterations":2000}', '{"iterations":true}',
                         '{"iterations":1000,"command":"ignored"}', '{"iterations":1000} {}'):
                check(request("POST", "/tasks", body, "smoke-invalid-001")[0] == 400,
                      "strict HTTP input is rejected: " + body)
            check(request("POST", "/tasks", '{"iterations":1000}', "smoke-origin-001",
                          "https://example.invalid")[0] == 403, "foreign origin is rejected")
            task = wait_success(task_id, 1)
            attempt = workdir / "tasks" / task_id / "attempt-1"
            raw = (attempt / "raw.json").read_bytes()
            profile = json.loads(raw)
            result = json.loads((attempt / "result.json").read_bytes())
            check(result == task["result"], "API result equals the analyzer artifact")
            check(hashlib.sha256(raw).hexdigest() == result["input_sha256"], "result binds to actual collector bytes")
            check(profile["iterations"] == 10000000 and profile["source"] == "controlled_self"
                  and profile["unit"] == "cpu_ticks", "measurement scope and unit remain explicit")
            check(profile["total_ticks"] == result["summary"]["total_ticks"]
                  == result["flamegraph"]["children"][0]["self"], "actual CPU count survives both handoffs")
            first_events = [event["kind"] for event in task["events"]]
            check(first_events == ["TASK_QUEUED", "TASK_RUNNING", "TASK_SUCCEEDED"], "state transition events are recorded")
            check(len(request("GET", "/tasks")[1]["data"]) == 1, "replays and invalid requests created no extra work")
            stop()
            start()
            recovered = request("GET", f"/tasks/{task_id}")[1]["data"]
            check(recovered == task, "restart preserves completed evidence without resampling")
            status, retried = request("POST", f"/tasks/{task_id}/retry", '{}', "smoke-retry-001")
            check(status == 201 and retried["data"]["attempt"] == 2, "explicit retry opens attempt two")
            second = wait_success(task_id, 2)
            check((attempt / "raw.json").read_bytes() == raw, "retry preserves attempt one's raw artifact")
            check(len(second["events"]) == 6 and second["result"]["input_sha256"]
                  == hashlib.sha256((workdir / "tasks" / task_id / "attempt-2" / "raw.json").read_bytes()).hexdigest(),
                  "second attempt publishes its own evidence")
            receipt = {"passed": True, "scope": "real local controlled-self CPU collection; not function sampling or production validation",
                       "check_count": len(checks), "checks": checks,
                       "measurement_summary": {key: profile[key] for key in ("source", "unit", "total_ticks", "clock_ticks_per_second")},
                       "first_attempt_sha256": result["input_sha256"],
                       "final_status": second["status"], "final_attempt": second["attempt"]}
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(receipt, ensure_ascii=True, indent=2))
        finally:
            stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
