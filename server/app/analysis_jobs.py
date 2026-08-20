"""Durable analyzer job orchestration.

The gRPC callback only persists collector artifacts and enqueues a job.  A
separate worker owns analyzer execution through a renewable database lease.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text

from server.app.analyzer_runner import (
    analyze_pprof_artifacts,
    analyze_raw_perf_artifacts,
    analyze_speedscope_artifacts,
)
from server.app.artifact_integrity import verify_artifact_bytes
from server.app.artifact_contracts import (
    COLLECTOR_CONTRACTS,
    CollectorArtifactContract,
    get_collector_contract,
)
from server.app.database import init_db, new_session
from server.app.logging_utils import log_event
from server.app.sql_repository import SqlRepository

ANALYZER_TYPE = "artifact-set"
ANALYZER_VERSION = "1.0.0"

ANALYSIS_READY_TYPES = {
    "flamegraph_json",
    "flamegraph_svg",
    "top_json",
    "ebpf_metrics",
    "continuous_summary",
    "continuous_flamegraph_json",
    "continuous_top_json",
    "java_flamegraph_html",
    "memory_json",
    "pprof_raw",
    "sys_metrics",
}


def artifact_input_checksum(artifacts: list[dict[str, Any]]) -> str:
    """Return a stable checksum used to deduplicate repeated Agent callbacks."""

    canonical = []
    for artifact in artifacts:
        canonical.append({
            "artifact_type": artifact.get("artifact_type", "raw"),
            "bucket": artifact.get("bucket", "mini-drop"),
            "object_key": artifact.get("object_key") or artifact.get("cos_key") or "",
            "filename": artifact.get("filename") or "",
            "local_path": artifact.get("local_path") or "",
            "size_bytes": int(artifact.get("size_bytes", 0) or 0),
            "sha256": artifact.get("sha256") or "",
            "metadata": artifact.get("metadata") or {},
        })
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def enqueue_artifact_analysis(
    repo: Any,
    task_id: str,
    artifacts: list[dict[str, Any]],
    artifact_ids: list[int] | None = None,
    *,
    collector_type: str | None = None,
):
    """Enqueue when the repository supports durable analysis jobs."""

    enqueue = getattr(repo, "enqueue_analysis_job", None)
    if not callable(enqueue):
        return None
    contract = (
        get_collector_contract(collector_type)
        if collector_type
        else None
    )
    return enqueue(
        task_id,
        analyzer_type=contract.analyzer_type if contract else ANALYZER_TYPE,
        analyzer_version=contract.analyzer_version if contract else ANALYZER_VERSION,
        input_checksum=artifact_input_checksum(artifacts),
        input_artifact_ids=artifact_ids or [],
        max_retries=int(os.getenv("MINI_DROP_ANALYZER_MAX_RETRIES", "3")),
    )


@dataclass(frozen=True)
class ProcessResult:
    job_id: str
    status: str


@dataclass(frozen=True)
class AnalyzerOutput:
    reason: str
    artifacts: list[dict[str, Any]]
    existing_artifact_ids: list[int]


class AnalyzerHandler(Protocol):
    analyzer_type: str
    version: str

    def analyze(self, task_id: str, artifacts: list[dict[str, Any]]) -> AnalyzerOutput:
        ...


class AnalyzerRegistry:
    """Version-aware registry; new analyzer types do not change the worker loop."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], AnalyzerHandler] = {}

    def register(self, handler: AnalyzerHandler) -> None:
        key = (handler.analyzer_type, handler.version)
        if key in self._handlers:
            raise ValueError(f"Analyzer already registered: {key[0]}@{key[1]}")
        self._handlers[key] = handler

    def resolve(self, analyzer_type: str, version: str) -> AnalyzerHandler:
        try:
            return self._handlers[(analyzer_type, version)]
        except KeyError as exc:
            raise LookupError(
                f"Analyzer not registered: {analyzer_type}@{version}"
            ) from exc


