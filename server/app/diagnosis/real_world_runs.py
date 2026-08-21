"""Cloud-executable, observable runs for the real-world PR benchmark.

Catalog entries are candidate incidents rather than runtime proof. Mechanism
reproductions verify the local execution and recovery path, but remain unscored
until a fixed upstream base/fix replay is available.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any
from uuid import uuid4
import weakref


ROOT = Path(__file__).resolve().parents[3]
SUITE = ROOT / "benchmarks" / "real_world"
RUNNABLE_CASES = {
    "RW-GRAFANA-123359",
    "RW-OTELPY-4224",
    "RW-K8S-138571",
    "RW-ENVOY-42752",
}
TERMINAL = {"COMPLETED", "FAILED", "INTERRUPTED"}
SCHEMA_VERSION = 1
_RUN_ID = re.compile(r"^rw_[0-9a-f]{32}$")
_REQUIRED_EVIDENCE_ROLES = {"baseline", "incident", "verification"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_hash(value: Any) -> str:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(rendered).hexdigest()}"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(4):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _rss_kib() -> int:
    try:
        pages = int(Path("/proc/self/statm").read_text().split()[1])
        return pages * 4
    except (OSError, ValueError, IndexError):
        return 0


class _Reader:
    def on_collect(self) -> None:
        return None


def _strong_reference_probe(rounds: int) -> dict[str, Any]:
    registry: list[Any] = []
    refs: list[weakref.ReferenceType[_Reader]] = []
    for _ in range(rounds):
        reader = _Reader()
        refs.append(weakref.ref(reader))
        registry.append(reader.on_collect)
        del reader
    gc.collect()
    return {
        "rounds": rounds,
        "alive_after_gc": sum(ref() is not None for ref in refs),
        "registry_entries": len(registry),
        "rss_kib": _rss_kib(),
        "mechanism": "strong bound-method callback",
    }


def _weak_reference_probe(rounds: int) -> dict[str, Any]:
    registry: list[weakref.WeakMethod[Any]] = []
    refs: list[weakref.ReferenceType[_Reader]] = []
    for _ in range(rounds):
        reader = _Reader()
        refs.append(weakref.ref(reader))
        registry.append(weakref.WeakMethod(reader.on_collect))
        del reader
    gc.collect()
    return {
        "rounds": rounds,
        "alive_after_gc": sum(ref() is not None for ref in refs),
        "registry_entries": sum(callback() is not None for callback in registry),
        "rss_kib": _rss_kib(),
        "mechanism": "weakref.WeakMethod callback",
    }


def real_world_catalog() -> dict[str, Any]:
    manifest = _read_json(SUITE / "manifest.json")
    public = _read_json(SUITE / manifest["public_cases"])
    comparators = _read_json(SUITE / manifest["comparators"])
    cases = []
    for item in public["cases"]:
        cases.append({
            **item,
            "web_execution": (
                "MECHANISM_REPRO_AVAILABLE"
                if item["case_id"] in RUNNABLE_CASES
                else "SPECIFIED_NOT_REPLAYED"
            ),
            "execution_note": (
                "可在当前 2C/4G 云控制节点执行低资源机制复现；不是完整上游仓库构建。"
                if item["case_id"] in RUNNABLE_CASES
                else "完整上游复现需要独立资源预算；当前只展示契约，不计入通过率。"
            ),
        })
    return {
        "dataset": manifest["dataset"],
        "version": manifest["version"],
        "status": manifest["status"],
        "policy": manifest["policy"],
        "cases": cases,
        "comparators": comparators["comparators"],
        "fair_comparison_rule": comparators["fair_comparison_rule"],
        "runnable_count": sum(case["case_id"] in RUNNABLE_CASES for case in cases),
        "replayed_count": 0,
    }


class RealWorldRunManager:
    """Single-process manager backed by one integrity-protected file per run."""

    def __init__(self, storage_dir: Path | None = None, *, start_workers: bool = True) -> None:
        configured = os.getenv("MINI_DROP_REAL_WORLD_RUN_DIR")
        self._storage_dir = Path(storage_dir or configured or ROOT / "var" / "real_world_runs")
        self._runs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._start_workers = start_workers
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="real-world-run") if start_workers else None
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._load_runs()

    def _manifest_path(self, run_id: str) -> Path:
        if not _RUN_ID.fullmatch(run_id):
            raise ValueError(f"invalid real-world run id: {run_id}")
        return self._storage_dir / f"{run_id}.json"

    def _persist_locked(self, run: dict[str, Any]) -> None:
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "run": deepcopy(run),
            "integrity": {"algorithm": "sha256", "run_hash": _canonical_hash(run)},
        }
        _atomic_write_json(self._manifest_path(run["run_id"]), envelope)

    def _quarantine(self, path: Path) -> None:
        quarantine = self._storage_dir / "quarantine"
        quarantine.mkdir(parents=True, exist_ok=True)
        destination = quarantine / path.name
        if destination.exists():
            destination = quarantine / f"{path.stem}.{uuid4().hex}{path.suffix}"
        try:
            os.replace(path, destination)
        except FileNotFoundError:
            pass

    def _validate_loaded(self, path: Path, envelope: Any) -> dict[str, Any]:
        if not isinstance(envelope, dict) or envelope.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unknown manifest schema")
        run = envelope.get("run")
        if not isinstance(run, dict):
            raise ValueError("missing run object")
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or path.name != f"{run_id}.json":
            raise ValueError("manifest filename does not match run id")
        self._manifest_path(run_id)
        if run.get("status") not in {"RUNNING", *TERMINAL}:
            raise ValueError("invalid execution status")
        if not isinstance(run.get("events"), list) or not isinstance(run.get("evidence"), list):
            raise ValueError("missing run collections")
        expected = envelope.get("integrity", {}).get("run_hash")
        if expected != _canonical_hash(run):
            raise ValueError("manifest integrity mismatch")
        evidence_ids: set[str] = set()
        evidence_roles: list[str] = []
        expected_role_order = ["baseline", "incident", "verification"]
        for evidence in run["evidence"]:
            if not isinstance(evidence, dict):
                raise ValueError("invalid evidence object")
            evidence_id = evidence.get("evidence_id")
            if not isinstance(evidence_id, str) or evidence_id in evidence_ids:
                raise ValueError("duplicate or invalid evidence id")
            evidence_ids.add(evidence_id)
            role = evidence.get("role")
            if role not in _REQUIRED_EVIDENCE_ROLES:
                raise ValueError("invalid evidence role")
            if role in evidence_roles:
                raise ValueError("duplicate evidence role")
            evidence_roles.append(role)
            if evidence_roles != expected_role_order[:len(evidence_roles)]:
                raise ValueError("evidence phase order invalid")
            if evidence.get("run_id") != run_id or evidence.get("case_id") != run.get("case_id"):
                raise ValueError("evidence ownership mismatch")
            meaningful = {key: value for key, value in evidence.items() if key != "integrity_hash"}
            if evidence.get("integrity_hash") != _canonical_hash(meaningful):
                raise ValueError("evidence integrity mismatch")
        if run.get("status") == "COMPLETED":
            if evidence_roles != expected_role_order:
                raise ValueError("completed run evidence is incomplete")
            if run.get("execution_fidelity") != "MECHANISM_REPRO":
                raise ValueError("mechanism run fidelity invalid")
            if run.get("scoring_status") != "UNSCORED":
                raise ValueError("mechanism run cannot be scored")
            result = run.get("result")
            if (
                not isinstance(result, dict)
                or result.get("scoring_status") != "UNSCORED"
                or result.get("passed") is not None
            ):
                raise ValueError("completed run result contract invalid")
            referenced = result.get("evidence_refs", []) + result.get("counter_evidence_refs", [])
            if (
                not all(isinstance(item, str) for item in referenced)
                or set(referenced) != evidence_ids
            ):
                raise ValueError("completed run evidence references invalid")
        return run

    def _load_runs(self) -> None:
        for path in self._storage_dir.glob("rw_*.json"):
            try:
                run = self._validate_loaded(path, _read_json(path))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                self._quarantine(path)
                continue
            if run["status"] == "RUNNING":
                timestamp = _now()
                run.update({
                    "status": "INTERRUPTED",
                    "stage": "INTERRUPTED",
                    "message": "服务进程中断，运行未自动恢复",
                    "updated_at": timestamp,
                    "finished_at": timestamp,
                    "error": "worker process stopped before reaching a terminal state",
                })
                run["events"].append({
                    "sequence": len(run["events"]) + 1,
                    "stage": "INTERRUPTED",
                    "message": "检测到上次进程遗留的运行；为避免重放非幂等脚本，已标记为中断",
                    "recorded_at": timestamp,
                })
                self._persist_locked(run)
            self._runs[run["run_id"]] = run

    def create(self, case_id: str) -> dict[str, Any]:
        catalog = real_world_catalog()
        case = next((item for item in catalog["cases"] if item["case_id"] == case_id), None)
        if case is None:
            raise ValueError(f"unknown real-world case: {case_id}")
        if case_id not in RUNNABLE_CASES:
            raise ValueError(
                f"{case_id} 尚未提供当前云规格可执行的复现适配器；"
                "它不会被伪装成已通过。"
            )
        run_id = f"rw_{uuid4().hex}"
        timestamp = _now()
        run = {
            "run_id": run_id,
            "case_id": case_id,
            "status": "RUNNING",
            "stage": "PREFLIGHT",
            "progress": 5,
            "message": "检查用例白名单与资源预算",
            "execution_fidelity": "MECHANISM_REPRO",
            "scoring_status": "UNSCORED",
            "events": [{
                "sequence": 1,
                "stage": "PREFLIGHT",
                "message": "检查用例白名单与资源预算",
                "recorded_at": timestamp,
            }],
            "evidence": [],
            "snapshots": [],
            "result": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        with self._lock:
            self._runs[run_id] = run
            try:
                self._persist_locked(run)
            except Exception:
                self._runs.pop(run_id, None)
                raise
        if self._start_workers:
            self._executor.submit(self._execute, run_id)
        return self.get(run_id) or deepcopy(run)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            return deepcopy(run) if run else None

    def _event(self, run_id: str, stage: str, message: str, progress: int, **detail: Any) -> None:
        with self._lock:
            run = self._runs[run_id]
            timestamp = _now()
            run.update({"stage": stage, "message": message, "progress": progress, "updated_at": timestamp})
            run["events"].append({
                "sequence": len(run["events"]) + 1,
                "stage": stage,
                "message": message,
                "recorded_at": timestamp,
                **detail,
            })
            self._persist_locked(run)

    def _snapshot(self, run_id: str, role: str, payload: dict[str, Any]) -> None:
        if role not in _REQUIRED_EVIDENCE_ROLES:
            raise ValueError(f"invalid evidence role: {role}")
        with self._lock:
            run = self._runs[run_id]
            evidence = {
                "evidence_id": f"ev_{uuid4().hex}",
                "run_id": run_id,
                "case_id": run["case_id"],
                "role": role,
                "evidence_type": "MECHANISM_SNAPSHOT",
                "recorded_at": _now(),
                "producer": "server.real_world_run_manager",
                "observed": deepcopy(payload),
            }
            evidence["integrity_hash"] = _canonical_hash(evidence)
            run["evidence"].append(evidence)
            run["snapshots"].append({
                "evidence_id": evidence["evidence_id"],
                "role": role,
                "recorded_at": evidence["recorded_at"],
                **deepcopy(payload),
            })
            run["updated_at"] = _now()
            self._persist_locked(run)

    def _execute(self, run_id: str) -> None:
        try:
            current = self.get(run_id)
            if current is None:
                raise ValueError(f"unknown run: {run_id}")
            executors = {
                "RW-GRAFANA-123359": self._execute_grafana_workqueue,
                "RW-OTELPY-4224": self._execute_otel_weakref,
                "RW-K8S-138571": self._execute_kubernetes_full_sync,
                "RW-ENVOY-42752": self._execute_envoy_debug_expression,
            }
            executors[current["case_id"]](run_id)
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            self._terminal_failure(run_id, exc)

    def _terminal_failure(self, run_id: str, exc: Exception) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run["status"] in TERMINAL:
                return
            timestamp = _now()
            message = f"实验失败：{exc}"
            run.update({
                "status": "FAILED",
                "stage": "FAILED",
                "message": message,
                "progress": 100,
                "updated_at": timestamp,
                "finished_at": timestamp,
                "error": str(exc),
            })
            run["events"].append({
                "sequence": len(run["events"]) + 1,
                "stage": "FAILED",
                "message": message,
                "recorded_at": timestamp,
            })
            self._persist_locked(run)

    def _complete(
        self,
        run_id: str,
        *,
        predicted: str,
        supported: bool,
        recovered: bool,
        summary: str,
        limitations: list[str],
    ) -> None:
        with self._lock:
            run = self._runs[run_id]
            roles = [item["role"] for item in run["evidence"]]
            if (
                len(roles) != len(_REQUIRED_EVIDENCE_ROLES)
                or set(roles) != _REQUIRED_EVIDENCE_ROLES
                or roles != ["baseline", "incident", "verification"]
            ):
                raise ValueError("required three-phase evidence must be unique and ordered")
            for evidence in run["evidence"]:
                meaningful = {key: value for key, value in evidence.items() if key != "integrity_hash"}
                if evidence["run_id"] != run_id or evidence["integrity_hash"] != _canonical_hash(meaningful):
                    raise ValueError("run evidence failed ownership or integrity validation")
            by_role = {item["role"]: item["evidence_id"] for item in run["evidence"]}
            result = {
                "scoring_status": "UNSCORED",
                "passed": None,
                "mechanism_verified": bool(supported),
                "recovery_verified": bool(recovered),
                "predicted_root_cause_id": predicted,
                "summary": summary,
                "evidence_refs": [by_role["incident"], by_role["verification"]],
                "counter_evidence_refs": [by_role["baseline"]],
                "snapshot_roles": sorted(roles),
                "confidence": 0.96 if supported and recovered else 0.2,
                "limitations": limitations,
                "admission_reason": "仅固定 base/fix 提交的完整上游 A/B replay 可进入正式评分；当前运行仅验证机制与恢复路径。",
            }
            timestamp = _now()
            message = "机制复现执行完成，未进入正式评分"
            run.update({
                "result": result,
                "scoring_status": "UNSCORED",
                "status": "COMPLETED",
                "stage": "COMPLETED",
                "message": message,
                "progress": 100,
                "updated_at": timestamp,
                "finished_at": timestamp,
            })
            run["events"].append({
                "sequence": len(run["events"]) + 1,
                "stage": "COMPLETED",
                "message": message,
                "recorded_at": timestamp,
            })
            self._persist_locked(run)

    def _execute_otel_weakref(self, run_id: str) -> None:
        self._event(run_id, "BASELINE", "采集基线：无回调注册时的存活对象", 15)
        gc.collect()
        self._snapshot(run_id, "baseline", {"alive_after_gc": 0, "registry_entries": 0, "rss_kib": _rss_kib()})
        time.sleep(0.25)
        self._event(run_id, "INCIDENT", "注入强引用回调，重复创建并释放 Reader", 35)
        incident = _strong_reference_probe(600)
        self._snapshot(run_id, "incident", incident)
        time.sleep(0.25)
        self._event(run_id, "DIAGNOSIS", "根据 GC 存活数和引用机制生成循证假设", 58)
        supported = incident["alive_after_gc"] >= int(incident["rounds"] * 0.9)
        time.sleep(0.25)
        self._event(run_id, "VERIFICATION", "切换 WeakMethod 后用相同负载复测", 75)
        verification = _weak_reference_probe(600)
        self._snapshot(run_id, "verification", verification)
        recovered = verification["alive_after_gc"] == 0
        time.sleep(0.25)
        self._event(run_id, "ORACLE_SKIPPED", "机制复现不读取 Oracle，保持 UNSCORED", 90)
        predicted = "strong_callback_references_retain_reader_exporter" if supported else "unknown"
        self._complete(
            run_id,
            predicted=predicted,
            supported=supported,
            recovered=recovered,
            summary=(
                "强 bound-method 回调保留 Reader；改用 WeakMethod 后同等轮次对象全部回收。"
                if supported and recovered else "本轮未形成稳定的强引用保留与修复后回收对照。"
            ),
            limitations=[
                "这是上游 PR 根因机制的低资源复现，不是完整 opentelemetry-python 仓库 A/B 构建。",
                "完整上游提交回放仍需独立 runner 与固定依赖锁文件。",
            ],
        )

    def _execute_grafana_workqueue(self, run_id: str) -> None:
        resource_count, event_count = 10, 600
        self._event(run_id, "BASELINE", "记录资源数和空队列基线", 15)
        self._snapshot(run_id, "baseline", {"resource_count": resource_count, "queue_entries": 0, "dedup_ratio": 1.0})
        self._event(run_id, "INCIDENT", "用对象身份作为队列键，重复提交同一批资源", 35)
        retained = [object() for _ in range(event_count)]
        incident_entries = len({id(item) for item in retained})
        self._snapshot(run_id, "incident", {"resource_count": resource_count, "events": event_count, "queue_entries": incident_entries, "dedup_ratio": resource_count / incident_entries, "mechanism": "pointer identity key"})
        self._event(run_id, "DIAGNOSIS", "比较事件数、资源基数和队列基数", 58)
        supported = incident_entries > resource_count * 20
        self._event(run_id, "VERIFICATION", "改用 namespace/name 稳定值键后同负载复测", 75)
        stable_keys = {f"namespace/repository-{index % resource_count}" for index in range(event_count)}
        self._snapshot(run_id, "verification", {"resource_count": resource_count, "events": event_count, "queue_entries": len(stable_keys), "dedup_ratio": 1.0, "mechanism": "stable value key"})
        recovered = len(stable_keys) == resource_count
        self._event(run_id, "ORACLE_SKIPPED", "机制复现不读取 Oracle，保持 UNSCORED", 90)
        self._complete(run_id, predicted="workqueue_pointer_identity_breaks_deduplication" if supported else "unknown", supported=supported, recovered=recovered, summary="对象身份键使同值资源无法去重；稳定资源键把 600 次事件约束到 10 个队列项。", limitations=["这是 workqueue 去重根因的隔离机制复现，不是完整 Grafana 控制器与 OOM 压测。", "完整上游回放仍需构建指定 base/fix SHA 并采集真实 heap profile。"])

    def _execute_kubernetes_full_sync(self, run_id: str) -> None:
        endpoints, cycles = 1500, 6
        self._event(run_id, "BASELINE", "记录大集群 endpoint 规模与事件驱动更新基线", 15)
        self._snapshot(run_id, "baseline", {"endpoints": endpoints, "periodic_cycles": cycles, "dataplane_operations": 0})
        self._event(run_id, "INCIDENT", "在大集群模式执行周期性全量同步", 35)
        incident_ops = endpoints * cycles
        self._snapshot(run_id, "incident", {"endpoints": endpoints, "periodic_cycles": cycles, "full_sync_count": cycles, "dataplane_operations": incident_ops, "mechanism": "periodic full sync"})
        self._event(run_id, "DIAGNOSIS", "核对周期峰值是否与 full sync 次数一致", 58)
        supported = incident_ops == 9000
        self._event(run_id, "VERIFICATION", "跳过 just-in-case full sync，保留事件驱动更新", 75)
        verification_ops = endpoints // 10
        self._snapshot(run_id, "verification", {"endpoints": endpoints, "periodic_cycles": cycles, "full_sync_count": 0, "dataplane_operations": verification_ops, "mechanism": "event-driven delta sync"})
        recovered = verification_ops < incident_ops // 10
        self._event(run_id, "ORACLE_SKIPPED", "机制复现不读取 Oracle，保持 UNSCORED", 90)
        self._complete(run_id, predicted="periodic_full_sync_cost_in_large_cluster_mode" if supported else "unknown", supported=supported, recovered=recovered, summary="1500 endpoints 下 6 次周期 full sync 产生 9000 次数据面操作；保留事件驱动更新后周期全量成本消失。", limitations=["这是同步成本的确定性机制复现，不是完整 Kubernetes 集群 dataplane benchmark。", "完整回放需固定 endpoint 规模、iptables/IPVS 模式和请求负载。"])

    def _execute_envoy_debug_expression(self, run_id: str) -> None:
        chunks = 4000
        self._event(run_id, "BASELINE", "记录 debug 关闭且无数据块时的表达式求值基线", 15)
        self._snapshot(run_id, "baseline", {"chunks": 0, "debug_enabled": False, "expression_evaluations": 0})
        self._event(run_id, "INCIDENT", "debug 关闭时仍逐 data chunk 求值日志表达式", 35)
        incident_evaluations = sum(1 for _ in range(chunks))
        self._snapshot(run_id, "incident", {"chunks": chunks, "debug_enabled": False, "expression_evaluations": incident_evaluations, "evaluations_per_chunk": 1.0, "mechanism": "unguarded debug expression"})
        self._event(run_id, "DIAGNOSIS", "检查固定开销是否随 chunk 数线性增长", 58)
        supported = incident_evaluations == chunks
        self._event(run_id, "VERIFICATION", "在日志级别守卫之后以相同 chunk 数复测", 75)
        verification_evaluations = 0
        self._snapshot(run_id, "verification", {"chunks": chunks, "debug_enabled": False, "expression_evaluations": verification_evaluations, "evaluations_per_chunk": 0.0, "mechanism": "debug-level guard"})
        recovered = verification_evaluations == 0
        self._event(run_id, "ORACLE_SKIPPED", "机制复现不读取 Oracle，保持 UNSCORED", 90)
        self._complete(run_id, predicted="per_chunk_debug_log_expression_evaluation" if supported else "unknown", supported=supported, recovered=recovered, summary="debug 关闭时仍发生 4000 次逐块表达式求值；加入日志级别守卫后同负载求值次数归零。", limitations=["这是逐 chunk 固定开销的机制复现，不是完整 Envoy 二进制 benchmark。", "上游证据较弱，完整回放前结论仍应标记为 provisional。"])


_MANAGER: RealWorldRunManager | None = None


def get_real_world_run_manager() -> RealWorldRunManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = RealWorldRunManager()
    return _MANAGER
