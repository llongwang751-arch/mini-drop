"""Bounded real HTTP traffic for an already deployed original office backend.

Uses an isolated acceptance account, not direct repository calls or DB writes.
Reports contain timings/statuses only. This is integration traffic, not a
capacity benchmark or fault injection.
"""
import argparse
import json
import time
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://172.17.0.1:18090")
    parser.add_argument("--account-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--requests", type=int, default=180)
    parser.add_argument("--interval", type=float, default=1.5)
    args = parser.parse_args()
    if not 1 <= args.requests <= 300 or args.interval < 1:
        parser.error("use 1..300 requests, at least 1 second apart")
    account = json.loads(Path(args.account_file).read_text())
    def post(path, payload, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(args.base + path, headers=headers, data=json.dumps(payload).encode())
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.load(response)
    _, login = post("/api/auth/login", account)
    token = login["access_token"]
    rows = []
    for index in range(args.requests):
        started = time.monotonic()
        try:
            status, result = post("/api/chat", {"message": "员工年假申请须提前多久提交？", "use_rag": True}, token)
            valid = "三个工作日" in result.get("answer", "") and "模拟 LLM" not in result.get("answer", "")
            row = {"index": index, "status": status, "answer_valid": valid, "trace_id": result.get("trace_id")}
        except Exception as exc:
            row = {"index": index, "status": getattr(exc, "code", 0), "error_type": type(exc).__name__}
        row["duration_ms"] = (time.monotonic() - started) * 1000
        rows.append(row)
        Path(args.output).write_text(json.dumps({"requests": rows, "finished": index == args.requests - 1}, indent=2))
        time.sleep(max(0, args.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
