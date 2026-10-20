import hashlib
import json
import os
import re
import shutil
import signal
import tempfile
import threading
import time
from pathlib import Path

from . import __version__, telemetry
from .config import Config
from .disk import WorkspaceBudget
from .errors import AnalysisError, LeaseLost
from .folded import Limits
from .isolate import analyze_isolated
from .partial import build_partial
from .pipeline import build_outputs, json_bytes
from .registry import get_pipeline
from .repository import Job, Repository
from .result import make_result
from .runtime import emit, install_logging
from .storage import ObjectStore


def log(event: str, *, job: Job | None = None, level: str = "info", **fields):
    stages = {
        "worker_started": "startup",
        "worker_stopped": "shutdown",
        "analysis_started": "analysis",
        "analysis_succeeded": "analysis",
        "analysis_failed": "analysis",
        "analysis_lease_lost": "recovery",
        "worker_dependency_unavailable": "dependency",
        "late_failure_discarded": "recovery",
        "analysis_failure_write_unavailable": "recovery",
    }
    stage = stages.get(event)
    if stage is None:
        event, stage = "invalid_log_event", "logging"
    identifiers = {}
    if job:
        identifiers.update(
            task_id=str(job.task_id),
            attempt_id=str(job.attempt_id),
            analysis_job_id=str(job.id),
            analysis_attempt=job.attempt,
            request_id=job.params.get("request_id", ""),
            agent_id=job.params.get("agent_id", ""),
        )
    fields.pop("effective_config", None)
    emit(
        "drop-analyzer",
        __version__,
        event,
        level=level,
        stage=stage,
        **identifiers,
        **fields,
    )


def cleanup_stale_workdirs(root_path: str, *, max_age_seconds: float = 24 * 3600):
    """Remove only old directories created by this worker's tempfile prefix."""
    root = Path(root_path).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    cutoff = time.time() - max_age_seconds
    for candidate in root.iterdir():
        if candidate.is_symlink() or not re.fullmatch(
            r"job-\d+-[a-z0-9_]+", candidate.name
        ):
            continue
        resolved = candidate.resolve()
        if (
            resolved.parent == root
            and resolved.is_dir()
            and resolved.stat().st_mtime < cutoff
        ):
            shutil.rmtree(resolved)


def cleanup_indexes(
    root_path: str, *, max_age_seconds=24 * 3600, max_bytes=512 * 1024 * 1024
):
    root = Path(root_path).resolve() / "indexes"
    if not root.is_dir() or root.is_symlink():
        return
    entries = []
    for candidate in root.iterdir():
        if (
            not candidate.is_symlink()
            and candidate.is_file()
            and re.fullmatch(r"hprof-[a-f0-9]{64}\.sqlite3", candidate.name)
            and candidate.resolve().parent == root
        ):
            entries.append(
                (candidate.stat().st_mtime, candidate.stat().st_size, candidate)
            )
    remaining = sum(size for _, size, _ in entries)
    for modified, size, candidate in sorted(entries):
        if modified < time.time() - max_age_seconds or remaining > max_bytes:
            # This scavenger runs between jobs only. SQLite sidecars belong to
            # the exact verified digest-named database, never an arbitrary path.
            candidate.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                sidecar = candidate.with_name(candidate.name + suffix)
                if not sidecar.is_symlink():
                    sidecar.unlink(missing_ok=True)
            remaining -= size


class LeaseGuard:
    def __init__(self, repository: Repository, job: Job, renew_seconds: float):
        self.repository, self.job, self.renew_seconds = repository, job, renew_seconds
        self.stop = threading.Event()
        self.lost = threading.Event()
        self.thread = threading.Thread(
            target=self._renew, name="analysis-lease", daemon=True
        )

    def _renew(self):
        while not self.stop.wait(self.renew_seconds):
            try:
                renewed = self.repository.renew(self.job)
            except Exception:  # noqa: BLE001 -- any failed renewal fences all publication
                renewed = False
            if not renewed:
                self.lost.set()
                return

    def check(self):
        if self.lost.is_set():
            raise LeaseLost()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=15)


