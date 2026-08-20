"""Generate controlled gRPC traffic against the OTel Demo ad service."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import statistics
import threading
import time

import grpc

from otel_ad_cpu_experiment import (
    DEFAULT_OTEL_ROOT,
    ROOT,
    compile_proto,
    docker_host_port,
    host_pid,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load the OTel Demo ad gRPC API")
    parser.add_argument("--otel-root", type=Path, default=DEFAULT_OTEL_ROOT)
    parser.add_argument("--container", default="ad")
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    demo_pb2, demo_pb2_grpc = compile_proto(
        args.otel_root.resolve(), ROOT / "tmp" / "otel_pb"
    )
    target = f"127.0.0.1:{docker_host_port(args.container, 9555)}"
    deadline = time.monotonic() + args.duration
    lock = threading.Lock()
    requests = 0
    errors = 0
    latencies: list[float] = []

    def worker() -> None:
        nonlocal requests, errors
        channel = grpc.insecure_channel(target)
        stub = demo_pb2_grpc.AdServiceStub(channel)
        request = demo_pb2.AdRequest(context_keys=["binoculars", "telescopes"])
        local_requests = 0
        local_errors = 0
        local_latencies: list[float] = []
        try:
            while time.monotonic() < deadline:
                started = time.perf_counter()
                try:
                    stub.GetAds(request, timeout=5)
                    local_requests += 1
                    local_latencies.append((time.perf_counter() - started) * 1000)
                except grpc.RpcError:
                    local_errors += 1
        finally:
            channel.close()
            with lock:
                requests += local_requests
                errors += local_errors
                latencies.extend(local_latencies)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(worker) for _ in range(args.workers)]
        for future in futures:
            future.result()

    sorted_latencies = sorted(latencies)
    p95_index = max(0, min(len(sorted_latencies) - 1, int(len(sorted_latencies) * 0.95)))
    result = {
        "target": target,
        "container": args.container,
        "host_pid": host_pid(args.container),
        "duration_seconds": args.duration,
        "workers": args.workers,
        "requests": requests,
        "errors": errors,
        "request_rate_per_second": round(requests / args.duration, 3),
        "latency_ms_mean": round(statistics.fmean(latencies), 3) if latencies else None,
        "latency_ms_p95": (
            round(sorted_latencies[p95_index], 3) if sorted_latencies else None
        ),
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(args.output.resolve())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
