"""在新建、网络隔离的 PostgreSQL 夹具中运行基准二进制。

只删除本次创建的随机容器和卷，不接受现有 DSN，也不读取部署密钥；
人工审阅后再将输出归档为证据。数据为合成记录，不包含鉴权材料。
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import uuid
import re


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.fixtures).resolve()
    if not (root / "platform-benchmark").is_file():
        raise FileNotFoundError("Compile tools/cmd/platform-benchmark for Linux first")
    # CPU/内存上限不能约束百万行 seed 的磁盘流量；拒绝在繁忙 Linux 宿主运行，
    # 并对本夹具设置块设备吞吐上限，使收集证据时现有部署仍可响应。
    io_limits = []
    io_device = None
    pressure_file = Path("/proc/pressure/io")
    if pressure_file.exists():
        pressure = pressure_file.read_text(encoding="utf-8")
        match = re.search(r"^some avg10=([0-9.]+)", pressure, re.M)
        if not match or float(match[1]) > 10:
            raise RuntimeError("Host I/O pressure is elevated; run the isolated benchmark when the host is idle")
        source = subprocess.run(["findmnt", "-n", "-o", "SOURCE", "--target", str(root)], capture_output=True, text=True, check=True).stdout.strip().split("[", 1)[0]
        if not Path(source).is_block_device():
            raise RuntimeError("Cannot identify the fixture block device for mandatory I/O throttling")
        parent = subprocess.run(["lsblk", "-n", "-d", "-o", "PKNAME", source], capture_output=True, text=True, check=True).stdout.strip()
        if parent:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", parent):
                raise RuntimeError("Ambiguous backing device; refusing an unbounded seed")
            source = "/dev/" + parent
        device_type = subprocess.run(["lsblk", "-n", "-d", "-o", "TYPE", source], capture_output=True, text=True, check=True).stdout.strip()
        if device_type != "disk" or not Path(source).is_block_device():
            raise RuntimeError("A simple physical backing device is required for verified fixture I/O limits")
        io_device = source
        io_limits = ["--device-read-bps", source + ":4mb", "--device-write-bps", source + ":2mb", "--blkio-weight", "100"]
    token = uuid.uuid4().hex[:12]
    name = "drop-million-bench-" + token
    volume = name + "-data"
    password = secrets.token_urlsafe(32)
    env = dict(os.environ, POSTGRES_PASSWORD=password,
               DROP_BENCH_DATABASE_URL=f"postgres://postgres:{password}@127.0.0.1:5432/drop_perf_fixture?sslmode=disable")
    def run(argv, check=True, timeout=1200):
        result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout)
        if password in result.stdout + result.stderr:
            raise RuntimeError("Suppressed unexpected secret-bearing tool output")
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result
    started = time.monotonic()
    try:
        run(["docker", "volume", "create", "--label", "drop.test=isolated-million-task-benchmark", volume])
        run(["docker", "run", "-d", "--name", name, "--network", "none", "--memory", "256m", "--memory-swap", "384m", "--cpus", "0.5", "--pids-limit", "96", *io_limits, "-v", volume + ":/var/lib/postgresql/data", "--mount", f"type=bind,src={root},dst=/fixtures,readonly", "-e", "POSTGRES_PASSWORD", "-e", "POSTGRES_DB=drop_perf_fixture", "postgres:17.11-alpine", "-c", "shared_buffers=32MB", "-c", "work_mem=2MB", "-c", "maintenance_work_mem=32MB", "-c", "max_connections=30", "-c", "jit=off"])
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if run(["docker", "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres"], check=False).returncode == 0:
                break
            time.sleep(.5)
        else:
            raise RuntimeError("Fixture PostgreSQL did not become ready")
        result = run(["docker", "exec", "-e", "DROP_BENCH_DATABASE_URL", "-e", "DROP_BENCH_ALLOW_DISPOSABLE=1", "-e", "DROP_BENCH_OUTPUT=/tmp/platform-benchmark.json", "-e", "GOMAXPROCS=1", "-e", "GOMEMLIMIT=96MiB", name, "/fixtures/platform-benchmark"], check=False)
        run(["docker", "cp", name + ":/tmp/platform-benchmark.json", args.output])
        report = json.loads(Path(args.output).read_text(encoding="utf-8"))
        report["environment"].update({"fixture_container_limit": {"memory_bytes": 256 << 20, "memory_and_swap_bytes": 384 << 20, "cpus": .5, "pids": 96, "io_device": io_device, "read_bytes_per_second": (4 << 20) if io_device else None, "write_bytes_per_second": (2 << 20) if io_device else None}, "postgres_settings": {"shared_buffers": "32MB", "work_mem": "2MB", "maintenance_work_mem": "32MB", "max_connections": 30, "jit": "off"}, "runner_elapsed_seconds": round(time.monotonic() - started, 3)})
        report["environment"]["docker_stats_at_finish"] = json.loads(run(["docker", "stats", "--no-stream", "--format", "{{json .}}", name]).stdout)
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if result.returncode or not report["passed"]:
            raise RuntimeError(result.stdout + result.stderr)
        print(json.dumps({"passed": True, "report": args.output, "tasks": report["seed"]["tasks"], "elapsed_seconds": report["environment"]["runner_elapsed_seconds"]}))
    finally:
        run(["docker", "rm", "-f", name], check=False)
        run(["docker", "volume", "rm", volume], check=False)


if __name__ == "__main__":
    main()