class ArtifactSetAnalyzer:
    analyzer_type = ANALYZER_TYPE
    version = ANALYZER_VERSION

    def analyze(self, task_id: str, artifacts: list[dict[str, Any]]) -> AnalyzerOutput:
        ready_ids = [
            int(item["id"])
            for item in artifacts
            if item.get("id") is not None
            and item.get("artifact_type") in ANALYSIS_READY_TYPES
        ]
        if ready_ids:
            return AnalyzerOutput(_done_reason(artifacts), [], ready_ids)

        generated = analyze_raw_perf_artifacts(
            task_id, artifacts, allow_remote=True
        )
        if not generated:
            raise RuntimeError("未生成分析产物，请检查输入产物、挂载路径和 Analyzer 日志")
        return AnalyzerOutput(
            "Analyzer Worker 已生成火焰图和热点分析结果",
            generated,
            [],
        )


class CollectorContractAnalyzer:
    """Validate one collector's declared output and finish/generate analysis."""

    def __init__(self, contract: CollectorArtifactContract) -> None:
        self.contract = contract
        self.analyzer_type = contract.analyzer_type
        self.version = contract.analyzer_version

    def analyze(self, task_id: str, artifacts: list[dict[str, Any]]) -> AnalyzerOutput:
        artifact_types = self.contract.validate(artifacts)
        ready_ids = [
            int(item["id"])
            for item in artifacts
            if item.get("id") is not None
            and item.get("artifact_type") in self.contract.analysis_types
        ]

        if self.contract.collector_type == "perf_cpu" and not artifact_types.intersection(
            self.contract.analysis_types
        ):
            generated = analyze_raw_perf_artifacts(task_id, artifacts, allow_remote=True)
            if not generated:
                raise RuntimeError(
                    "perf_cpu: 原始 perf.data 未能生成火焰图和热点分析结果"
                )
            for artifact in generated:
                metadata = dict(artifact.get("metadata") or {})
                metadata.update({
                    "collector_type": self.contract.collector_type,
                    "analyzer_type": self.analyzer_type,
                    "analyzer_version": self.version,
                })
                artifact["metadata"] = metadata
            return AnalyzerOutput(
                "perf CPU 原始栈已生成火焰图与 TopN 热点",
                generated,
                [],
            )

        if self.contract.collector_type == "continuous_perf" and not artifact_types.intersection(
            self.contract.analysis_types
        ):
            generated = analyze_raw_perf_artifacts(task_id, artifacts, allow_remote=True)
            if not generated:
                raise RuntimeError(
                    "continuous_perf: 原始 perf.data 未能生成窗口火焰图和热点结果"
                )
            type_mapping = {
                "flamegraph_json": "continuous_flamegraph_json",
                "flamegraph_svg": "continuous_flamegraph_svg",
                "top_json": "continuous_top_json",
            }
            converted = []
            for artifact in generated:
                mapped_type = type_mapping.get(artifact.get("artifact_type"))
                if not mapped_type:
                    continue
                artifact["artifact_type"] = mapped_type
                metadata = dict(artifact.get("metadata") or {})
                metadata.update({
                    "collector_type": self.contract.collector_type,
                    "analyzer_type": self.analyzer_type,
                    "analyzer_version": self.version,
                    "window_index": 0,
                })
                artifact["metadata"] = metadata
                converted.append(artifact)
            if not converted:
                raise RuntimeError("continuous_perf: 未生成可登记的窗口分析产物")
            return AnalyzerOutput(
                "持续采样原始栈已生成窗口火焰图与 TopN 热点",
                converted,
                [],
            )

        if self.contract.collector_type in ("go_pprof", "pyspy") and not artifact_types.intersection(
            self.contract.analysis_types
        ):
            generated = (
                analyze_pprof_artifacts(task_id, artifacts, allow_remote=True)
                if self.contract.collector_type == "go_pprof"
                else analyze_speedscope_artifacts(task_id, artifacts, allow_remote=True)
            )
            if not generated:
                raise RuntimeError(
                    f"{self.contract.collector_type}: 原始产物未能生成火焰图和热点分析结果"
                )
            for artifact in generated:
                metadata = dict(artifact.get("metadata") or {})
                metadata.update({
                    "collector_type": self.contract.collector_type,
                    "analyzer_type": self.analyzer_type,
                    "analyzer_version": self.version,
                })
                artifact["metadata"] = metadata
            return AnalyzerOutput(
                f"{self.contract.collector_type} 原始栈已生成火焰图与 TopN 热点",
                generated,
                [],
            )

        if not ready_ids:
            raise RuntimeError(
                f"{self.contract.collector_type}: 必要产物存在，但没有可登记的分析产物"
            )
        return AnalyzerOutput(
            _collector_done_reason(self.contract.collector_type),
            [],
            ready_ids,
        )