def process_job(job: Job, repository: Repository, storage: ObjectStore, config: Config):
    pipeline = get_pipeline(job.pipeline)
    task_budget = job.params.get("budget", {})
    if not isinstance(task_budget, dict):
        raise AnalysisError("INVALID_INPUT", "Task budget must be an object")

    def budget(name, maximum):
        value = task_budget.get(name, maximum)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise AnalysisError(
                "INVALID_INPUT", "Task budget must contain positive integers"
            )
        return min(value, maximum)

    limits = Limits(max_input_bytes=budget("max_file_bytes", 32 * 1024 * 1024))
    with LeaseGuard(repository, job, config.renew_seconds) as lease:
        artifact = repository.input_artifact(job)
        auxiliary = (
            repository.auxiliary_artifacts(job)
            if hasattr(repository, "auxiliary_artifacts")
            else []
        )
        inputs = [artifact, *auxiliary]
        if (
            any(
                isinstance(item["size"], bool)
                or not isinstance(item["size"], int)
                or not 0 < item["size"] <= limits.max_input_bytes
                for item in inputs
            )
            or sum(item["size"] for item in inputs) > config.analysis_download_bytes
        ):
            raise AnalysisError(
                "INPUT_LIMIT_EXCEEDED",
                "Combined raw and auxiliary downloads exceed the analysis byte budget",
            )
        work_root = Path(config.work_dir).resolve()
        work_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f"job-{job.id}-", dir=work_root
        ) as work_dir:
            disk = WorkspaceBudget(
                work_dir,
                max_bytes=config.analysis_temp_bytes,
                min_free_bytes=config.analysis_min_free_bytes,
                index_dir=work_root / "indexes" if job.kind == "JAVA_HEAP" else None,
                index_digest=artifact["sha256"],
            )

            def before_write(size):
                lease.check()
                disk.check(additional_bytes=size)

            raw_path = Path(work_dir) / "raw.profile"
            disk.check(additional_bytes=artifact["size"])
            raw = storage.download(
                artifact["object_key"],
                raw_path,
                expected_size=artifact["size"],
                max_bytes=limits.max_input_bytes,
                before_write=before_write,
            )
            lease.check()
            if len(raw) > limits.max_input_bytes:
                raise AnalysisError(
                    "INPUT_LIMIT_EXCEEDED", "Raw artifact exceeds the task byte budget"
                )
            if (
                len(raw) != artifact["size"]
                or hashlib.sha256(raw).hexdigest() != artifact["sha256"].lower()
            ):
                raise AnalysisError(
                    "ARTIFACT_INTEGRITY_ERROR",
                    "Raw artifact size or SHA-256 does not match metadata",
                )
            context = {
                "pipeline": job.pipeline,
                "kind": job.kind,
                "format": artifact.get("format") or pipeline.formats[0],
                "artifact": artifact,
                "inputs": [artifact],
                "params": job.params,
                "raw_path": str(raw_path),
                "work_dir": work_dir,
                "limits": {
                    "input_bytes": limits.max_input_bytes,
                    "download_bytes": config.analysis_download_bytes,
                    "address_space_bytes": budget(
                        "max_address_space_bytes", config.analysis_memory_bytes
                    ),
                    "cpu_seconds": budget(
                        "max_cpu_seconds", config.analysis_cpu_seconds
                    ),
                    "processes": budget("max_processes", 16),
                    "wall_seconds": config.analysis_wall_seconds,
                    "output_bytes": budget("max_file_bytes", 32 * 1024 * 1024),
                    "temp_bytes": config.analysis_temp_bytes,
                    "min_free_bytes": config.analysis_min_free_bytes,
                },
                "index_dir": str(work_root / "indexes"),
            }
            for binary in auxiliary:
                binary_path = Path(work_dir) / "target.elf"
                disk.check(additional_bytes=binary["size"])
                binary_raw = storage.download(
                    binary["object_key"],
                    binary_path,
                    expected_size=binary["size"],
                    max_bytes=limits.max_input_bytes,
                    before_write=before_write,
                )
                if hashlib.sha256(binary_raw).hexdigest() != binary["sha256"].lower():
                    raise AnalysisError(
                        "ARTIFACT_INTEGRITY_ERROR",
                        "Auxiliary binary SHA-256 mismatches metadata",
                    )
                context["binary_path"] = str(binary_path)
                context["inputs"].append(binary)
            disk.check()
            pipeline.validate(context)
            isolated = analyze_isolated(
                context,
                check_lease=lease.check,
                on_progress=(lambda stage: repository.progress(job, stage))
                if hasattr(repository, "progress")
                else None,
            )
            isolated["metrics"]["downloaded_bytes"] = sum(
                item["size"] for item in context["inputs"]
            )
            telemetry.observe(
                "duration_seconds",
                isolated["metrics"]["duration_ms"] / 1000,
                kind=job.kind,
            )
            telemetry.observe(
                "cpu_seconds", isolated["metrics"]["cpu_seconds"], kind=job.kind
            )
            telemetry.observe(
                "temp_disk_bytes", isolated["metrics"]["temp_disk_bytes"], kind=job.kind
            )
            if isolated["metrics"]["max_rss_bytes"] is not None:
                telemetry.observe(
                    "max_rss_bytes", isolated["metrics"]["max_rss_bytes"], kind=job.kind
                )
            parsed = isolated["parsed"]
            partial = build_partial(
                parsed, job, artifact, private_paths=(work_dir, str(work_root))
            )
            if partial is not None:
                lease.check()
                repository.save_partial(job, partial, artifact)
            parsed["metadata"]["resource_usage"] = isolated["metrics"]
            parsed["metadata"]["input_digest"] = artifact["sha256"]
            settings = (
                repository.rule_settings()
                if hasattr(repository, "rule_settings")
                else []
            )
            result = make_result(
                parsed, job.kind, {**job.params, "_diagnostic_rules": settings}
            )
            prefix = f"results/tasks/{job.task_id}/attempts/{job.collection_attempt}/analysis/{job.id}/runs/{job.attempt}"
            outputs = build_outputs(
                parsed,
                task_id=str(job.task_id),
                job_id=str(job.id),
                input_sha256=artifact["sha256"],
                public_result=result,
                object_prefix=prefix,
                attempt_id=str(job.attempt_id),
                input_artifact_id=str(artifact["id"]) if "id" in artifact else None,
            )
            manifest = json.loads(outputs["manifest.json"])
            manifest.update(isolated["manifest"])
            manifest["metadata"] = parsed["metadata"]
            manifest["resource_usage"] = isolated["metrics"]
            for extra in parsed.get("extra_outputs", []):
                name = extra["name"]
                if name != "optimized.elf":
                    raise AnalysisError(
                        "INVALID_OUTPUT", "Unregistered analysis output name"
                    )
                body = (Path(work_dir) / name).read_bytes()
                if len(body) > 32 * 1024 * 1024:
                    raise AnalysisError(
                        "OUTPUT_LIMIT_EXCEEDED", "Analysis artifact exceeds byte limit"
                    )
                outputs[name] = body
                manifest["artifacts"].append(
                    {
                        "name": name,
                        "size": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "object_key": f"{prefix}/{name}",
                    }
                )
            outputs["manifest.json"] = json_bytes(manifest)
            disk.check()
            lease.check()
            artifacts = []
            for name, body in outputs.items():
                lease.check()
                disk.check()
                content_type = (
                    "application/x-elf"
                    if name.endswith(".elf")
                    else "application/gzip"
                    if name.endswith(".gz")
                    else "application/json"
                )
                metadata = storage.upload(f"{prefix}/{name}", body, content_type)
                artifacts.append(
                    {
                        **metadata,
                        "name": name,
                        "kind": "MANIFEST" if name == "manifest.json" else "RESULT",
                        "format": "elf"
                        if name.endswith(".elf")
                        else "json_gzip"
                        if name.endswith(".gz")
                        else "json",
                        "metadata": {
                            "analyzer_version": __version__,
                            "pipeline": job.pipeline,
                            "source": result["summary"]["source"],
                            "input_digest": artifact["sha256"],
                        },
                    }
                )
            lease.check()
            repository.complete(job, result, artifacts)
            return result


