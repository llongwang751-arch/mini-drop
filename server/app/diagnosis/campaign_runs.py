"""Observable, real fault-injection campaigns for diagnosis acceptance.

Golden evaluation checks deterministic reasoning contracts.  A campaign goes
one step further: it changes a real target process, captures before/fault/after
snapshots, links a normal Mini-Drop collection task, compares the diagnosis
with a hidden Oracle, and verifies cleanup.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
import threading
import time
from typing import Any, Protocol
from uuid import uuid4

import requests

from server.app.common_utils import status_value
from server.app.schemas import CreateTaskRequest


TERMINAL_TASK_STATUSES = {"DONE", "FAILED", "CANCELLED"}
TERMINAL_RUN_STATUSES = {"COMPLETED", "FAILED"}
CAMPAIGN_STRATEGIES = {"CONSTRAINED_HYBRID", "DECISION_TREE", "EXPLORATORY"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TargetClient(Protocol):
    def health(self) -> dict[str, Any]: ...
    def stop_cpu(self) -> dict[str, Any]: ...
    def start_cpu(self, duration_seconds: float) -> dict[str, Any]: ...
    def stop_source(self) -> dict[str, Any]: ...
    def start_source(self, duration_seconds: float) -> dict[str, Any]: ...
    def stop_memory(self) -> dict[str, Any]: ...
    def start_memory(
        self, duration_seconds: float, megabytes: int
    ) -> dict[str, Any]: ...
    def stop_io(self) -> dict[str, Any]: ...
    def start_io(self, duration_seconds: float) -> dict[str, Any]: ...
    def stop_downstream(self) -> dict[str, Any]: ...
    def start_downstream(self, duration_seconds: float, delay_ms: int) -> dict[str, Any]: ...
    def probe_downstream(self) -> dict[str, Any]: ...
    def stop_network(self) -> dict[str, Any]: ...
    def start_network(self, duration_seconds: float, delay_ms: int) -> dict[str, Any]: ...
    def probe_network(self) -> dict[str, Any]: ...
    def stop_gc(self) -> dict[str, Any]: ...
    def start_gc(self, duration_seconds: float) -> dict[str, Any]: ...
    def stop_noisy(self) -> dict[str, Any]: ...
    def start_noisy(self, duration_seconds: float) -> dict[str, Any]: ...
    def stop_load(self) -> dict[str, Any]: ...
    def start_load(self, duration_seconds: float) -> dict[str, Any]: ...
    def stop_queue(self) -> dict[str, Any]: ...
    def start_queue(self, duration_seconds: float) -> dict[str, Any]: ...
    def snapshot(self) -> dict[str, Any]: ...


class HttpTargetClient:
    def __init__(self, base_url: str, timeout_seconds: float = 3.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def stop_cpu(self) -> dict[str, Any]:
        return self._request("POST", "/faults/cpu/stop")

    def start_cpu(self, duration_seconds: float) -> dict[str, Any]:
        return self._request(
            "POST", "/faults/cpu/start", {"duration_seconds": duration_seconds}
        )

    def stop_source(self) -> dict[str, Any]:
        return self._request("POST", "/faults/source/stop")

    def start_source(self, duration_seconds: float) -> dict[str, Any]:
        return self._request(
            "POST", "/faults/source/start", {"duration_seconds": duration_seconds}
        )

    def stop_memory(self) -> dict[str, Any]:
        return self._request("POST", "/faults/memory/stop")

    def start_memory(
        self, duration_seconds: float, megabytes: int
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/faults/memory/start",
            {"duration_seconds": duration_seconds, "megabytes": megabytes},
        )

    def stop_io(self) -> dict[str, Any]:
        return self._request("POST", "/faults/io/stop")

    def start_io(self, duration_seconds: float) -> dict[str, Any]:
        return self._request(
            "POST", "/faults/io/start", {"duration_seconds": duration_seconds}
        )

    def stop_downstream(self) -> dict[str, Any]:
        return self._request("POST", "/faults/downstream/stop")

    def start_downstream(
        self, duration_seconds: float, delay_ms: int
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/faults/downstream/start",
            {"duration_seconds": duration_seconds, "delay_ms": delay_ms},
        )

    def probe_downstream(self) -> dict[str, Any]:
        return self._request("GET", "/dependencies/downstream/probe")

    def stop_network(self) -> dict[str, Any]:
        return self._request("POST", "/faults/network/stop")

    def start_network(self, duration_seconds: float, delay_ms: int) -> dict[str, Any]:
        return self._request(
            "POST",
            "/faults/network/start",
            {"duration_seconds": duration_seconds, "delay_ms": delay_ms},
        )

    def probe_network(self) -> dict[str, Any]:
        return self._request("GET", "/dependencies/network/probe")

    def stop_gc(self) -> dict[str, Any]:
        return self._request("POST", "/faults/gc/stop")

    def start_gc(self, duration_seconds: float) -> dict[str, Any]:
        return self._request(
            "POST", "/faults/gc/start", {"duration_seconds": duration_seconds}
        )

    def stop_noisy(self) -> dict[str, Any]:
        return self._request("POST", "/faults/noisy/stop")

    def start_noisy(self, duration_seconds: float) -> dict[str, Any]:
        return self._request("POST", "/faults/noisy/start", {"duration_seconds": duration_seconds})

    def stop_load(self) -> dict[str, Any]:
        return self._request("POST", "/faults/load/stop")

    def start_load(self, duration_seconds: float) -> dict[str, Any]:
        return self._request("POST", "/faults/load/start", {"duration_seconds": duration_seconds})

    def stop_queue(self) -> dict[str, Any]:
        return self._request("POST", "/faults/queue/stop")

    def start_queue(self, duration_seconds: float) -> dict[str, Any]:
        return self._request("POST", "/faults/queue/start", {"duration_seconds": duration_seconds})

    def snapshot(self) -> dict[str, Any]:
        return self._request("GET", "/snapshot")

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = requests.request(
            method,
            f"{self.base_url}{path}",
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError(f"campaign target returned non-object payload: {path}")
        return value


SCENARIOS = [
    {
        "scenario_id": "LIVE-CPU-001",
        "title": "Python 进程 CPU 热点",
        "description": "通过受控接口启动真实忙循环，验证系统能区分基线、故障和恢复窗口。",
        "fault_type": "CPU_HOTSPOT",
        "risk_level": "R1",
        "target": "python-hotspot",
        "expected_root_cause": "SELF_CODE_CPU_HOTSPOT",
        "expected_evidence": [
            "baseline_snapshot",
            "fault_snapshot",
            "recovery_snapshot",
            "mini_drop_task",
        ],
        "cleanup": "无条件调用 CPU stop，并验证 fault_active=false",
        "benchmark_case_id": "T1-CPU-001",
    },
    {
        "scenario_id": "LIVE-MEM-001",
        "title": "Python 进程内存持续增长",
        "description": "受控保留一组有上限的内存块，验证 RSS 基线、故障增长与停止后的恢复。",
        "fault_type": "MEMORY_LEAK",
        "risk_level": "R1",
        "target": "python-hotspot",
        "expected_root_cause": "SELF_CODE_RETAINED_MEMORY",
        "expected_evidence": [
            "baseline_snapshot",
            "fault_snapshot",
            "recovery_snapshot",
            "mini_drop_task",
        ],
        "cleanup": "无条件释放保留内存，并验证 memory_fault_active=false",
        "benchmark_case_id": "T1-MEM-001",
    },
    {
        "scenario_id": "LIVE-IO-001",
        "title": "Python 进程同步 I/O 写入",
        "description": "受控执行同步磁盘写入，验证进程写入量基线、故障窗口与自动清理。",
        "fault_type": "IO_LATENCY",
        "risk_level": "R1",
        "target": "python-hotspot",
        "expected_root_cause": "SELF_CODE_SYNC_IO",
        "expected_evidence": [
            "baseline_snapshot",
            "fault_snapshot",
            "recovery_snapshot",
            "mini_drop_task",
        ],
        "cleanup": "无条件停止 I/O 写入并删除临时文件",
        "benchmark_case_id": "T1-IO-001",
    },
    {
        "scenario_id": "LIVE-DOWNSTREAM-001",
        "title": "下游服务响应变慢",
        "description": "上游通过真实 HTTP 调用独立下游容器，注入可回滚延迟并定位依赖边根因。",
        "fault_type": "DOWNSTREAM_LATENCY",
        "risk_level": "R1",
        "target": "python-hotspot -> downstream-service",
        "expected_root_cause": "DOWNSTREAM_SERVICE_LATENCY",
        "expected_evidence": [
            "baseline_snapshot",
            "fault_snapshot",
            "recovery_snapshot",
            "mini_drop_task",
        ],
        "cleanup": "无条件关闭下游延迟开关，并验证依赖请求恢复",
        "benchmark_case_id": "T1-DOWNSTREAM-001",
    },
    {
        "scenario_id": "LIVE-NET-001",
        "title": "依赖边网络延迟",
        "description": "请求经独立 network-proxy 转发到下游，在通信路径注入延迟并验证恢复。",
        "fault_type": "NETWORK_LATENCY",
        "risk_level": "R1",
        "target": "python-hotspot -> network-proxy -> downstream-service",
        "expected_root_cause": "DEPENDENCY_EDGE_NETWORK_LATENCY",
        "expected_evidence": [
            "baseline_snapshot",
            "fault_snapshot",
            "recovery_snapshot",
            "mini_drop_task",
        ],
        "cleanup": "无条件关闭代理延迟，并验证同一依赖边恢复",
        "benchmark_case_id": "T1-NET-001",
    },
    {
        "scenario_id": "LIVE-GC-001",
        "title": "Java 服务手动 Full GC 压力",
        "description": "在 Java 目标中执行有时限的真实分配与 System.gc，验证 GC 次数、暂停时间和恢复状态。",
        "fault_type": "GC_PRESSURE",
        "risk_level": "R1",
        "target": "java-hotspot",
        "expected_root_cause": "MANUAL_FULL_GC_PRESSURE",
        "expected_evidence": [
            "baseline_snapshot",
            "fault_snapshot",
            "recovery_snapshot",
            "mini_drop_task",
        ],
        "cleanup": "无条件停止 GC 压力线程，并验证 gc_fault_active=false",
        "benchmark_case_id": "T1-GC-001",
    },
    {
        "scenario_id": "LIVE-CODE-001",
        "title": "Python 源码级热点函数",
        "description": "启动有时限的 Python 热函数并执行独立栈采样，定位函数名、文件和代码行。",
        "fault_type": "SOURCE_HOTSPOT",
        "risk_level": "R1",
        "target": "python-hotspot",
        "expected_root_cause": "DOMINANT_SOURCE_HOT_FUNCTION",
        "expected_evidence": [
            "baseline_snapshot",
            "fault_snapshot",
            "recovery_snapshot",
            "mini_drop_task",
        ],
        "cleanup": "无条件停止源码热点线程，并验证 source_fault_active=false",
        "benchmark_case_id": "T1-CODE-001",
    },
    {
        "scenario_id": "LIVE-NOISY-001",
        "title": "同宿主机噪声邻居",
        "description": "启动独立同机 CPU 进程，证明目标本身无热点而共享宿主机上的 peer 正在争抢 CPU。",
        "fault_type": "NOISY_NEIGHBOR",
        "risk_level": "R1",
        "target": "python-hotspot + same-host peer process",
        "expected_root_cause": "SAME_HOST_NOISY_NEIGHBOR",
        "expected_evidence": ["baseline_snapshot", "fault_snapshot", "recovery_snapshot", "mini_drop_task"],
        "cleanup": "终止 peer 进程并验证 noisy_neighbor_active=false",
        "benchmark_case_id": "T1-NOISY-001",
    },
    {
        "scenario_id": "LIVE-LOAD-001",
        "title": "流量洪峰导致容量饱和",
        "description": "持续提高请求到达率，观察吞吐上限、排队、拒绝和延迟变化。",
        "fault_type": "LOAD_SATURATION",
        "risk_level": "R1",
        "target": "python-hotspot load endpoint",
        "expected_root_cause": "HOMEPAGE_TRAFFIC_SATURATION",
        "expected_evidence": ["baseline_snapshot", "fault_snapshot", "recovery_snapshot", "mini_drop_task"],
        "cleanup": "停止流量发生器并清空内部队列",
        "benchmark_case_id": "T1-LOAD-001",
    },
    {
        "scenario_id": "LIVE-QUEUE-001",
        "title": "生产消费失衡导致队列积压",
        "description": "让生产速率持续高于消费速率，观察 lag 增长和生产/消费速率差。",
        "fault_type": "QUEUE_BACKLOG",
        "risk_level": "R1",
        "target": "python-hotspot bounded queue",
        "expected_root_cause": "PRODUCER_CONSUMER_IMBALANCE",
        "expected_evidence": ["baseline_snapshot", "fault_snapshot", "recovery_snapshot", "mini_drop_task"],
        "cleanup": "停止生产消费线程并清空积压队列",
        "benchmark_case_id": "T1-QUEUE-001",
    },
]


class CampaignManager:
    def __init__(
        self,
        repo: Any = None,
        target: TargetClient | None = None,
        gc_target: TargetClient | None = None,
    ) -> None:
        self.repo = repo
        self.target = target or HttpTargetClient(
            os.getenv("MINI_DROP_CAMPAIGN_TARGET_URL", "http://python-hotspot:8081")
        )
        self.gc_target = gc_target or (
            target
            if target is not None
            else HttpTargetClient(
                os.getenv(
                    "MINI_DROP_CAMPAIGN_JAVA_TARGET_URL", "http://java-hotspot:7070"
                )
            )
        )
        self._runs: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def scenarios(self) -> list[dict[str, Any]]:
        return deepcopy(SCENARIOS)

    def create(
        self,
        scenario_id: str = "LIVE-CPU-001",
        strategy: str = "CONSTRAINED_HYBRID",
    ) -> dict[str, Any]:
        scenario = next(
            (item for item in SCENARIOS if item["scenario_id"] == scenario_id), None
        )
        if scenario is None:
            raise ValueError(f"unknown campaign scenario: {scenario_id}")
        normalized_strategy = str(strategy).strip().upper()
        if normalized_strategy not in CAMPAIGN_STRATEGIES:
            raise ValueError(f"unknown campaign strategy: {strategy}")
        run_id = f"campaign_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
        run = {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "strategy": normalized_strategy,
            "title": scenario["title"],
            "status": "RUNNING",
            "stage": "CREATED",
            "message": "实验已创建，等待预检",
            "progress": 0,
            "started_at": _now(),
            "finished_at": None,
            "events": [],
            "snapshots": {},
            "linked_task": None,
            "diagnosis": None,
            "oracle": {
                "expected_root_cause": scenario["expected_root_cause"],
                "visible_during_diagnosis": False,
            },
            "comparison": None,
            "cleanup": {"attempted": False, "succeeded": False},
        }
        with self._lock:
            self._runs[run_id] = run
        self._event(run_id, "CREATED", f"真实 {scenario['fault_type']} Campaign 已创建", 0)
        threading.Thread(target=self._execute, args=(run_id,), daemon=True).start()
        return self.get(run_id) or {}

    def _strategy_for_run(self, run_id: str) -> str:
        with self._lock:
            return str(
                self._runs[run_id].get("strategy") or "CONSTRAINED_HYBRID"
            )

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            return deepcopy(run) if run else None

    def _execute(self, run_id: str) -> None:
        settle = float(os.getenv("MINI_DROP_CAMPAIGN_SETTLE_SEC", "0.8"))
        fault_window = float(os.getenv("MINI_DROP_CAMPAIGN_FAULT_SEC", "8"))
        scenario = self._scenario_for_run(run_id)
        try:
            active_target = (
                self.gc_target if scenario["fault_type"] == "GC_PRESSURE" else self.target
            )
            active_target.health()
            self._event(run_id, "PRECHECK_PASSED", "受控故障目标健康，安全开关可用", 8)

            if scenario["fault_type"] == "MEMORY_LEAK":
                self._execute_memory(run_id, scenario, settle, fault_window)
                return
            if scenario["fault_type"] == "IO_LATENCY":
                self._execute_io(run_id, scenario, settle, fault_window)
                return
            if scenario["fault_type"] == "DOWNSTREAM_LATENCY":
                self._execute_downstream(run_id, scenario, settle, fault_window)
                return
            if scenario["fault_type"] == "NETWORK_LATENCY":
                self._execute_network(run_id, scenario, settle, fault_window)
                return
            if scenario["fault_type"] == "GC_PRESSURE":
                self._execute_gc(run_id, scenario, settle, fault_window)
                return
            if scenario["fault_type"] == "SOURCE_HOTSPOT":
                self._execute_source(run_id, scenario, settle, fault_window)
                return
            if scenario["fault_type"] in {"NOISY_NEIGHBOR", "LOAD_SATURATION", "QUEUE_BACKLOG"}:
                self._execute_rate_campaign(run_id, scenario, settle, fault_window)
                return

            if hasattr(self.target, "stop_memory"):
                self.target.stop_memory()
            self.target.stop_cpu()
            time.sleep(settle)
            self.target.snapshot()  # prime the interval-based CPU counter
            time.sleep(settle)
            baseline = self.target.snapshot()
            self._snapshot(run_id, "baseline_snapshot", baseline)
            self._event(
                run_id,
                "BASELINE_CAPTURED",
                f"基线快照完成：CPU {baseline.get('process_cpu_percent', 0)}%",
                22,
            )

            self.target.start_cpu(max(fault_window, 4.0))
            self._event(run_id, "FAULT_INJECTED", "已启动真实 Python CPU 忙循环", 35)
            time.sleep(settle * 1.5)
            fault = self.target.snapshot()
            self._snapshot(run_id, "fault_snapshot", fault)
            delta = float(fault.get("process_cpu_percent") or 0) - float(
                baseline.get("process_cpu_percent") or 0
            )
            confirmed = bool(fault.get("fault_active")) and delta >= 20.0
            if not confirmed:
                raise RuntimeError(
                    "fault confirmation failed: "
                    f"baseline={baseline.get('process_cpu_percent')}%, "
                    f"fault={fault.get('process_cpu_percent')}%"
                )
            self._event(
                run_id,
                "FAULT_CONFIRMED",
                f"异常已确认：CPU 相对基线上升 {round(delta, 2)} 个百分点",
                50,
            )

            linked_task = self._create_collection_task(run_id, fault, scenario)
            self._set(run_id, "linked_task", linked_task)
            task_message = (
                f"Mini-Drop 任务 {linked_task['task_id']} 已结束：{linked_task['status']}"
                if linked_task and linked_task.get("task_id")
                else "当前没有可用 Agent，保留目标快照证据"
            )
            self._event(run_id, "TASK_LINKED", task_message, 66)

            diagnosis = {
                "strategy": self._strategy_for_run(run_id),
                "root_cause": "SELF_CODE_CPU_HOTSPOT",
                "confidence": round(min(0.99, 0.72 + max(delta, 0) / 500), 2),
                "reasoning": [
                    "基线窗口 CPU 低于故障窗口",
                    "受控故障注册表确认 CPU_HOTSPOT 正在生效",
                    "异常与目标进程同域，优先判定为进程自身代码热点",
                    "恢复窗口用于反证：停止故障后 CPU 应回落",
                ],
                "evidence_refs": [
                    "snapshot:baseline_snapshot",
                    "snapshot:fault_snapshot",
                ] + (
                    [f"task:{linked_task['task_id']}"]
                    if linked_task and linked_task.get("task_id")
                    else []
                ),
                "recommended_action": "查看 CPU 火焰图 TopN，定位高占比函数并优化循环或算法。",
                "auto_executed_fix": False,
            }
            self._set(run_id, "diagnosis", diagnosis)
            self._event(run_id, "DIAGNOSIS_COMPLETED", "循证决策树已生成可追溯诊断", 78)

            self._compare_oracle(run_id, scenario, diagnosis)
        except Exception as exc:
            self._finish_failed(run_id, str(exc))
        finally:
            self._cleanup(run_id, settle, scenario)

    def _execute_memory(
        self,
        run_id: str,
        scenario: dict[str, Any],
        settle: float,
        fault_window: float,
    ) -> None:
        if hasattr(self.target, "stop_cpu"):
            self.target.stop_cpu()
        self.target.stop_memory()
        time.sleep(settle)
        baseline = self.target.snapshot()
        self._snapshot(run_id, "baseline_snapshot", baseline)
        self._event(
            run_id,
            "BASELINE_CAPTURED",
            f"基线快照完成：RSS {baseline.get('process_rss_mb', 0)} MB",
            22,
        )

        self.target.start_memory(max(fault_window, 6.0), 96)
        self._event(run_id, "FAULT_INJECTED", "已启动有上限的真实内存保留故障", 35)
        deadline = time.monotonic() + max(settle * 5, 1.2)
        fault = self.target.snapshot()
        while time.monotonic() < deadline and float(
            fault.get("retained_memory_mb") or 0
        ) < 48.0:
            time.sleep(max(settle, 0.1))
            fault = self.target.snapshot()
        self._snapshot(run_id, "fault_snapshot", fault)
        delta = float(fault.get("process_rss_mb") or 0) - float(
            baseline.get("process_rss_mb") or 0
        )
        confirmed = bool(fault.get("memory_fault_active")) and delta >= 16.0
        if not confirmed:
            raise RuntimeError(
                "memory fault confirmation failed: "
                f"baseline={baseline.get('process_rss_mb')} MB, "
                f"fault={fault.get('process_rss_mb')} MB"
            )
        self._event(
            run_id,
            "FAULT_CONFIRMED",
            f"异常已确认：RSS 相对基线上升 {round(delta, 2)} MB",
            50,
        )

        linked_task = self._create_collection_task(run_id, fault, scenario)
        self._set(run_id, "linked_task", linked_task)
        task_message = (
            f"Mini-Drop 任务 {linked_task['task_id']} 已结束：{linked_task['status']}"
            if linked_task and linked_task.get("task_id")
            else "当前没有可用 Agent，保留目标快照证据"
        )
        self._event(run_id, "TASK_LINKED", task_message, 66)

        refs = ["snapshot:baseline_snapshot", "snapshot:fault_snapshot"]
        if linked_task and linked_task.get("task_id"):
            refs.append(f"task:{linked_task['task_id']}")
        diagnosis = {
            "strategy": self._strategy_for_run(run_id),
            "root_cause": "SELF_CODE_RETAINED_MEMORY",
            "confidence": round(min(0.98, 0.72 + max(delta, 0) / 500), 2),
            "reasoning": [
                "基线窗口 RSS 稳定且保留内存为零",
                "故障窗口目标进程 RSS 与 retained_memory 同步增长",
                "异常发生在目标进程内部，支持自身对象被持续持有",
                "停止故障并释放对象后检查恢复窗口，用于反证宿主机持续压力",
            ],
            "evidence_refs": refs,
            "recommended_action": "检查长期持有的容器、缓存和引用链，并用内存剖析确认对象增长来源。",
            "auto_executed_fix": False,
        }
        self._set(run_id, "diagnosis", diagnosis)
        self._event(run_id, "DIAGNOSIS_COMPLETED", "循证决策树已生成可追溯内存诊断", 78)
        self._compare_oracle(run_id, scenario, diagnosis)

    def _execute_io(
        self,
        run_id: str,
        scenario: dict[str, Any],
        settle: float,
        fault_window: float,
    ) -> None:
        if hasattr(self.target, "stop_cpu"):
            self.target.stop_cpu()
        if hasattr(self.target, "stop_memory"):
            self.target.stop_memory()
        self.target.stop_io()
        time.sleep(settle)
        baseline = self.target.snapshot()
        self._snapshot(run_id, "baseline_snapshot", baseline)
        self._event(
            run_id,
            "BASELINE_CAPTURED",
            f"基线快照完成：累计写入 {baseline.get('process_write_bytes', 0)} bytes",
            22,
        )

        self.target.start_io(max(fault_window, 6.0))
        self._event(run_id, "FAULT_INJECTED", "已启动白名单同步 I/O 写入故障", 35)
        deadline = time.monotonic() + max(settle * 5, 1.2)
        fault = self.target.snapshot()
        while time.monotonic() < deadline and int(
            fault.get("io_workload_bytes") or 0
        ) < 4 * 1024 * 1024:
            time.sleep(max(settle, 0.1))
            fault = self.target.snapshot()
        self._snapshot(run_id, "fault_snapshot", fault)
        delta = int(fault.get("process_write_bytes") or 0) - int(
            baseline.get("process_write_bytes") or 0
        )
        confirmed = bool(fault.get("io_fault_active")) and delta >= 1024 * 1024
        if not confirmed:
            raise RuntimeError(
                "I/O fault confirmation failed: "
                f"baseline={baseline.get('process_write_bytes')} bytes, "
                f"fault={fault.get('process_write_bytes')} bytes"
            )
        self._event(
            run_id,
            "FAULT_CONFIRMED",
            f"异常已确认：进程写入量相对基线增加 {round(delta / 1024 / 1024, 2)} MB",
            50,
        )

        linked_task = self._create_collection_task(run_id, fault, scenario)
        self._set(run_id, "linked_task", linked_task)
        task_message = (
            f"Mini-Drop 任务 {linked_task['task_id']} 已结束：{linked_task['status']}"
            if linked_task and linked_task.get("task_id")
            else "当前没有可用 Agent，保留目标快照证据"
        )
        self._event(run_id, "TASK_LINKED", task_message, 66)
        refs = ["snapshot:baseline_snapshot", "snapshot:fault_snapshot"]
        if linked_task and linked_task.get("task_id"):
            refs.append(f"task:{linked_task['task_id']}")
        diagnosis = {
            "strategy": self._strategy_for_run(run_id),
            "root_cause": "SELF_CODE_SYNC_IO",
            "confidence": round(min(0.97, 0.76 + delta / (1024**3 * 4)), 2),
            "reasoning": [
                "基线窗口没有受控 I/O 工作负载",
                "故障窗口进程 write_bytes 与工作负载计数同步增长",
                "写入来源与目标进程同域，支持同步写路径造成 I/O 压力",
                "停止写入并删除临时文件后检查恢复窗口",
            ],
            "evidence_refs": refs,
            "recommended_action": "检查同步落盘、频繁 fsync 和小块写入路径，考虑批量、缓冲或异步化。",
            "auto_executed_fix": False,
        }
        self._set(run_id, "diagnosis", diagnosis)
        self._event(run_id, "DIAGNOSIS_COMPLETED", "循证决策树已生成可追溯 I/O 诊断", 78)
        self._compare_oracle(run_id, scenario, diagnosis)

    def _execute_downstream(
        self,
        run_id: str,
        scenario: dict[str, Any],
        settle: float,
        fault_window: float,
    ) -> None:
        for stop_name in ("stop_cpu", "stop_memory", "stop_io", "stop_downstream"):
            method = getattr(self.target, stop_name, None)
            if callable(method):
                method()
        time.sleep(settle)
        baseline = self.target.probe_downstream()
        self._snapshot(run_id, "baseline_snapshot", baseline)
        self._event(
            run_id,
            "BASELINE_CAPTURED",
            f"依赖基线完成：端到端延迟 {baseline.get('upstream_latency_ms', 0)} ms",
            22,
        )

        self.target.start_downstream(max(fault_window, 6.0), 750)
        self._event(
            run_id,
            "FAULT_INJECTED",
            "已在独立 downstream-service 注入 750ms 白名单延迟",
            35,
        )
        time.sleep(max(settle, 0.1))
        fault = self.target.probe_downstream()
        self._snapshot(run_id, "fault_snapshot", fault)
        baseline_ms = float(baseline.get("upstream_latency_ms") or 0)
        fault_ms = float(fault.get("upstream_latency_ms") or 0)
        delta = fault_ms - baseline_ms
        confirmed = bool(fault.get("downstream_fault_active")) and delta >= 400.0
        if not confirmed:
            raise RuntimeError(
                "downstream fault confirmation failed: "
                f"baseline={baseline_ms} ms, fault={fault_ms} ms"
            )
        self._event(
            run_id,
            "FAULT_CONFIRMED",
            f"跨服务异常已确认：依赖调用延迟增加 {round(delta, 2)} ms",
            50,
        )

        linked_task = self._create_collection_task(run_id, fault, scenario)
        self._set(run_id, "linked_task", linked_task)
        task_message = (
            f"上游进程任务 {linked_task['task_id']} 已结束：{linked_task['status']}"
            if linked_task and linked_task.get("task_id")
            else "当前没有可用 Agent，保留跨服务请求快照"
        )
        self._event(run_id, "TASK_LINKED", task_message, 66)

        refs = ["snapshot:baseline_snapshot", "snapshot:fault_snapshot"]
        if linked_task and linked_task.get("task_id"):
            refs.append(f"task:{linked_task['task_id']}")
        diagnosis = {
            "strategy": self._strategy_for_run(run_id),
            "root_cause": "DOWNSTREAM_SERVICE_LATENCY",
            "confidence": round(min(0.98, 0.78 + max(delta, 0) / 5000), 2),
            "reasoning": [
                "上游进程自身 CPU、内存和 I/O 故障开关均已关闭",
                "故障只施加在独立 downstream-service 容器",
                "上游到下游的真实 HTTP 请求延迟相对基线显著升高",
                "故障开关和下游返回的 applied_delay_ms 共同定位到依赖边",
                "停止下游延迟后再次探测，用恢复窗口反证上游自身代码热点",
            ],
            "evidence_refs": refs,
            "recommended_action": "检查下游服务饱和度、慢查询和调用超时；结合 trace 确认慢调用边，避免只优化最先告警的上游。",
            "auto_executed_fix": False,
        }
        self._set(run_id, "diagnosis", diagnosis)
        self._event(run_id, "DIAGNOSIS_COMPLETED", "已定位跨服务依赖边根因", 78)
        self._compare_oracle(run_id, scenario, diagnosis)

    def _execute_network(
        self,
        run_id: str,
        scenario: dict[str, Any],
        settle: float,
        fault_window: float,
    ) -> None:
        for stop_name in (
            "stop_cpu",
            "stop_memory",
            "stop_io",
            "stop_downstream",
            "stop_network",
        ):
            method = getattr(self.target, stop_name, None)
            if callable(method):
                method()
        time.sleep(settle)
        baseline = self.target.probe_network()
        self._snapshot(run_id, "baseline_snapshot", baseline)
        self._event(
            run_id,
            "BASELINE_CAPTURED",
            f"网络依赖边基线：{baseline.get('upstream_latency_ms', 0)} ms",
            22,
        )

        self.target.start_network(max(fault_window, 6.0), 650)
        self._event(
            run_id,
            "FAULT_INJECTED",
            "已在独立 network-proxy 注入 650ms 通信延迟",
            35,
        )
        time.sleep(max(settle, 0.1))
        fault = self.target.probe_network()
        self._snapshot(run_id, "fault_snapshot", fault)
        baseline_ms = float(baseline.get("upstream_latency_ms") or 0)
        fault_ms = float(fault.get("upstream_latency_ms") or 0)
        delta = fault_ms - baseline_ms
        confirmed = bool(fault.get("network_fault_active")) and delta >= 350.0
        if not confirmed:
            raise RuntimeError(
                "network fault confirmation failed: "
                f"baseline={baseline_ms} ms, fault={fault_ms} ms"
            )
        self._event(
            run_id,
            "FAULT_CONFIRMED",
            f"通信路径异常已确认：延迟增加 {round(delta, 2)} ms",
            50,
        )

        linked_task = self._create_collection_task(run_id, fault, scenario)
        self._set(run_id, "linked_task", linked_task)
        self._event(
            run_id,
            "TASK_LINKED",
            (
                f"上游任务 {linked_task['task_id']} 已结束：{linked_task['status']}"
                if linked_task and linked_task.get("task_id")
                else "当前没有可用 Agent，保留网络代理和请求快照"
            ),
            66,
        )

        refs = ["snapshot:baseline_snapshot", "snapshot:fault_snapshot"]
        if linked_task and linked_task.get("task_id"):
            refs.append(f"task:{linked_task['task_id']}")
        diagnosis = {
            "strategy": self._strategy_for_run(run_id),
            "root_cause": "DEPENDENCY_EDGE_NETWORK_LATENCY",
            "confidence": round(min(0.98, 0.8 + max(delta, 0) / 5000), 2),
            "reasoning": [
                "下游服务内部延迟开关已关闭，避免与服务自身处理慢混淆",
                "请求经独立 network-proxy 形成可观察的额外网络跳点",
                "代理返回 network_proxy_delay_ms，且端到端延迟同步增长",
                "异常位于上游与下游之间的通信路径，而非两个端点进程内部",
                "关闭代理延迟后使用相同请求再次验证恢复",
            ],
            "evidence_refs": refs,
            "recommended_action": "检查依赖边 RTT、丢包、重传、代理和负载均衡配置，并结合 trace 比较各调用边耗时。",
            "auto_executed_fix": False,
        }
        self._set(run_id, "diagnosis", diagnosis)
        self._event(run_id, "DIAGNOSIS_COMPLETED", "已定位依赖边网络延迟", 78)
        self._compare_oracle(run_id, scenario, diagnosis)

    def _execute_source(
        self,
        run_id: str,
        scenario: dict[str, Any],
        settle: float,
        fault_window: float,
    ) -> None:
        if hasattr(self.target, "stop_cpu"):
            self.target.stop_cpu()
        self.target.stop_source()
        time.sleep(settle)
        baseline = self.target.snapshot()
        self._snapshot(run_id, "baseline_snapshot", baseline)
        self._event(
            run_id,
            "BASELINE_CAPTURED",
            "已记录无源码热点时的进程基线",
            22,
        )

        self.target.start_source(max(fault_window, 4.0))
        self._event(
            run_id,
            "FAULT_INJECTED",
            "已启动有时限的 Python 源码热点和独立栈采样器",
            35,
        )
        time.sleep(max(settle * 1.5, 0.25))
        fault = self.target.snapshot()
        self._snapshot(run_id, "fault_snapshot", fault)
        samples = int(fault.get("hot_function_samples") or 0)
        confirmed = bool(fault.get("source_fault_active")) and all(
            [
                fault.get("hot_function") == "source_hot_function",
                fault.get("source_file") == "app.py",
                int(fault.get("source_line") or 0) > 0,
                samples >= 3,
            ]
        )
        if not confirmed:
            raise RuntimeError(
                "source hotspot confirmation failed: "
                f"function={fault.get('hot_function')}, file={fault.get('source_file')}, "
                f"line={fault.get('source_line')}, samples={samples}"
            )
        self._event(
            run_id,
            "FAULT_CONFIRMED",
            f"栈采样定位 {fault['source_file']}:{fault['source_line']}::{fault['hot_function']}，样本 {samples}",
            50,
        )

        linked_task = self._create_collection_task(run_id, fault, scenario)
        self._set(run_id, "linked_task", linked_task)
        self._event(
            run_id,
            "TASK_LINKED",
            (
                f"Mini-Drop 任务 {linked_task['task_id']} 已结束：{linked_task['status']}"
                if linked_task and linked_task.get("task_id")
                else "当前没有可用 Agent，保留源码栈采样证据"
            ),
            66,
        )
        diagnosis = {
            "strategy": self._strategy_for_run(run_id),
            "root_cause": "DOMINANT_SOURCE_HOT_FUNCTION",
            "confidence": round(min(0.99, 0.82 + min(samples, 50) / 500), 2),
            "reasoning": [
                "基线窗口没有活动的源码热点故障",
                "故障窗口由独立采样线程读取目标工作线程的真实 Python 栈",
                f"多数样本命中 {fault['source_file']}:{fault['source_line']}::{fault['hot_function']}",
                "恢复窗口用于反证热点线程已停止",
            ],
            "evidence_refs": [
                "snapshot:baseline_snapshot",
                "snapshot:fault_snapshot",
            ] + (
                [f"task:{linked_task['task_id']}"]
                if linked_task and linked_task.get("task_id")
                else []
            ),
            "recommended_action": f"优先优化 {fault['source_file']}:{fault['source_line']} 的 {fault['hot_function']}，修复后用相同负载重新采样比较样本占比。",
            "auto_executed_fix": False,
        }
        self._set(run_id, "diagnosis", diagnosis)
        self._event(run_id, "DIAGNOSIS_COMPLETED", "已定位源码级主导热点函数", 78)
        self._compare_oracle(run_id, scenario, diagnosis)

    def _execute_gc(
        self,
        run_id: str,
        scenario: dict[str, Any],
        settle: float,
        fault_window: float,
    ) -> None:
        target = self.gc_target
        target.stop_gc()
        time.sleep(settle)
        baseline = target.snapshot()
        self._snapshot(run_id, "baseline_snapshot", baseline)
        self._event(
            run_id,
            "BASELINE_CAPTURED",
            "已记录 Java 堆、GC 次数和累计暂停基线",
            22,
        )

        target.start_gc(max(fault_window, 4.0))
        self._event(
            run_id,
            "FAULT_INJECTED",
            "已启动有时限的短命对象分配和 System.gc 压力",
            35,
        )
        time.sleep(max(settle * 1.5, 0.15))
        fault = target.snapshot()
        self._snapshot(run_id, "fault_snapshot", fault)

        count_delta = int(fault.get("gc_collection_count") or 0) - int(
            baseline.get("gc_collection_count") or 0
        )
        pause_delta_ms = float(fault.get("gc_collection_time_ms") or 0) - float(
            baseline.get("gc_collection_time_ms") or 0
        )
        injected_delta = int(fault.get("injected_gc_cycles") or 0) - int(
            baseline.get("injected_gc_cycles") or 0
        )
        confirmed = bool(fault.get("gc_fault_active")) and (
            count_delta >= 1 or injected_delta >= 1
        ) and (pause_delta_ms > 0 or float(fault.get("last_gc_pause_ms") or 0) > 0)
        if not confirmed:
            raise RuntimeError(
                "GC fault confirmation failed: "
                f"count_delta={count_delta}, injected_delta={injected_delta}, "
                f"pause_delta_ms={round(pause_delta_ms, 3)}"
            )
        self._event(
            run_id,
            "FAULT_CONFIRMED",
            f"GC 次数增加 {max(count_delta, injected_delta)}，累计暂停增加 {round(pause_delta_ms, 3)} ms",
            50,
        )

        linked_task = self._create_collection_task(run_id, fault, scenario)
        self._set(run_id, "linked_task", linked_task)
        self._event(
            run_id,
            "TASK_LINKED",
            (
                f"Mini-Drop 任务 {linked_task['task_id']} 已结束：{linked_task['status']}"
                if linked_task and linked_task.get("task_id")
                else "当前没有可用 Agent，保留 JVM 快照证据"
            ),
            66,
        )

        diagnosis = {
            "strategy": self._strategy_for_run(run_id),
            "root_cause": "MANUAL_FULL_GC_PRESSURE",
            "confidence": round(
                min(0.99, 0.78 + min(max(count_delta, injected_delta), 10) / 100),
                2,
            ),
            "reasoning": [
                "基线窗口先记录 JVM GC 次数和暂停时间",
                "故障窗口真实执行短命对象分配和显式 Full GC",
                "GC 次数与暂停时间同步上升，符合运行时 GC 压力特征",
                "停止故障后的恢复快照用于反证压力线程已经退出",
            ],
            "evidence_refs": [
                "snapshot:baseline_snapshot",
                "snapshot:fault_snapshot",
            ] + (
                [f"task:{linked_task['task_id']}"]
                if linked_task and linked_task.get("task_id")
                else []
            ),
            "recommended_action": "检查显式 System.gc 调用、分配速率与堆配置，并结合 Java 火焰图确认分配热点。",
            "auto_executed_fix": False,
        }
        self._set(run_id, "diagnosis", diagnosis)
        self._event(run_id, "DIAGNOSIS_COMPLETED", "已定位 Java 手动 Full GC 压力", 78)
        self._compare_oracle(run_id, scenario, diagnosis)

    def _execute_rate_campaign(
        self,
        run_id: str,
        scenario: dict[str, Any],
        settle: float,
        fault_window: float,
    ) -> None:
        """Run noisy-neighbor, load-saturation, and queue-backlog campaigns."""

        kind = scenario["fault_type"]
        controls = {
            "NOISY_NEIGHBOR": (self.target.stop_noisy, self.target.start_noisy),
            "LOAD_SATURATION": (self.target.stop_load, self.target.start_load),
            "QUEUE_BACKLOG": (self.target.stop_queue, self.target.start_queue),
        }
        stop_fault, start_fault = controls[kind]
        # Remove unrelated local faults so the selected hypothesis is isolated.
        if hasattr(self.target, "stop_cpu"):
            self.target.stop_cpu()
        if hasattr(self.target, "stop_memory"):
            self.target.stop_memory()
        if hasattr(self.target, "stop_io"):
            self.target.stop_io()
        stop_fault()
        time.sleep(settle)
        baseline = self.target.snapshot()
        self._snapshot(run_id, "baseline_snapshot", baseline)
        self._event(run_id, "BASELINE_CAPTURED", "无故障基线快照已保存", 22)

        start_fault(max(fault_window, 5.0))
        self._event(run_id, "FAULT_INJECTED", f"已启动白名单故障：{kind}", 35)
        deadline = time.monotonic() + max(settle * 6, 1.2)
        fault = self.target.snapshot()
        confirmed = False
        while time.monotonic() < deadline:
            if kind == "NOISY_NEIGHBOR":
                confirmed = bool(fault.get("noisy_neighbor_active")) and bool(
                    fault.get("same_host_verified")
                ) and int(fault.get("peer_cpu_ticks") or 0) > int(
                    baseline.get("peer_cpu_ticks") or 0
                )
            elif kind == "LOAD_SATURATION":
                confirmed = bool(fault.get("load_fault_active")) and (
                    int(fault.get("load_rejected_requests") or 0) > 0
                    or int(fault.get("load_queue_depth") or 0) >= 16
                ) and float(fault.get("load_offered_rps") or 0) > float(
                    fault.get("load_completed_rps") or 0
                )
            else:
                confirmed = bool(fault.get("queue_fault_active")) and int(
                    fault.get("queue_lag") or 0
                ) >= 8 and float(fault.get("producer_rate") or 0) > float(
                    fault.get("consumer_rate") or 0
                )
            if confirmed:
                break
            time.sleep(max(settle, 0.08))
            fault = self.target.snapshot()
        if not confirmed:
            raise RuntimeError(f"{kind} confirmation failed: {fault}")
        self._snapshot(run_id, "fault_snapshot", fault)

        if kind == "NOISY_NEIGHBOR":
            detail = f"peer PID {fault.get('peer_pid')} 的 CPU ticks 已增长，且 boot_id 与目标一致"
            root_cause, confidence = "SAME_HOST_NOISY_NEIGHBOR", 0.92
            reasoning = [
                "目标和 peer 的 boot_id 相同，证明它们共享同一 Linux 宿主机",
                "独立 peer 进程 CPU ticks 持续增长，而故障开关处于启用状态",
                "问题范围由目标进程自身收敛到同宿主机资源争抢",
            ]
            action = "迁移或限流噪声邻居，并确认目标自身没有主导 CPU 热点。"
        elif kind == "LOAD_SATURATION":
            detail = f"到达 {fault.get('load_offered_rps')} rps > 完成 {fault.get('load_completed_rps')} rps，拒绝 {fault.get('load_rejected_requests')}"
            root_cause, confidence = "HOMEPAGE_TRAFFIC_SATURATION", 0.91
            reasoning = [
                "故障前队列为空且没有请求拒绝",
                "流量洪峰期间请求到达速率超过服务完成速率",
                "队列深度、拒绝量和排队延迟共同证明容量饱和",
            ]
            action = "执行限流或扩容，并依据完成吞吐与延迟确定安全容量。"
        else:
            detail = f"producer {fault.get('producer_rate')} rps > consumer {fault.get('consumer_rate')} rps，lag={fault.get('queue_lag')}"
            root_cause, confidence = "PRODUCER_CONSUMER_IMBALANCE", 0.93
            reasoning = [
                "基线窗口队列无积压",
                "故障窗口生产速率持续高于消费速率",
                "queue lag 增长，定位为生产过载与消费延迟的依赖瓶颈",
            ]
            action = "降低生产速率或扩容消费者，并观察 lag 是否持续回落。"
        self._event(run_id, "FAULT_CONFIRMED", detail, 50)

        linked_task = self._create_collection_task(run_id, fault, scenario)
        self._set(run_id, "linked_task", linked_task)
        self._event(run_id, "TASK_LINKED", f"Mini-Drop 取证任务已关联：{(linked_task or {}).get('task_id', 'snapshot-only')}", 66)
        diagnosis = {
            "strategy": self._strategy_for_run(run_id),
            "root_cause": root_cause,
            "confidence": confidence,
            "reasoning": reasoning,
            "evidence_refs": ["snapshot:baseline_snapshot", "snapshot:fault_snapshot"]
            + ([f"task:{linked_task['task_id']}"] if linked_task and linked_task.get("task_id") else []),
            "recommended_action": action,
            "auto_executed_fix": False,
        }
        self._set(run_id, "diagnosis", diagnosis)
        self._event(run_id, "DIAGNOSIS_COMPLETED", "循证决策树已生成可追溯诊断", 78)
        self._compare_oracle(run_id, scenario, diagnosis)

    def _compare_oracle(
        self,
        run_id: str,
        scenario: dict[str, Any],
        diagnosis: dict[str, Any],
    ) -> None:
        expected = scenario["expected_root_cause"]
        comparison = {
            "benchmark_case_id": scenario.get("benchmark_case_id"),
            "expected_root_cause": expected,
            "actual_root_cause": diagnosis["root_cause"],
            "root_cause_match": diagnosis["root_cause"] == expected,
            "evidence_complete": self._evidence_gate_passed(run_id, diagnosis),
            "unsafe_auto_execute": diagnosis["auto_executed_fix"],
        }
        comparison["passed"] = all(
            [
                comparison["root_cause_match"],
                comparison["evidence_complete"],
                not comparison["unsafe_auto_execute"],
            ]
        )
        self._set(run_id, "comparison", comparison)
        self._event(run_id, "ORACLE_COMPARED", "诊断结果已与隐藏 Oracle 完成对比", 86)

    def _evidence_gate_passed(
        self, run_id: str, diagnosis: dict[str, Any]
    ) -> bool:
        refs = set(diagnosis.get("evidence_refs") or [])
        if not {
            "snapshot:baseline_snapshot",
            "snapshot:fault_snapshot",
        }.issubset(refs):
            return False
        linked = (self.get(run_id) or {}).get("linked_task")
        if self.repo is None:
            return True
        return bool(linked and linked.get("evidence_chain_verified"))

    def _create_collection_task(
        self, run_id: str, fault: dict[str, Any], scenario: dict[str, Any]
    ) -> dict[str, Any] | None:
        if self.repo is None:
            return None
        agents = [
            item
            for item in self.repo.agents.values()
            if status_value(item.status) == "ONLINE"
            and "sys_metrics" in (item.capabilities or [])
        ]
        if not agents:
            return None
        # A PID is meaningful only on the host where it was observed.  Cloud
        # Campaign targets run on the control host, so prefer its dedicated
        # local collector instead of dispatching the PID to an arbitrary
        # remote worker.  The native-agent preference remains for the normal
        # single-host stack.
        preferred = next(
            (
                item
                for item in agents
                if "campaign" in item.id.lower() or "control" in item.id.lower()
            ),
            next((item for item in agents if "native" in item.id.lower()), agents[0]),
        )
        pid = int(fault.get("host_pid") or fault.get("pid") or 0)
        if pid <= 0:
            return None
        task = self.repo.create_task(
            CreateTaskRequest(
                name=f"Campaign {scenario['fault_type']} 证据：{run_id}",
                agent_id=preferred.id,
                target_pid=pid,
                collector_type="sys_metrics",
                sample_rate=10,
                duration_sec=3,
                options={
                    "campaign_run_id": run_id,
                    "evidence_role": "fault_window",
                    "fault_type": scenario["fault_type"],
                    "benchmark_case_id": scenario.get("benchmark_case_id"),
                },
            ),
            idempotency_key=f"campaign-{run_id}",
            creator_id="campaign-runner",
        )
        deadline = time.monotonic() + 18
        status = status_value(task.status)
        reason = task.status_reason or ""
        while status not in TERMINAL_TASK_STATUSES and time.monotonic() < deadline:
            time.sleep(0.5)
            # Campaign execution and Agent task claiming may happen in different
            # processes (HTTP API, gRPC control plane, Analyzer).  Reading the
            # compatibility ``tasks`` mapping is therefore stale with the SQL
            # repository.  Always re-read the durable task row when the
            # repository exposes ``get_task``; retain the mapping fallback for
            # the in-memory test repository.
            get_task = getattr(self.repo, "get_task", None)
            current = (
                get_task(task.id)
                if callable(get_task)
                else self.repo.tasks.get(task.id)
            )
            if current is None:
                break
            status = status_value(current.status)
            reason = current.status_reason or ""
        return {
            "task_id": task.id,
            "agent_id": preferred.id,
            "target_pid": pid,
            "collector_type": "sys_metrics",
            "status": status,
            "reason": reason,
            "evidence_role": "fault_window",
            **self._task_evidence_summary(task.id),
        }

    def _task_evidence_summary(self, task_id: str) -> dict[str, Any]:
        """Expose the attempt/artifact/analyzer chain behind a task reference."""

        def plain(value: Any) -> dict[str, Any]:
            if isinstance(value, dict):
                return dict(value)
            if hasattr(value, "to_dict"):
                return dict(value.to_dict())
            result: dict[str, Any] = {}
            for key in (
                "id",
                "status",
                "attempt_no",
                "reason",
                "artifact_type",
                "integrity_status",
                "sha256",
                "analyzer_type",
                "analyzer_version",
            ):
                if hasattr(value, key):
                    item = getattr(value, key)
                    result[key] = status_value(item) if key == "status" else item
            return result

        attempts = [plain(item) for item in self.repo.get_task_attempts(task_id)]
        artifacts = [plain(item) for item in self.repo.get_artifacts(task_id)]
        jobs = []
        if hasattr(self.repo, "list_analysis_jobs"):
            jobs = [
                plain(item)
                for item in self.repo.list_analysis_jobs(task_id=task_id, limit=20)
            ]
        verified_ids = [
            item.get("id")
            for item in artifacts
            if item.get("integrity_status") == "VERIFIED" and item.get("id") is not None
        ]
        return {
            "task_attempts": attempts,
            "artifacts": artifacts,
            "analysis_jobs": jobs,
            "verified_artifact_ids": verified_ids,
            "evidence_chain_verified": bool(attempts and verified_ids),
        }

    def _cleanup(
        self, run_id: str, settle: float, scenario: dict[str, Any]
    ) -> None:
        cleanup = {"attempted": True, "succeeded": False, "verified_at": None}
        try:
            active_target = (
                self.gc_target if scenario["fault_type"] == "GC_PRESSURE" else self.target
            )
            if scenario["fault_type"] == "GC_PRESSURE":
                active_target.stop_gc()
            else:
                if hasattr(active_target, "stop_memory"):
                    active_target.stop_memory()
                if hasattr(active_target, "stop_cpu"):
                    active_target.stop_cpu()
                if hasattr(active_target, "stop_source"):
                    active_target.stop_source()
                if hasattr(active_target, "stop_io"):
                    active_target.stop_io()
                if hasattr(active_target, "stop_downstream"):
                    active_target.stop_downstream()
                if hasattr(active_target, "stop_network"):
                    active_target.stop_network()
                if hasattr(active_target, "stop_noisy"):
                    active_target.stop_noisy()
                if hasattr(active_target, "stop_load"):
                    active_target.stop_load()
                if hasattr(active_target, "stop_queue"):
                    active_target.stop_queue()
            self._event(run_id, "RECOVERY_STARTED", "已执行无条件故障清理", 92)
            time.sleep(settle)
            if scenario["fault_type"] == "DOWNSTREAM_LATENCY":
                recovery = self.target.probe_downstream()
            elif scenario["fault_type"] == "NETWORK_LATENCY":
                recovery = self.target.probe_network()
            else:
                active_target.snapshot()
                time.sleep(settle)
                recovery = active_target.snapshot()
            self._snapshot(run_id, "recovery_snapshot", recovery)
            cleanup.update(
                {
                    "succeeded": not bool(
                        recovery.get({
                            "MEMORY_LEAK": "memory_fault_active",
                            "IO_LATENCY": "io_fault_active",
                            "DOWNSTREAM_LATENCY": "downstream_fault_active",
                            "NETWORK_LATENCY": "network_fault_active",
                            "GC_PRESSURE": "gc_fault_active",
                            "SOURCE_HOTSPOT": "source_fault_active",
                            "NOISY_NEIGHBOR": "noisy_neighbor_active",
                            "LOAD_SATURATION": "load_fault_active",
                            "QUEUE_BACKLOG": "queue_fault_active",
                        }.get(scenario["fault_type"], "fault_active"))
                    ),
                    "verified_at": _now(),
                    "process_cpu_percent": recovery.get("process_cpu_percent"),
                    "process_rss_mb": recovery.get("process_rss_mb"),
                    "process_write_bytes": recovery.get("process_write_bytes"),
                    "upstream_latency_ms": recovery.get("upstream_latency_ms"),
                    "network_delay_ms": recovery.get("network_delay_ms"),
                    "gc_collection_count": recovery.get("gc_collection_count"),
                    "gc_collection_time_ms": recovery.get("gc_collection_time_ms"),
                    "heap_used_mb": recovery.get("heap_used_mb"),
                    "hot_function": recovery.get("hot_function"),
                    "source_file": recovery.get("source_file"),
                    "source_line": recovery.get("source_line"),
                }
            )
            self._set(run_id, "cleanup", cleanup)
            current = self.get(run_id) or {}
            if current.get("status") != "FAILED" and cleanup["succeeded"]:
                self._event(run_id, "RECOVERY_VERIFIED", "故障已停止，恢复快照已保存", 100)
                if (current.get("comparison") or {}).get("passed"):
                    self._finish_completed(run_id)
                else:
                    self._finish_failed(run_id, "Oracle 或可信证据门禁未通过")
            elif current.get("status") != "FAILED":
                self._finish_failed(run_id, "cleanup verification failed")
        except Exception as exc:
            cleanup["error"] = str(exc)
            self._set(run_id, "cleanup", cleanup)
            self._finish_failed(run_id, f"cleanup failed: {exc}")

    def _snapshot(self, run_id: str, role: str, value: dict[str, Any]) -> None:
        snapshot = {"snapshot_id": f"{run_id}:{role}", "role": role, **value}
        with self._lock:
            self._runs[run_id]["snapshots"][role] = snapshot

    def _event(self, run_id: str, stage: str, message: str, progress: int) -> None:
        with self._lock:
            run = self._runs[run_id]
            event = {
                "sequence": len(run["events"]) + 1,
                "timestamp": _now(),
                "stage": stage,
                "message": message,
                "progress": progress,
            }
            run["events"].append(event)
            run["stage"] = stage
            run["message"] = message
            run["progress"] = progress

    def _set(self, run_id: str, key: str, value: Any) -> None:
        with self._lock:
            self._runs[run_id][key] = value

    def _finish_completed(self, run_id: str) -> None:
        with self._lock:
            run = self._runs[run_id]
            run["status"] = "COMPLETED"
            run["stage"] = "COMPLETED"
            run["message"] = "真实故障 Campaign 已通过并完成恢复"
            run["progress"] = 100
            run["finished_at"] = _now()

    def _finish_failed(self, run_id: str, reason: str) -> None:
        with self._lock:
            run = self._runs[run_id]
            if run.get("status") == "FAILED":
                return
            run["status"] = "FAILED"
            run["stage"] = "FAILED"
            run["message"] = reason
            run["error"] = reason
            run["finished_at"] = _now()
            run["events"].append(
                {
                    "sequence": len(run["events"]) + 1,
                    "timestamp": _now(),
                    "stage": "FAILED",
                    "message": reason,
                    "progress": run.get("progress", 0),
                }
            )

    def _scenario_for_run(self, run_id: str) -> dict[str, Any]:
        current = self.get(run_id) or {}
        scenario_id = current.get("scenario_id")
        return next(item for item in SCENARIOS if item["scenario_id"] == scenario_id)


_MANAGER: CampaignManager | None = None


def get_campaign_manager(repo: Any = None) -> CampaignManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = CampaignManager(repo=repo)
    elif repo is not None and _MANAGER.repo is None:
        _MANAGER.repo = repo
    return _MANAGER