def default_analyzer_registry() -> AnalyzerRegistry:
    registry = AnalyzerRegistry()
    # Historical jobs created before collector contracts remain replayable.
    registry.register(ArtifactSetAnalyzer())
    for contract in COLLECTOR_CONTRACTS.values():
        registry.register(CollectorContractAnalyzer(contract))
    return registry


class AnalysisWorker:
    def __init__(
        self,
        repo: SqlRepository,
        *,
        worker_id: str | None = None,
        lease_sec: int = 120,
        heartbeat_interval_sec: float | None = None,
        registry: AnalyzerRegistry | None = None,
    ) -> None:
        self.repo = repo
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}"
        self.lease_sec = max(10, lease_sec)
        self.heartbeat_interval_sec = (
            max(0.05, heartbeat_interval_sec)
            if heartbeat_interval_sec is not None
            else max(1.0, self.lease_sec / 3)
        )
        self.registry = registry or default_analyzer_registry()

    def process_once(self) -> ProcessResult | None:
        job = self.repo.claim_analysis_job(self.worker_id, lease_sec=self.lease_sec)
        if job is None:
            return None
        heartbeat = _AnalysisLeaseHeartbeat(
            self.repo,
            job.id,
            self.worker_id,
            lease_sec=self.lease_sec,
            interval_sec=self.heartbeat_interval_sec,
        )
        heartbeat.start()
        try:
            artifacts = self.repo.get_artifacts(job.task_id)
            for artifact in artifacts:
                status, reason = verify_artifact_bytes(artifact)
                marker = getattr(self.repo, "mark_artifact_integrity", None)
                if callable(marker) and artifact.get("id") is not None:
                    marker(int(artifact["id"]), status, reason)
            handler = self.registry.resolve(job.analyzer_type, job.analyzer_version)
            output = handler.analyze(job.task_id, artifacts)
            if heartbeat.lease_lost:
                log_event(
                    "warning",
                    "analysis_job_lease_lost",
                    analysis_job_id=job.id,
                    task_id=job.task_id,
                    worker_id=self.worker_id,
                )
                return ProcessResult(job.id, "LEASE_LOST")
            self.repo.complete_analysis_job(
                job.id,
                self.worker_id,
                output_artifacts=output.artifacts,
                output_artifact_ids=output.existing_artifact_ids,
                reason=output.reason,
            )
            return ProcessResult(job.id, "SUCCEEDED")
        except Exception as exc:
            if heartbeat.lease_lost:
                log_event(
                    "warning",
                    "analysis_job_abandoned_after_lease_loss",
                    analysis_job_id=job.id,
                    task_id=job.task_id,
                    worker_id=self.worker_id,
                    error=type(exc).__name__,
                )
                return ProcessResult(job.id, "LEASE_LOST")
            failed = self.repo.fail_analysis_job(
                job.id,
                self.worker_id,
                error_code=type(exc).__name__.upper(),
                error_message=str(exc),
                retry_delay_sec=int(os.getenv("MINI_DROP_ANALYZER_RETRY_DELAY_SEC", "5")),
            )
            log_event(
                "warning",
                "analysis_job_failed",
                analysis_job_id=job.id,
                task_id=job.task_id,
                status=failed.status,
                retry_count=failed.retry_count,
                error=type(exc).__name__,
            )
            return ProcessResult(job.id, failed.status)
        finally:
            heartbeat.stop()


