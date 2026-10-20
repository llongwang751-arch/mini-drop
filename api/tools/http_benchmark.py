"""小规模鉴权只读基线；凭据从文件读取且不输出，不将请求耗时冒充完整生产性能结论。"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import http.cookies
import json
import math
import pathlib
import statistics
import time
import urllib.error
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1")
    parser.add_argument("--secret-dir", default="/opt/drop/deploy/secrets")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    base = args.base_url.rstrip("/") + "/api/v1"
    secrets = pathlib.Path(args.secret_dir)
    payload = json.dumps({
        "username": (secrets / "admin_username").read_text().strip(),
        "password": (secrets / "admin_password").read_text().strip(),
    }).encode()
    login = urllib.request.Request(base + "/auth/login", data=payload,
                                   headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(login, timeout=15) as response:
        session = http.cookies.SimpleCookie(response.headers["Set-Cookie"])
        token = session["drop_session"].value
        csrf = json.load(response)["data"]["csrf_token"]
    headers = {"Cookie": "drop_session=" + token}

    def get(path: str) -> dict:
        request = urllib.request.Request(base + path, headers=headers)
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)

    before = get("/overview")["data"]["tasks_total"]
    endpoints = ["/overview", "/tasks?page=1&page_size=20"]

    def measure(index: int) -> dict:
        endpoint = endpoints[index % len(endpoints)]
        start = time.perf_counter()
        code, error = 200, None
        try:
            data = get(endpoint)
            if data.get("error") is not None:
                error = data["error"]["code"]
        except urllib.error.HTTPError as exc:
            code, error = exc.code, "HTTP_ERROR"
        except Exception:
            code, error = 0, "REQUEST_FAILED"
        return {"endpoint": endpoint, "status": code, "error": error,
                "latency_ms": (time.perf_counter() - start) * 1000}

    started = dt.datetime.now(dt.timezone.utc).isoformat()
    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        samples = list(pool.map(measure, range(100)))
    elapsed = time.perf_counter() - start
    after = get("/overview")["data"]["tasks_total"]

    def summarize(items: list[dict]) -> dict:
        values = sorted(item["latency_ms"] for item in items)
        errors = sum(item["status"] != 200 or item["error"] is not None for item in items)
        percentile = lambda p: round(values[max(0, math.ceil(len(values) * p / 100) - 1)], 3)
        return {"requests": len(items), "errors": errors,
                "error_rate_percent": errors / len(items) * 100,
                "mean_ms": round(statistics.mean(values), 3),
                "p50_ms": percentile(50), "p95_ms": percentile(95),
                "p99_ms": percentile(99), "max_ms": round(max(values), 3)}

    report = {"started_at": started, "base_url": args.base_url, "concurrency": 10,
              "elapsed_seconds": round(elapsed, 3), "requests_per_second": round(100 / elapsed, 3),
              "summary": summarize(samples),
              "endpoints": {path: summarize([s for s in samples if s["endpoint"] == path])
                            for path in endpoints},
              "task_count_before": before, "task_count_after": after,
              "note": "100 measured authenticated reads through local Nginx; one login/logout; real E2E data may change concurrently."}
    pathlib.Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    logout = urllib.request.Request(base + "/auth/logout", data=b"{}",
                                    headers={**headers, "X-CSRF-Token": csrf,
                                             "Content-Type": "application/json"})
    with urllib.request.urlopen(logout, timeout=15) as response:
        response.read()


if __name__ == "__main__":
    main()
