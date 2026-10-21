"""向受控演示发出真实HTTP负载；返回客户端观察结果，不直接写服务端遥测记录。"""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def get_json(url, timeout=5):
    try:
        response = urlopen(url, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("RESPONSE_LIMIT")
        return response.status, json.loads(raw)


def run_load(gateway, scenario, count=6, duration=0):
    """次数/时长双重有界；长期模式可覆盖实际采集窗口，但最多1000请求和120秒。"""
    if scenario not in {"normal", "slow_query", "cpu", "upstream_error", "timeout"} or not 1 <= count <= 1000 or not 0 <= duration <= 120:
        raise ValueError("LOAD_BUDGET_REJECTED")
    started = timestamp()
    tick = time.monotonic()
    rows = []
    while len(rows) < count and (not duration or time.monotonic()-tick < duration):
        status, result = get_json(gateway.rstrip("/") + "/demo/request?" + urlencode({"scenario": scenario}))
        rows.append({"status_code": status, "duration_ms": result.get("duration_ms"), "trace_id": result.get("trace_id")})
        if duration:
            time.sleep(.02)
    return {"provenance": "synthetic_controlled_live_http_demo", "scenario": scenario,
            "start": started, "end": timestamp(), "completed_requests": len(rows),
            "client_responses": rows, "elapsed_seconds": round(time.monotonic()-tick, 3)}


def signals(origin, service, signal, start, end, limit=30):
    status, body = get_json(origin.rstrip("/") + "/v1/signals?" + urlencode({"service": service, "signal": signal, "start": start, "end": end, "limit": limit}))
    if status != 200:
        raise ValueError("SIGNAL_QUERY_REJECTED")
    return body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="http://observability-gateway:8130")
    parser.add_argument("--scenario", choices=("normal", "slow_query", "cpu", "upstream_error", "timeout"), default="normal")
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--duration-seconds", type=float, default=0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    parsed = urlsplit(args.gateway)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        parser.error("gateway must be a credential-free internal http origin")
    result = run_load(args.gateway, args.scenario, args.count, args.duration_seconds)
    content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    print(content, end="")


if __name__ == "__main__":
    main()
