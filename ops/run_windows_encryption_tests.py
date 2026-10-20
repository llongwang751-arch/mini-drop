"""创建新的私有便携 PostgreSQL 集群，仅运行迁移的真实数据库部分。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api/tools"))
from run_windows_benchmark import Job  # noqa: E402


def main():
    if os.name != "nt":
        raise RuntimeError("This disposable runner requires Windows")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default=str(ROOT / "ops/evidence/encryption-postgres.json")
    )
    args = parser.parse_args()
    binaries = ROOT / ".local/supplychain/portable-pg17/pgsql/bin"
    if not (binaries / "postgres.exe").is_file():
        raise RuntimeError("Cached official PostgreSQL binaries are required")
    base = (ROOT / ".local/supplychain").resolve()
    cluster = (base / ("encryption-cluster-" + uuid.uuid4().hex[:12])).resolve()
    if cluster.parent != base or not base.is_relative_to((ROOT / ".local").resolve()):
        raise RuntimeError("Disposable cluster path escaped the workspace")
    cluster.mkdir()
    secret = secrets.token_urlsafe(32)
    password_file = cluster / "initdb.password"
    password_file.write_text(secret, encoding="utf8")
    env = dict(
        os.environ, PGPASSWORD=secret, PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1"
    )
    job, postgres = Job(), None
    started = time.monotonic()

    def spawn(argv, **kwargs):
        process = subprocess.Popen(
            [str(value) for value in argv],
            env=env,
            stdin=subprocess.DEVNULL,
            cwd=ROOT / "ops",
            creationflags=subprocess.CREATE_NO_WINDOW,
            **kwargs,
        )
        job.assign(process)
        return process

    def run(argv, *, check=True, timeout=60):
        process = spawn(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise RuntimeError("Disposable fixture command timed out") from None
        if secret.encode() in stdout + stderr:
            raise RuntimeError("Suppressed secret-bearing fixture output")
        if check and process.returncode:
            raise RuntimeError("Disposable fixture command failed")
        return process.returncode, stdout + stderr

    try:
        run(
            [
                binaries / "initdb.exe",
                "-D",
                cluster / "data",
                "-U",
                "postgres",
                "--auth=scram-sha-256",
                "--encoding=UTF8",
                "--locale=C",
                "--pwfile",
                password_file,
            ]
        )
        password_file.unlink()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with (cluster / "postgres.log").open("wb") as log:
            postgres = spawn(
                [
                    binaries / "postgres.exe",
                    "-D",
                    cluster / "data",
                    "-h",
                    "127.0.0.1",
                    "-p",
                    port,
                    "-c",
                    "shared_buffers=32MB",
                    "-c",
                    "work_mem=2MB",
                    "-c",
                    "maintenance_work_mem=32MB",
                    "-c",
                    "max_connections=12",
                    "-c",
                    "jit=off",
                    "-c",
                    "max_parallel_workers_per_gather=0",
                ],
                stdout=log,
                stderr=log,
            )
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if postgres.poll() is not None:
                raise RuntimeError("Disposable PostgreSQL exited before readiness")
            code, _ = run(
                [
                    binaries / "pg_isready.exe",
                    "-h",
                    "127.0.0.1",
                    "-p",
                    port,
                    "-U",
                    "postgres",
                ],
                check=False,
                timeout=5,
            )
            if code == 0:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("Disposable PostgreSQL readiness timed out")
        run(
            [
                binaries / "createdb.exe",
                "-h",
                "127.0.0.1",
                "-p",
                port,
                "-U",
                "postgres",
                "drop_encryption_fixture",
            ]
        )
        env.update(
            ENCRYPTION_TEST_DSN=f"postgresql://postgres:{secret}@127.0.0.1:{port}/drop_encryption_fixture?sslmode=disable",
            ENCRYPTION_TEST_ALLOW_DISPOSABLE="1",
        )
        # 测试与 PostgreSQL 共用同一个 512 MiB、单逻辑 CPU 的 Windows Job 资源边界。
        code, content = run(
            [sys.executable, "-m", "unittest", "test_encryption_postgres", "-v"],
            check=False,
            timeout=90,
        )
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        log_path = output.with_suffix(".log")
        log_path.write_bytes(content)
        text = content.decode("utf8", errors="replace")
        count = re.search(r"Ran (\d+) tests in ([\d.]+)s", text)
        report = {
            "passed": code == 0
            and text.rstrip().endswith("OK")
            and "skipped" not in text,
            "provenance": "Actual Windows portable PostgreSQL17.11 fresh loopback cluster; all five production migrations; S3 remains synthetic Fake storage. No Docker, remote host or production data.",
            "tests_run": int(count[1]) if count else None,
            "test_duration_seconds": float(count[2]) if count else None,
            "skipped": 0,
            "job_object": {
                "memory_limit_bytes": 512 << 20,
                "logical_cpus_allowed": 1,
                "peak_committed_memory_bytes": job.peak(),
            },
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "log_file": log_path.name,
            "log_sha256": hashlib.sha256(content).hexdigest(),
            "source_sha256": hashlib.sha256(
                (ROOT / "ops/encrypt_existing.py").read_bytes()
            ).hexdigest(),
            "test_sha256": hashlib.sha256(
                (ROOT / "ops/test_encryption_postgres.py").read_bytes()
            ).hexdigest(),
            "migration_sha256": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted((ROOT / "db/migrations").glob("*.sql"))
            },
        }
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
        print(
            json.dumps(
                {
                    key: report[key]
                    for key in (
                        "passed",
                        "tests_run",
                        "skipped",
                        "test_duration_seconds",
                        "job_object",
                    )
                }
            )
        )
        return 0 if report["passed"] else 1
    finally:
        try:
            if postgres and postgres.poll() is None:
                run(
                    [
                        binaries / "pg_ctl.exe",
                        "-D",
                        cluster / "data",
                        "-m",
                        "fast",
                        "-w",
                        "-t",
                        "15",
                        "stop",
                    ],
                    check=False,
                    timeout=20,
                )
        finally:
            job.close()
        if postgres:
            postgres.wait(timeout=20)
        target = cluster.resolve()
        if (
            target.parent != base
            or not target.name.startswith("encryption-cluster-")
            or not target.is_relative_to((ROOT / ".local").resolve())
        ):
            raise RuntimeError("Refusing recursive removal outside this fixture")
        shutil.rmtree(target)


if __name__ == "__main__":
    raise SystemExit(main())
