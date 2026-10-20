"""Run static Linux Go test binaries inside a disposable isolated PostgreSQL container."""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import secrets
import subprocess
import time
import uuid

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = pathlib.Path(args.fixtures).resolve()
    password = secrets.token_urlsafe(32)
    container = "drop-api-full-test-" + uuid.uuid4().hex[:10]
    env = dict(os.environ, POSTGRES_PASSWORD=password)
    results = []
    def run(argv: list[str], *, extra_env=None, check=True):
        result = subprocess.run(argv, text=True, capture_output=True, env=extra_env or env, timeout=300)
        if password in result.stdout + result.stderr:
            raise AssertionError("integration secret appeared in output")
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result
    try:
        run(["docker", "run", "-d", "--name", container, "--network", "none", "--memory", "512m", "--cpus", "0.75", "--tmpfs", "/var/lib/postgresql/data:rw,noexec,nosuid,size=256m", "--mount", f"type=bind,src={root},dst=/fixtures,readonly", "-e", "POSTGRES_PASSWORD", "postgres:17.11-alpine"])
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            ready = run(["docker", "exec", container, "pg_isready", "-h", "127.0.0.1", "-U", "postgres"], check=False)
            if ready.returncode == 0:
                break
            time.sleep(.25)
        else:
            raise RuntimeError("isolated PostgreSQL TCP listener did not start")
        test_env = dict(env, TEST_DATABASE_URL=f"postgres://postgres:{password}@127.0.0.1:5432/postgres?sslmode=disable")
        for package in ["repository", "service", "httpapi"]:
            start = time.monotonic()
            result = run(["docker", "exec", "-e", "TEST_DATABASE_URL", "-e", "TEST_MIGRATION_FILE=/fixtures/db/migrations/001_initial.sql", "-e", "TEST_MIGRATIONS_DIR=/fixtures/db/migrations", "-e", "TEST_TASK_KINDS_FILE=/fixtures/contracts/task-kinds.json", container, f"/fixtures/tests/{package}.test", "-test.v", "-test.timeout=180s"], extra_env=test_env, check=False)
            print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, end="")
            results.append({"package": package, "passed": result.returncode == 0, "seconds": round(time.monotonic() - start, 3), "output": result.stdout + result.stderr})
            if result.returncode:
                raise RuntimeError(f"integration package {package} failed")
    finally:
        run(["docker", "rm", "-f", container], check=False)
        pathlib.Path(args.output).write_text(json.dumps({"environment": "PostgreSQL17.11 isolated network=none, 512MiB, 0.75CPU, disposable tmpfs; no production access", "results": results, "passed": len(results) == 3 and all(r["passed"] for r in results)}, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