class _AnalysisLeaseHeartbeat:
    """Renew an AnalysisJob lease while a synchronous analyzer is running."""

    def __init__(
        self,
        repo: SqlRepository,
        job_id: str,
        worker_id: str,
        *,
        lease_sec: int,
        interval_sec: float,
    ) -> None:
        self.repo = repo
        self.job_id = job_id
        self.worker_id = worker_id
        self.lease_sec = lease_sec
        self.interval_sec = interval_sec
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"analysis-lease-{job_id[:24]}",
            daemon=True,
        )

    @property
    def lease_lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval_sec * 2))

    def _run(self) -> None:
        # Renew once immediately after claim. Waiting for the first interval
        # makes short leases vulnerable to scheduler stalls and made the
        # Windows regression test timing-dependent under parallel load.
        while not self._stop.is_set():
            try:
                renewed = self.repo.renew_analysis_job_lease(
                    self.job_id,
                    self.worker_id,
                    lease_sec=self.lease_sec,
                )
            except Exception as exc:
                log_event(
                    "error",
                    "analysis_job_lease_renew_failed",
                    analysis_job_id=self.job_id,
                    worker_id=self.worker_id,
                    error=type(exc).__name__,
                )
                self._lost.set()
                return
            if not renewed:
                self._lost.set()
                return
            if self._stop.wait(self.interval_sec):
                return


def _done_reason(artifacts: list[dict[str, Any]]) -> str:
    types = {item.get("artifact_type") for item in artifacts}
    if "ebpf_metrics" in types:
        return "eBPF IO 延迟分布已生成"
    if "sys_metrics" in types:
        return "系统多维指标分析已生成"
    if "memory_json" in types:
        return "内存时间序列分析已生成"
    if "continuous_summary" in types:
        return "持续采样窗口分析已生成"
    if "java_flamegraph_html" in types:
        return "Java 火焰图已生成"
    if "pprof_raw" in types:
        return "pprof 采集数据已验证"
    return "Analyzer Worker 已验证可视化分析结果"


def _collector_done_reason(collector_type: str) -> str:
    reasons = {
        "perf_cpu": "perf CPU 火焰图与热点分析已验证",
        "ebpf_io": "eBPF IO 延迟分布已通过产物契约验证",
        "pyspy": "Python 用户态火焰图已通过产物契约验证",
        "continuous_perf": "持续采样窗口与时间轴摘要已通过产物契约验证",
        "java_async": "Java async-profiler 火焰图已通过产物契约验证",
        "go_pprof": "Go pprof 数据已通过产物契约验证",
        "memory_smaps": "进程内存趋势已通过产物契约验证",
        "sys_metrics": "系统多维指标已通过 sys_metrics.v2 契约验证",
    }
    return reasons[collector_type]


def _healthcheck() -> int:
    try:
        with new_session() as session:
            session.execute(text("SELECT 1"))
        return 0
    except Exception:
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini-Drop Analysis Worker")
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    init_db()
    if args.healthcheck:
        raise SystemExit(_healthcheck())

    worker = AnalysisWorker(
        SqlRepository(),
        lease_sec=int(os.getenv("MINI_DROP_ANALYZER_LEASE_SEC", "120")),
    )
    poll_sec = max(0.1, float(os.getenv("MINI_DROP_ANALYZER_POLL_SEC", "1")))
    while True:
        result = worker.process_once()
        if args.once:
            return
        if result is None:
            time.sleep(poll_sec)


if __name__ == "__main__":
    main()
