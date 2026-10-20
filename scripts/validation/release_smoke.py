"""Authorized public-release check against this task's fixed server.

Uses real controlled processes advertised by its Agent. Credentials, cookies,
profile bytes and signed URLs never enter output or exception text.
"""
import argparse
import hashlib
import http.cookiejar
import json
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ORIGIN = "https://120.24.187.205"
PHASE = "initialization"


def main():
    global PHASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--secrets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collect", action="store_true", required=True)
    args = parser.parse_args()
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    csrf = ""

    def api(path, body=None):
        headers = {"Content-Type": "application/json", "Origin": ORIGIN}
        if body is not None:
            headers.update({"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid.uuid4())})
        req = urllib.request.Request(ORIGIN + "/api/v1" + path, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        with opener.open(req, timeout=20) as response:
            raw = response.read(2 * 1024**2 + 1)
            if len(raw) > 2 * 1024**2:
                raise RuntimeError("PUBLIC_JSON_BOUND_EXCEEDED")
            return json.loads(raw)["data"]

    PHASE = "login"
    auth = api("/auth/login", {"username": (args.secrets / "admin_username").read_text().strip(),
                               "password": (args.secrets / "admin_password").read_text().strip()})
    csrf = auth["csrf_token"]
    if not list(jar) or not all(cookie.secure for cookie in jar):
        raise RuntimeError("PUBLIC_SESSION_COOKIE_NOT_SECURE")
    PHASE = "historical_result"
    initial = api("/tasks?page_size=20")["items"]
    historical = next((t for t in initial if t["analysis_status"] == "SUCCESS" and t["collection_status"] == "COLLECTED"), None)
    if not historical:
        raise RuntimeError("PRESERVED_SUCCESSFUL_TASK_MISSING")
    if not api("/tasks/" + str(historical["id"]) + "/result").get("flamegraph"):
        raise RuntimeError("PRESERVED_RESULT_NOT_READABLE")
    PHASE = "controlled_target"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        agent = next(a for a in api("/agents?page_size=20")["items"] if a["id"] == "aliyun-local")
        if "PYTHON_MEMORY" in agent["capabilities"]:
            break
        time.sleep(2)
    if "PYTHON_MEMORY" not in agent["capabilities"]:
        raise RuntimeError("VALIDATED_PYTHON_MEMORY_TOOLCHAIN_UNAVAILABLE")
    target = next(p for p in agent["resources"]["processes"] if p.get("pid", 0) > 1 and p.get("uid") == 10001 and p.get("name", "").startswith("drop_workload"))
    reports = []
    for kind in ("PROC_CPU", "PERF_CPU"):
        PHASE = kind + "_collection"
        if kind not in agent["capabilities"]:
            raise RuntimeError("CONTROLLED_" + kind + "_UNAVAILABLE")
        task = api("/tasks", {"name": "发布验收 · 真实受控 " + kind + " " + uuid.uuid4().hex[:8],
                              "kind": kind, "target_agent_id": agent["id"], "visibility": "private",
                              "parameters": {"pid": target["pid"], "duration_seconds": 2, "frequency_hz": 10}})
        task_id = str(task["id"])
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            task = api("/tasks/" + task_id)
            if task["collection_status"] in ("FAILED", "CANCELED") or task["analysis_status"] == "FAILED":
                raise RuntimeError("PUBLIC_CONTROLLED_" + kind + "_FAILED")
            if task["collection_status"] == "COLLECTED" and task["analysis_status"] == "SUCCESS":
                break
            time.sleep(1)
        else:
            raise RuntimeError("PUBLIC_CONTROLLED_TASK_TIMED_OUT")
        PHASE = kind + "_result_download"
        result = api("/tasks/" + task_id + "/result")
        if result.get("total_samples", 0) <= 0:
            raise RuntimeError("PUBLIC_CONTROLLED_CAPTURE_EMPTY")
        artifacts = api("/tasks/" + task_id + "/artifacts")["items"]
        raw = next(a for a in artifacts if a["kind"] == "RAW" and a["status"] == "ready")
        signed = api("/tasks/" + task_id + "/artifacts/" + str(raw["id"]) + "/download")
        url = urllib.parse.urljoin(ORIGIN, signed.get("url") or signed["download_url"])
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme + "://" + parsed.netloc != ORIGIN or not parsed.path.startswith("/objects/") or parsed.fragment:
            raise RuntimeError("PUBLIC_ARTIFACT_ORIGIN_REJECTED")
        # Identity encoding and a separate client: no session cookie reaches S3.
        request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(64 * 1024**2 + 1)
        if len(data) != raw["size"] or hashlib.sha256(data).hexdigest() != raw["sha256"]:
            raise RuntimeError("PUBLIC_RAW_SIZE_OR_HASH_MISMATCH")
        reports.append({"kind": kind, "task_id": task_id, "collection_status": "COLLECTED", "analysis_status": "SUCCESS",
                        "raw_size": len(data), "raw_sha256": raw["sha256"], "download_verified": True,
                        "source": "real controlled live process; no imported or synthetic folded data"})
    report = {"origin": ORIGIN, "tls_verified": True, "secure_session_cookie": True,
              "historical_result_readable": True, "historical_task_id": str(historical["id"]),
              "python_memory_live_attach_advertised": True, "tasks": reports}
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Runtime codes are fixed above; other exceptions may contain signed URLs.
        code = str(error) if isinstance(error, RuntimeError) and str(error).isupper() else "PUBLIC_RELEASE_CHECK_FAILED"
        print(json.dumps({"passed": False, "error_code": code, "phase": PHASE, "exception_type": type(error).__name__,
                          "http_status": error.code if isinstance(error, urllib.error.HTTPError) else None}))
        raise SystemExit(1) from None