class Heartbeat:
    def __init__(self, path: str):
        self.path = Path(path)
        self.ready = False
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="analysis-health", daemon=True
        )

    def _run(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while not self.stop.is_set():
            try:
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps(
                        {
                            "updated_at": time.time(),
                            "ready": self.ready,
                            "version": __version__,
                        }
                    ),
                    encoding="utf-8",
                )
                os.replace(temporary, self.path)
            except OSError:
                pass
            self.stop.wait(2)


def run(config: Config):
    install_logging("drop-analyzer", __version__)
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    visible, digest = config.snapshot()
    emit(
        "drop-analyzer",
        __version__,
        "configuration_loaded",
        stage="startup",
        effective_config=visible,
        config_hash=digest,
    )
    telemetry.start(config)
    repository = Repository(
        config.database_url, config.worker_id, config.lease_seconds, config.queue
    )
    storage = ObjectStore(config)
    heartbeat = Heartbeat(config.health_file)
    heartbeat.thread.start()
    cleanup_stale_workdirs(config.work_dir)
    cleanup_indexes(config.work_dir)
    last_dependency_check = 0.0
    log(
        "worker_started",
        worker_id=config.worker_id,
        lease_seconds=config.lease_seconds,
        concurrency=1,
    )
    try:
        while not stop.is_set():
            job = None
            started = time.monotonic()
            try:
                cleanup_indexes(config.work_dir)
                if not heartbeat.ready or time.monotonic() - last_dependency_check > 30:
                    heartbeat.ready = repository.ready() and storage.ready()
                    if heartbeat.ready:
                        statuses = {
                            row["status"]: row["count"]
                            for row in repository.queue_stats()
                        }
                        for status in (
                            "pending",
                            "running",
                            "retry",
                            "success",
                            "failed",
                        ):
                            telemetry.gauge(
                                "analysis_jobs", statuses.get(status, 0), status=status
                            )
                    last_dependency_check = time.monotonic()
                    if not heartbeat.ready:
                        stop.wait(config.poll_seconds)
                        continue
                job = repository.claim()
                if job is None:
                    stop.wait(config.poll_seconds)
                    continue
                log("analysis_started", job=job)
                with telemetry.analysis_span(job):
                    result = process_job(job, repository, storage, config)
                telemetry.count("jobs_finished_total", kind=job.kind, status="success")
                cleanup_indexes(config.work_dir)
                log(
                    "analysis_succeeded",
                    job=job,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    total_samples=result["total_samples"],
                    node_count=result["node_count"],
                )
            except LeaseLost:
                telemetry.count("lease_lost_total", kind=job.kind if job else "all")
                log(
                    "analysis_lease_lost",
                    job=job,
                    level="warning",
                    error_code="ANALYSIS_LEASE_LOST",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            except Exception as exc:  # noqa: BLE001 -- normalize untrusted tool failures without diagnostics
                error = (
                    exc
                    if isinstance(exc, AnalysisError)
                    else AnalysisError(
                        "ANALYSIS_INTERNAL_ERROR",
                        "Analysis service encountered a temporary failure",
                        retryable=True,
                    )
                )
                telemetry.count(
                    "jobs_finished_total" if job else "dependency_failures_total",
                    kind=job.kind if job else "all",
                    status="failure",
                )
                log(
                    "analysis_failed" if job else "worker_dependency_unavailable",
                    job=job,
                    level="error",
                    error_code=error.code,
                    retryable=error.retryable,
                    error_type=type(exc).__name__,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                heartbeat.ready = False
                if job:
                    try:
                        repository.fail(job, error)
                    except LeaseLost:
                        log("late_failure_discarded", job=job, level="warning")
                    except Exception as failure:  # noqa: BLE001 -- leave failed DB writes for lease recovery
                        log(
                            "analysis_failure_write_unavailable",
                            job=job,
                            level="error",
                            error_type=type(failure).__name__,
                        )
                stop.wait(config.poll_seconds)
    finally:
        heartbeat.ready = False
        heartbeat.stop.set()
        heartbeat.thread.join(timeout=5)
        try:
            Path(config.health_file).unlink(missing_ok=True)
        except OSError:
            pass
        log("worker_stopped")
