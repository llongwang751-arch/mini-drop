"""Portable PostgreSQL benchmark, without Docker or a Windows service.

Only a fresh directory below this repository's ignored .local/supplychain is
created/removed. PostgreSQL and the benchmark share a Windows Job Object with a
512 MiB committed-memory limit and one allowed logical CPU. All DB connections
use a random loopback port and a per-run secret that is never printed.
"""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]


class BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]


class IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", BasicLimits), ("io", IOCounters), ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]


class Job:
    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.kernel.QueryInformationJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.GetCurrentProcess.restype = wintypes.HANDLE
        self.kernel.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        current, system = ctypes.c_size_t(), ctypes.c_size_t()
        if not self.kernel.GetProcessAffinityMask(self.kernel.GetCurrentProcess(), ctypes.byref(current), ctypes.byref(system)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.affinity = current.value & -current.value
        limits = ExtendedLimits()
        limits.basic.flags = 0x10 | 0x200 | 0x2000  # affinity, job memory, kill on close
        limits.basic.affinity = self.affinity
        limits.job_memory = 512 << 20
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(process._handle)):
            process.terminate()
            raise ctypes.WinError(ctypes.get_last_error())

    def peak(self):
        result = ExtendedLimits()
        if not self.kernel.QueryInformationJobObject(self.handle, 9, ctypes.byref(result), ctypes.sizeof(result), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return result.peak_job_memory

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def main():
    if os.name != "nt":
        raise RuntimeError("This runner requires Windows; Linux uses the Docker-isolated runner")
    parser = argparse.ArgumentParser()
    parser.add_argument("--bin-directory", default=str(ROOT / ".local/supplychain/portable-pg17/pgsql/bin"))
    parser.add_argument("--benchmark", default=str(ROOT / ".local/supplychain/platform-benchmark.exe"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--migration-only", action="store_true")
    parser.add_argument("--migration-loader")
    parser.add_argument("--integration-tests-directory")
    args = parser.parse_args()
    if args.migration_only and not args.migration_loader:
        raise ValueError("--migration-only requires the compiled API migration loader")
    binaries = Path(args.bin_directory).resolve()
    if not (binaries / "postgres.exe").is_file():
        raise FileNotFoundError("Official PostgreSQL portable binaries are required")
    base = (ROOT / ".local/supplychain").resolve()
    cluster = (base / ("bench-cluster-" + uuid.uuid4().hex[:12])).resolve()
    if not cluster.is_relative_to(base) or not base.is_relative_to((ROOT / ".local").resolve()):
        raise RuntimeError("Fixture directory escaped the ignored project workspace")
    cluster.mkdir()
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_urlsafe(32)
    password_file = cluster / "initdb.password"
    password_file.write_text(secret, encoding="utf-8")
    env = dict(os.environ, PGPASSWORD=secret, GOMAXPROCS="1", GOMEMLIMIT="96MiB")
    def run(argv, check=True, timeout=120):
        result = subprocess.run([str(x) for x in argv], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
        if secret in result.stdout + result.stderr:
            raise RuntimeError("Suppressed unexpected secret-bearing tool output")
        if check and result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result
    job = Job()
    process = None
    started = time.monotonic()
    raw_log = base / (output.stem + ".stdout.txt")
    postgres_log = base / (output.stem + ".postgres.txt")
    try:
        data = cluster / "data"
        run([binaries / "initdb.exe", "-D", data, "-U", "postgres", "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C", "--pwfile", password_file])
        password_file.unlink()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with postgres_log.open("wb") as log:
            process = subprocess.Popen([str(binaries / "postgres.exe"), "-D", str(data), "-h", "127.0.0.1", "-p", str(port), "-c", "shared_buffers=32MB", "-c", "work_mem=2MB", "-c", "maintenance_work_mem=32MB", "-c", "max_connections=30", "-c", "max_parallel_workers_per_gather=0", "-c", "jit=off"], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            job.assign(process)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Disposable PostgreSQL process exited before readiness; inspect its local log")
            if run([binaries / "pg_isready.exe", "-h", "127.0.0.1", "-p", str(port), "-U", "postgres"], check=False).returncode == 0:
                break
            time.sleep(.25)
        else:
            raise RuntimeError("Disposable loopback PostgreSQL did not become ready")
        run([binaries / "createdb.exe", "-h", "127.0.0.1", "-p", str(port), "-U", "postgres", "drop_perf_fixture"])
        env.update(DROP_BENCH_DATABASE_URL=f"postgres://postgres:{secret}@127.0.0.1:{port}/drop_perf_fixture?sslmode=disable", DROP_BENCH_ALLOW_DISPOSABLE="1", DROP_BENCH_FIXTURES=str(ROOT), DROP_BENCH_OUTPUT=str(output))
        if args.integration_tests_directory:
            directory = Path(args.integration_tests_directory).resolve()
            env.update(TEST_DATABASE_URL=env["DROP_BENCH_DATABASE_URL"], TEST_MIGRATION_FILE=str(ROOT / "db/migrations/001_initial.sql"), TEST_MIGRATIONS_DIR=str(ROOT / "db/migrations"), TEST_TASK_KINDS_FILE=str(ROOT / "contracts/task-kinds.json"))
            results = []
            for package in ["repository", "service", "httpapi"]:
                executable = directory / (package + ".test.exe")
                tick = time.monotonic()
                child = subprocess.Popen([str(executable), "-test.v", "-test.timeout=180s"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
                job.assign(child)
                stdout, stderr = child.communicate(timeout=200)
                if secret.encode() in stdout + stderr:
                    raise RuntimeError("Suppressed unexpected secret-bearing integration output")
                content = (stdout + stderr).decode("utf8", errors="replace")
                result = {"package": package, "passed": child.returncode == 0 and "--- SKIP:" not in content, "seconds": round(time.monotonic()-tick,3), "skipped": len(re.findall(r"--- SKIP:", content)), "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(), "output": content}
                results.append(result)
                report = {"passed": len(results) == 3 and all(item["passed"] for item in results), "environment": "Native Windows portable PostgreSQL17.11; new private loopback cluster; no service, Docker or production data", "results": results, "migrations_sha256": {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in sorted((ROOT / "db/migrations").glob("*.sql"))}, "job_object": {"memory_limit_bytes": 512 << 20, "peak_committed_memory_bytes": job.peak(), "logical_cpus_allowed": 1}, "elapsed_seconds": round(time.monotonic()-started,3)}
                output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf8")
                print(json.dumps({"package": package, "passed": result["passed"], "skipped": result["skipped"], "seconds": result["seconds"]}), flush=True)
                if not result["passed"]:
                    raise RuntimeError("Real PostgreSQL integration did not pass; inspect the local report")
            return
        if args.migration_only:
            env.update(DATABASE_URL=env["DROP_BENCH_DATABASE_URL"], MIGRATIONS_DIR=str(ROOT / "db/migrations"))
            loader = Path(args.migration_loader).resolve()
            outputs = []
            for _ in range(2):
                child = subprocess.Popen([str(loader), "migrate"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
                job.assign(child)
                stdout, stderr = child.communicate(timeout=90)
                if secret.encode() in stdout + stderr:
                    raise RuntimeError("Suppressed unexpected secret-bearing migration output")
                if child.returncode:
                    raise RuntimeError("Real API migration loader failed")
                outputs.append((stdout + stderr).decode("utf-8"))
            probe = run([binaries / "psql.exe", "-h", "127.0.0.1", "-p", str(port), "-U", "postgres", "-d", "drop_perf_fixture", "-X", "-q", "-At", "-c", "SELECT json_build_object('schema_version',(SELECT max(version) FROM schema_migrations),'index_valid',(SELECT indisvalid AND indisready FROM pg_index WHERE indexrelid='idx_tasks_active_created'::regclass),'index_definition',pg_get_indexdef('idx_tasks_active_created'::regclass),'tasks',(SELECT count(*) FROM tasks));"])
            checks = json.loads(probe.stdout)
            latest_version = max(int(file.name.split("_")[0]) for file in (ROOT / "db/migrations").glob("*.sql"))
            if checks["schema_version"] != latest_version or checks["index_valid"] is not True or checks["tasks"] != 0:
                raise RuntimeError("Real migration loader did not produce the expected idempotent schema")
            report = {"passed": True, "environment": "Native Windows portable PostgreSQL17.11; new private loopback cluster; no service, Docker or production data", "real_api_migration_loader_runs": 2, "loader_sha256": hashlib.sha256(loader.read_bytes()).hexdigest(), "loader_output": outputs, "checks": checks, "job_object": {"memory_limit_bytes": 512 << 20, "peak_committed_memory_bytes": job.peak(), "logical_cpus_allowed": 1}, "migration_sha256": hashlib.sha256((ROOT / "db/migrations/004_task_list_index.sql").read_bytes()).hexdigest(), "elapsed_seconds": round(time.monotonic() - started, 3)}
            output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"passed": True, "migration_loader_runs": 2, "schema_version": latest_version, "output": str(output)}))
            return
        with raw_log.open("wb") as log:
            bench = subprocess.Popen([str(Path(args.benchmark).resolve())], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            job.assign(bench)
            code = bench.wait(timeout=1200)
        report = json.loads(output.read_text(encoding="utf-8"))
        report["environment"].update({"portable_postgres": True, "windows_service_registered": False, "listen": "127.0.0.1 on random ephemeral port", "job_object": {"job_memory_limit_bytes": 512 << 20, "peak_committed_job_memory_bytes": job.peak(), "cpu_affinity_mask": job.affinity, "logical_cpus_allowed": 1, "kill_on_job_close": True}, "postgres_settings": {"shared_buffers": "32MB", "work_mem": "2MB", "maintenance_work_mem": "32MB", "max_connections": 30, "max_parallel_workers_per_gather": 0, "jit": "off"}, "runner_elapsed_seconds": round(time.monotonic() - started, 3), "benchmark_executable_sha256": hashlib.sha256(Path(args.benchmark).read_bytes()).hexdigest(), "benchmark_source_sha256": hashlib.sha256((ROOT / "api/tools/cmd/platform-benchmark/main.go").read_bytes()).hexdigest(), "limitations": "Windows native portable PostgreSQL, shared one-CPU/512MiB job. This is not a Linux/container, production-server, profiler, S3, TLS/Nginx or Internet capacity benchmark."})
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if code or not report["passed"]:
            raise RuntimeError("Benchmark did not pass; inspect its preserved JSON report and local log")
        print(json.dumps({"passed": True, "report": str(output), "tasks": report["seed"]["tasks"], "elapsed_seconds": report["environment"]["runner_elapsed_seconds"], "peak_job_memory_bytes": job.peak()}))
    finally:
        if process and process.poll() is None:
            run([binaries / "pg_ctl.exe", "-D", cluster / "data", "-m", "fast", "-w", "-t", "20", "stop"], check=False, timeout=30)
        job.close()  # kills only this runner's assigned processes if still alive
        if process:
            process.wait(timeout=30)
        # Confirm absolute containment again immediately before recursive removal.
        resolved = cluster.resolve()
        if resolved.parent != base or not resolved.is_relative_to((ROOT / ".local").resolve()) or not resolved.name.startswith("bench-cluster-"):
            raise RuntimeError("Refusing to remove a directory outside this run's fixture")
        shutil.rmtree(resolved)


if __name__ == "__main__":
    main()
