#!/usr/bin/env python3
"""把 Locust 的 CSV 和附加状态分布合成一份可复核的收据。

收据只记录实际测到的数字：没有服务端 CPU/内存采样时留 null 并写明原因，
不用客户端延迟去反推资源占用。百分位直接取自 Locust 统计，不二次拟合。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import sys
from typing import Any

PERCENTILE_COLUMNS = ("50%", "66%", "75%", "80%", "90%", "95%", "98%", "99%", "99.9%", "99.99%", "100%")


def _read_csv(path: str) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _num(row: dict[str, str], *names: str, default: float = 0.0) -> float:
    for name in names:
        if name in row and row[name] not in ("", None):
            try:
                return float(row[name])
            except ValueError:
                continue
    return default


def is_passing(receipt: dict[str, Any]) -> bool:
    gates = receipt["gates"]
    return all(gate["passed"] for gate in gates)


def build_receipt(stats_csv: str, failures_csv: str, extra_json: str, meta: dict[str, Any]) -> dict[str, Any]:
    stats = _read_csv(stats_csv)
    extra: dict[str, Any] = {}
    if extra_json and os.path.exists(extra_json):
        with open(extra_json, encoding="utf-8") as handle:
            extra = json.load(handle)

    endpoints: list[dict[str, Any]] = []
    total_requests = total_failures = 0
    for row in stats:
        name = row.get("Name", "")
        # Locust 的汇总行 Type 为空、Name 为 "Aggregated"；总量自己累加，避免重复计数
        if not name or not row.get("Type", ""):
            continue
        count = int(_num(row, "Request Count"))
        failures = int(_num(row, "Failure Count"))
        total_requests += count
        total_failures += failures
        rps = _num(row, "Requests/s")
        endpoints.append(
            {
                "name": name,
                "method": row.get("Type", "GET"),
                "requests": count,
                "failures": failures,
                "rps": round(rps, 3),
                "median_ms": round(_num(row, "Median Response Time"), 1),
                "avg_ms": round(_num(row, "Average Response Time"), 1),
                "min_ms": round(_num(row, "Min Response Time", "Min"), 1),
                "max_ms": round(_num(row, "Max Response Time", "Max"), 1),
                "p50_ms": round(_num(row, "50%"), 1),
                "p90_ms": round(_num(row, "90%"), 1),
                "p95_ms": round(_num(row, "95%"), 1),
                "p99_ms": round(_num(row, "99%"), 1),
                "avg_bytes": round(_num(row, "Average Content Size"), 1),
            }
        )

    failures = []
    if failures_csv and os.path.exists(failures_csv):
        for row in _read_csv(failures_csv):
            failures.append(
                {
                    "name": row.get("Name", ""),
                    "error": row.get("Error", ""),
                    "occurrences": int(_num(row, "Occurrences")),
                }
            )
    failures.sort(key=lambda item: item["occurrences"], reverse=True)

    status_counts = extra.get("status_counts") or {}
    server_errors = sum(count for bucket in status_counts.values() for code, count in bucket.items()
                        if code.startswith("5"))
    throttled = sum(count for bucket in status_counts.values() for code, count in bucket.items()
                    if code == "429")

    error_rate = (total_failures / total_requests) if total_requests else 0.0
    overall_p95 = max((endpoint["p95_ms"] for endpoint in endpoints), default=0.0)
    overall_p99 = max((endpoint["p99_ms"] for endpoint in endpoints), default=0.0)

    gates = [
        {"id": "no_5xx", "rule": "服务端 5xx 计数为 0", "actual": server_errors, "passed": server_errors == 0},
        {"id": "error_rate", "rule": "失败率 ≤ 1%", "actual": round(error_rate, 5), "passed": error_rate <= 0.01},
        {"id": "client_p95", "rule": "各端点 P95 ≤ 800ms（含公网 RTT）", "actual": overall_p95, "passed": overall_p95 <= 800},
        {"id": "client_p99", "rule": "各端点 P99 ≤ 2000ms（含公网 RTT）", "actual": overall_p99, "passed": overall_p99 <= 2000},
    ]

    receipt: dict[str, Any] = {
        "schema": "drop.loadtest.receipt/1",
        "generated_at": meta.get("generated_at"),
        "target": meta.get("target"),
        "profile": meta.get("profile"),
        "generator": {
            "host": platform.node(),
            "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "location": meta.get("generator_location", "unknown"),
        },
        "load_model": {
            "scenario": meta.get("scenario"),
            "sessions": meta.get("sessions"),
            "virtual_users": meta.get("virtual_users"),
            "duration_seconds": meta.get("duration_seconds"),
            "writes_enabled": False,
            "tls_verify": not meta.get("insecure"),
            "http_protocol": "HTTP/1.1 (Locust/requests)",
        },
        "workload": {
            "endpoints": endpoints,
            "top_failures": failures[:20],
            "status_counts": status_counts,
            "bytes_total": extra.get("bytes_total"),
            "throttled_429": throttled,
        },
        "totals": {
            "requests": total_requests,
            "failures": total_failures,
            "error_rate": round(error_rate, 5),
            "aggregate_rps": round(sum(endpoint["rps"] for endpoint in endpoints), 3),
            "p95_ms_worst_endpoint": overall_p95,
            "p99_ms_worst_endpoint": overall_p99,
        },
        "gates": gates,
        "verdict": "pass" if all(gate["passed"] for gate in gates) else "fail",
        "not_measured": meta.get("not_measured", []),
    }
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="合成压测收据")
    parser.add_argument("--stats", required=True)
    parser.add_argument("--failures", default="")
    parser.add_argument("--extra", default="")
    parser.add_argument("--meta", required=True, help="run.py 写出的运行元数据 JSON")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    with open(args.meta, encoding="utf-8") as handle:
        meta = json.load(handle)
    receipt = build_receipt(args.stats, args.failures, args.extra, meta)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(receipt, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"{receipt['verdict']}: {receipt['totals']['requests']} requests, "
          f"{receipt['totals']['error_rate']:.2%} errors, "
          f"worst p95 {receipt['totals']['p95_ms_worst_endpoint']}ms -> {args.out}")
    return 0 if receipt["verdict"] == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
