"""Allow-listed fault-lab controls used by the interview demo.

The diagnosis worker never accepts a URL or command from the browser.  Every
scenario maps to a fixed endpoint on one explicitly configured demo service.
Supporting several runtimes here must not turn the page into a generic proxy:
the browser still supplies only ``scenario_id`` and a bounded duration.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request


@dataclass(frozen=True)
class FaultScenario:
    scenario_id: str
    title: str
    family: str
    symptom: str
    start_path: str
    stop_path: str
    active_field: str
    recommended_collectors: tuple[str, ...]
    expected_signals: tuple[str, ...]
    diagnosis_query: str
    related_skill: str
    start_defaults: dict[str, Any]
    acceptance_level: str = "FAULT_INJECTION_VERIFIED"
    supports_skill_ab: bool = True
    lab_key: str = "python"
    target_runtime: str = "Python"
    minimum_diagnosis_rounds: int = 3
    investigation_stages: tuple[str, ...] = (
        "低开销系统初筛",
        "运行时或内核专项取证",
        "寻找反证并观察恢复窗口",
    )

    def public_dict(
        self,
        snapshot: dict[str, Any] | None = None,
        *,
        available: bool = True,
        unavailable_reason: str = "",
    ) -> dict[str, Any]:
        snapshot = snapshot or {}
        return {
            "scenario_id": self.scenario_id,
            "title": self.title,
            "family": self.family,
            "symptom": self.symptom,
            "active": bool(snapshot.get(self.active_field, False)),
            "recommended_collectors": list(self.recommended_collectors),
            "expected_signals": list(self.expected_signals),
            "diagnosis_query": self.diagnosis_query,
            "related_skill": self.related_skill,
            "lab_key": self.lab_key,
            "target_runtime": self.target_runtime,
            "available": available,
            "unavailable_reason": unavailable_reason,
            "minimum_diagnosis_rounds": self.minimum_diagnosis_rounds,
            "investigation_stages": list(self.investigation_stages),
            "duration_options_seconds": [30, 60, 120],
            "supports_skill_ab": self.supports_skill_ab,
            "acceptance_level": self.acceptance_level,
            "skill_ab_unavailable_reason": (
                ""
                if self.supports_skill_ab
                else f"关联 Skill {self.related_skill} 尚未发布；可先使用“启动并诊断”演示动态探索。"
            ),
            "safety": {
                "allow_listed": True,
                "auto_stop": True,
                "max_duration_seconds": 300,
                "production_target": False,
            },
        }


SCENARIOS: tuple[FaultScenario, ...] = (
    FaultScenario(
        "cpu-hotspot",
        "CPU 计算热点",
        "CPU",
        "单进程 CPU 持续升高，并出现稳定的用户态热点函数",
        "/faults/cpu/start",
        "/faults/cpu/stop",
        "fault_active",
        ("sys_metrics", "perf_cpu", "pyspy"),
        ("目标进程 CPU 升高", "调用栈出现集中热点", "停止故障后 CPU 回落"),
        "诊断 demo 环境中的 python-hotspot 服务 CPU 持续升高。先做系统初筛，再定位调用栈热点，最后寻找 I/O 等待和同机争抢反证并观察停止后的恢复；不要在第一轮直接下结论。",
        "cpu-hotspot-diagnosis",
        {},
    ),
    FaultScenario(
        "source-hotspot",
        "Python 源码热点",
        "运行时",
        "Python 业务函数占用 CPU，可定位到稳定的文件、函数和行号",
        "/faults/source/start",
        "/faults/source/stop",
        "source_fault_active",
        ("sys_metrics", "pyspy"),
        ("Python 帧占比集中", "source_hot_function 成为 TopN", "源码映射可解析"),
        "诊断 demo 环境中的 python-hotspot 服务，先确认 CPU 异常，再定位源码级热点函数，最后用系统指标排除 I/O 和宿主机争抢；至少经过两类独立证据再下结论。",
        "python-runtime-diagnosis",
        {},
        acceptance_level="LIVE_DIAGNOSIS_VERIFIED",
    ),
    FaultScenario(
        "memory-pressure",
        "进程内存增长",
        "内存",
        "目标进程持续保留内存，RSS 在短时间内明显增长",
        "/faults/memory/start",
        "/faults/memory/stop",
        "memory_fault_active",
        ("sys_metrics", "memory_smaps"),
        ("RSS/PSS 增长", "retained memory 增长", "停止并清理后内存回落"),
        "诊断 demo 环境中的 python-hotspot 服务内存持续增长。先确认 RSS/PSS 趋势，再区分进程对象保留、页缓存和系统内存压力，最后检查停止故障后的恢复窗口；不要凭单点 RSS 下结论。",
        "memory-growth-diagnosis",
        {"megabytes": 96},
    ),
    FaultScenario(
        "io-write-latency",
        "同步写入压力",
        "I/O",
        "目标进程持续同步写文件，写入字节和 I/O 等待升高",
        "/faults/io/start",
        "/faults/io/stop",
        "io_fault_active",
        ("sys_metrics", "ebpf_io"),
        ("write_bytes 增长", "块设备延迟升高", "CPU 热点不能单独解释现象"),
        "诊断 demo 环境中的 python-hotspot 服务写入变慢。先用系统指标确认 I/O 方向，再采集块设备延迟和进程写入证据，随后寻找 CPU 热点或同机 I/O 争抢反证，并验证停止后的恢复。",
        "io-latency-diagnosis",
        {},
    ),
    FaultScenario(
        "noisy-neighbor",
        "同机噪声邻居",
        "资源竞争",
        "另一个同宿主机进程争抢 CPU，目标进程自身并无主导热点",
        "/faults/noisy/start",
        "/faults/noisy/stop",
        "noisy_neighbor_active",
        ("sys_metrics", "perf_cpu"),
        ("宿主机 CPU 饱和", "目标栈无主导热点", "同机 peer CPU ticks 增长"),
        "诊断 demo 环境中的 python-hotspot 服务吞吐下降。先比较目标进程与宿主机 CPU，再定位目标自身调用栈，最后寻找同机其他进程争抢的反证或支持证据，并观察停止后的恢复。",
        "same-host-contention-diagnosis",
        {},
    ),
    FaultScenario(
        "load-saturation",
        "入口负载饱和",
        "负载",
        "请求到达速度超过处理速度，拒绝数和延迟同时升高",
        "/faults/load/start",
        "/faults/load/stop",
        "load_fault_active",
        ("sys_metrics", "perf_cpu"),
        ("offered RPS 高于 completed RPS", "rejected requests 增长", "CPU 使用率升高"),
        "诊断 demo 环境中的 python-hotspot 入口请求被拒绝且吞吐下降。先确认到达率与完成率，再定位目标 CPU 路径，最后区分入口负载饱和、代码热点和下游等待，并观察停止后的恢复。",
        "load-saturation-diagnosis",
        {},
        supports_skill_ab=False,
    ),
    FaultScenario(
        "queue-backlog",
        "生产消费队列堆积",
        "队列",
        "生产速率高于消费速率，队列深度和排队延迟持续增长",
        "/faults/queue/start",
        "/faults/queue/stop",
        "queue_fault_active",
        ("sys_metrics",),
        ("producer rate 高于 consumer rate", "queue depth 增长", "停止注入后积压消退"),
        "诊断 demo 环境中的 python-hotspot 任务队列持续堆积。先比较生产和消费速率，再检查消费者资源与热点，最后排除突发流量和下游等待，并验证停止注入后积压是否消退。",
        "queue-backlog-diagnosis",
        {},
    ),
    FaultScenario(
        "go-cpu-hotspot",
        "Go 服务 CPU 热点",
        "Go 运行时",
        "Go 进程 CPU 持续升高，需要区分业务计算、运行时开销和系统等待",
        "/faults/cpu/start",
        "/faults/cpu/stop",
        "cpu_fault_active",
        ("sys_metrics", "go_pprof", "perf_cpu"),
        ("目标进程 CPU 升高", "pprof 热点集中", "系统等待不足以解释 CPU"),
        "诊断 demo 环境中的 go-hotspot 服务 CPU 持续升高。第一轮做系统初筛，第二轮用 Go pprof 定位热点，第三轮用系统或 perf 证据寻找运行时等待和 I/O 反证，并确认停止后的恢复。",
        "go-runtime-diagnosis",
        {},
        acceptance_level="LIVE_DIAGNOSIS_VERIFIED",
        lab_key="go",
        target_runtime="Go",
    ),
    FaultScenario(
        "go-network-latency",
        "Go 网络等待与下游慢响应",
        "网络",
        "请求处理时间升高但 CPU 不一定饱和，需要区分本地热点、网络等待和下游变慢",
        "/faults/network/start",
        "/faults/network/stop",
        "network_fault_active",
        ("sys_metrics", "go_pprof"),
        ("请求等待时间升高", "CPU 不能单独解释延迟", "停止注入后请求时延回落"),
        "诊断 demo 环境中的 go-hotspot 服务出现网络等待和下游慢响应。先检查端点 CPU 与系统指标，再查看 Go 阻塞栈，最后寻找本地计算热点反证并验证恢复窗口，不要只凭一次超时判断网络故障。",
        "network-degradation-diagnosis",
        {"delay_ms": 240},
        lab_key="go",
        target_runtime="Go",
    ),
    FaultScenario(
        "go-memory-growth",
        "Go 堆内存持续增长",
        "内存",
        "Go 服务分阶段保留堆对象，RSS 与堆内存同步增长，但总量被安全上限约束",
        "/faults/memory/start",
        "/faults/memory/stop",
        "memory_fault_active",
        ("sys_metrics", "memory_smaps", "go_pprof"),
        ("Go heap inuse 持续增长", "RSS/PSS 同向上升", "停止并触发回收后内存回落"),
        "诊断 demo 环境中的 go-hotspot 服务内存持续增长。第一轮确认进程与宿主机内存趋势，第二轮用 Go heap profile 区分对象保留和系统页缓存，第三轮寻找 swap、I/O 或短命分配反证并观察停止后的恢复；不要把一次 RSS 上升直接当成泄漏。",
        "go-runtime-diagnosis",
        {"megabytes": 96},
        lab_key="go",
        target_runtime="Go",
    ),
    FaultScenario(
        "go-file-io",
        "Go 同步文件写入",
        "I/O",
        "Go 服务反复执行固定临时文件写入与同步落盘，制造可恢复的块设备等待",
        "/faults/io/start",
        "/faults/io/stop",
        "io_fault_active",
        ("sys_metrics", "ebpf_io", "go_pprof"),
        ("进程 write bytes 持续增长", "同步写调用与 I/O 等待可见", "停止后写入速率和等待回落"),
        "诊断 demo 环境中的 go-hotspot 服务文件写入变慢。第一轮确认 CPU、I/O wait 与进程写入量，第二轮用 eBPF 定位同步写和块设备延迟，第三轮检查 Go 阻塞栈并排除 CPU 热点、网络等待与同机争抢，最后验证停止后的恢复。",
        "io-latency-diagnosis",
        {},
        lab_key="go",
        target_runtime="Go",
    ),
    FaultScenario(
        "java-gc-pressure",
        "Java 分配风暴与 GC 压力",
        "Java 运行时",
        "Java 服务持续制造短命对象，堆呈锯齿并可能出现 GC 与延迟抖动",
        "/faults/gc/start",
        "/faults/gc/stop",
        "gc_fault_active",
        ("sys_metrics", "memory_smaps", "java_async"),
        ("分配量快速增长", "GC 次数增加", "堆使用呈回收锯齿而非单调泄漏"),
        "诊断 demo 环境中进程名为 java 的 Java 服务出现 GC 和延迟抖动。先确认系统与堆趋势，再采集 JVM 调用栈定位分配路径，最后排除对象长期保留、锁等待和纯 CPU 热点，并验证停止后的恢复。",
        "gc-pressure-diagnosis",
        {},
        acceptance_level="LIVE_DIAGNOSIS_VERIFIED",
        lab_key="java",
        target_runtime="Java",
    ),
    FaultScenario(
        "java-lock-contention",
        "Java 锁竞争",
        "锁竞争",
        "多个 Java 线程竞争同一临界区，吞吐下降且阻塞/上下文切换增加",
        "/faults/lock/start",
        "/faults/lock/stop",
        "lock_fault_active",
        ("sys_metrics", "java_async", "perf_cpu"),
        ("锁等待次数增长", "调用栈出现 ReentrantLock/park", "CPU 热点不能单独解释吞吐"),
        "诊断 demo 环境中进程名为 java 的 Java 服务吞吐下降并疑似锁竞争。先比较 CPU、线程和上下文切换，再用 JVM Profile 定位锁路径，最后寻找 GC、I/O 或纯计算热点反证并观察恢复。",
        "lock-contention-diagnosis",
        {},
        lab_key="java",
        target_runtime="Java",
    ),
    FaultScenario(
        "java-downstream-latency",
        "Java 下游依赖慢响应",
        "下游依赖",
        "Java 请求线程等待受控下游响应，端到端延迟升高但本地 CPU 保持中低水平",
        "/faults/downstream/start",
        "/faults/downstream/stop",
        "downstream_fault_active",
        ("sys_metrics", "java_async"),
        ("请求等待时间升高", "线程栈以等待/park 为主", "本地 CPU 热点不足以解释延迟"),
        "诊断 demo 环境中进程名为 java 的 Java 服务端到端延迟升高。先检查本实例资源，再观察等待线程路径，随后对比下游响应与本地计算反证，最后验证停止故障后的恢复。",
        "dependency-latency-diagnosis",
        {"delay_ms": 260},
        lab_key="java",
        target_runtime="Java",
    ),
    FaultScenario(
        "java-offheap-growth",
        "Java 堆外内存持续增长",
        "内存",
        "Java 服务分阶段保留 DirectByteBuffer，进程 RSS 上升但 Java 堆指标不能完整解释",
        "/faults/offheap/start",
        "/faults/offheap/stop",
        "offheap_fault_active",
        ("sys_metrics", "memory_smaps", "java_async"),
        ("RSS/PSS 持续增长", "匿名内存占比增加", "Java 堆增幅小于进程 RSS 增幅"),
        "诊断 demo 环境中进程名为 java 的 Java 服务进程内存持续增长。第一轮对比 RSS/PSS 与 JVM 堆趋势，第二轮检查 Native/DirectByteBuffer 路径和内存映射，第三轮排除 GC 抖动、页缓存与宿主机压力并观察停止后的恢复；必须说明堆内与堆外证据差异。",
        "memory-growth-diagnosis",
        {"megabytes": 96},
        lab_key="java",
        target_runtime="Java",
    ),
    FaultScenario(
        "java-file-io",
        "Java 同步文件写入",
        "I/O",
        "Java 服务使用 FileChannel 强制同步固定临时文件，写入延迟和 I/O 等待升高",
        "/faults/io/start",
        "/faults/io/stop",
        "io_fault_active",
        ("sys_metrics", "ebpf_io", "java_async"),
        ("FileChannel 写入累计增长", "force/fsync 路径可见", "CPU 与 GC 不能单独解释等待"),
        "诊断 demo 环境中进程名为 java 的 Java 服务文件写入变慢。第一轮检查系统 I/O 与进程写入趋势，第二轮用 eBPF 和 JVM 栈定位 FileChannel.force 路径，第三轮寻找 GC、锁竞争、CPU 热点和下游等待反证，最后验证停止后的恢复。",
        "io-latency-diagnosis",
        {},
        lab_key="java",
        target_runtime="Java",
    ),
    FaultScenario(
        "cpp-cpu-hotspot",
        "C++ 计算热点",
        "C++",
        "C++ 进程出现稳定计算热点，需要区分业务循环、锁自旋和系统调用开销",
        "/faults/cpu/start",
        "/faults/cpu/stop",
        "cpu_fault_active",
        ("sys_metrics", "perf_cpu", "continuous_perf"),
        ("目标 CPU 升高", "perf 热点集中在固定函数", "锁等待和 I/O 不占主导"),
        "诊断 demo 环境中的 cpp-hotspot 服务 CPU 持续升高。先做系统初筛，再用 perf 定位计算热点，最后寻找锁竞争、I/O 等待和同机争抢反证，并确认停止后的恢复。",
        "cpp-runtime-diagnosis",
        {},
        acceptance_level="LIVE_DIAGNOSIS_VERIFIED",
        lab_key="cpp",
        target_runtime="C++",
    ),
    FaultScenario(
        "cpp-lock-contention",
        "C++ 互斥锁竞争",
        "锁竞争",
        "多个 C++ 线程争抢同一互斥锁，等待时间和上下文切换上升",
        "/faults/lock/start",
        "/faults/lock/stop",
        "lock_fault_active",
        ("sys_metrics", "perf_cpu", "continuous_perf"),
        ("互斥锁等待累计增长", "futex/锁调用栈集中", "降低竞争后吞吐恢复"),
        "诊断 demo 环境中的 cpp-hotspot 服务吞吐下降。先用系统指标区分 CPU 计算和阻塞等待，再用 perf 定位 mutex/futex 路径，最后寻找 I/O 或纯计算热点反证并观察恢复。",
        "lock-contention-diagnosis",
        {},
        lab_key="cpp",
        target_runtime="C++",
    ),
    FaultScenario(
        "cpp-memory-growth",
        "C++ 有界内存保留",
        "内存",
        "C++ 进程分阶段保留匿名内存，RSS 上升但不会耗尽宿主机",
        "/faults/memory/start",
        "/faults/memory/stop",
        "memory_fault_active",
        ("sys_metrics", "memory_smaps"),
        ("RSS/PSS 持续增长", "匿名映射占比增加", "停止并释放后内存回落"),
        "诊断 demo 环境中的 cpp-hotspot 服务 RSS 持续增长。先确认趋势，再区分匿名内存保留、页缓存和宿主机压力，最后观察停止释放后的恢复，不能凭单点 RSS 断言泄漏。",
        "memory-growth-diagnosis",
        {"megabytes": 96},
        lab_key="cpp",
        target_runtime="C++",
    ),
    FaultScenario(
        "cpp-file-io",
        "C++ 同步文件写入",
        "I/O",
        "C++ 服务对固定临时文件执行有界 write 与 fdatasync，制造可停止的磁盘等待",
        "/faults/io/start",
        "/faults/io/stop",
        "io_fault_active",
        ("sys_metrics", "ebpf_io", "perf_cpu"),
        ("进程写入字节增长", "write/fdatasync 系统调用路径可见", "停止后 I/O wait 回落"),
        "诊断 demo 环境中的 cpp-hotspot 服务文件 I/O 变慢。第一轮确认进程写入量与 I/O wait，第二轮用 eBPF 定位 write/fdatasync 和块设备延迟，第三轮用 perf 排除计算热点、锁竞争与内存压力，并验证停止后的恢复。",
        "io-latency-diagnosis",
        {},
        lab_key="cpp",
        target_runtime="C++",
    ),
    FaultScenario(
        "cpp-downstream-latency",
        "C++ 下游响应变慢",
        "下游依赖",
        "C++ 请求线程等待固定回环下游，端到端延迟上升但本地 CPU 不一定饱和",
        "/faults/downstream/start",
        "/faults/downstream/stop",
        "downstream_fault_active",
        ("sys_metrics", "perf_cpu", "continuous_perf"),
        ("回环请求延迟升高", "socket 等待路径可见", "本地计算热点不足以解释时延"),
        "诊断 demo 环境中的 cpp-hotspot 服务请求延迟升高。第一轮比较 CPU、运行队列与请求延迟，第二轮用 perf/持续采样识别 socket 等待路径，第三轮排除锁竞争、同步文件 I/O 和纯计算热点，并验证关闭下游延迟后的恢复。",
        "dependency-latency-diagnosis",
        {"delay_ms": 260},
        lab_key="cpp",
        target_runtime="C++",
    ),
)

_BY_ID = {scenario.scenario_id: scenario for scenario in SCENARIOS}


class FaultPlazaError(RuntimeError):
    """A safe, user-facing failure from the controlled fault lab."""


_LAB_ENVIRONMENTS = {
    "python": "MINI_DROP_FAULT_LAB_URL",
    "go": "MINI_DROP_FAULT_LAB_GO_URL",
    "java": "MINI_DROP_FAULT_LAB_JAVA_URL",
    "cpp": "MINI_DROP_FAULT_LAB_CPP_URL",
}


def _base_url(lab_key: str = "python") -> str:
    environment = _LAB_ENVIRONMENTS.get(lab_key)
    if environment is None:
        raise FaultPlazaError("受控故障实验室类型不在白名单中")
    return os.getenv(environment, "").strip().rstrip("/")


def _request_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    lab_key: str = "python",
) -> dict[str, Any]:
    base_url = _base_url(lab_key)
    if not base_url:
        environment = _LAB_ENVIRONMENTS[lab_key]
        raise FaultPlazaError(f"受控故障实验室未启用：未配置 {environment}")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib_request.Request(
        f"{base_url}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib_request.urlopen(request, timeout=3) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib_error.URLError, json.JSONDecodeError) as exc:
        raise FaultPlazaError(f"受控故障实验室不可用: {exc}") from exc
    if not isinstance(parsed, dict):
        raise FaultPlazaError("受控故障实验室返回了无效响应")
    return parsed


def get_fault_plaza() -> dict[str, Any]:
    labs: dict[str, dict[str, Any]] = {}
    for lab_key, environment in _LAB_ENVIRONMENTS.items():
        if not _base_url(lab_key):
            labs[lab_key] = {
                "status": "DISABLED",
                "reason": f"未配置 {environment}",
                "snapshot": {},
            }
            continue
        try:
            labs[lab_key] = {
                "status": "READY",
                "reason": "",
                "snapshot": _request_json("GET", "/snapshot", lab_key=lab_key),
            }
        except FaultPlazaError as exc:
            labs[lab_key] = {
                "status": "UNREACHABLE",
                "reason": str(exc),
                "snapshot": {},
            }
    ready_count = sum(item["status"] == "READY" for item in labs.values())
    configured_count = sum(item["status"] != "DISABLED" for item in labs.values())
    status = "READY" if ready_count else ("UNREACHABLE" if configured_count else "DISABLED")
    unavailable = [key for key, item in labs.items() if item["status"] != "READY"]
    reason = "" if not unavailable else "部分故障运行时未就绪：" + "、".join(unavailable)
    return {
        "status": status,
        "reason": reason,
        "lab_kind": "ALLOW_LISTED_CONTROLLED_FAULTS",
        "production_safe": False,
        "labs": {
            key: {"status": item["status"], "reason": item["reason"]}
            for key, item in labs.items()
        },
        "scenarios": [
            scenario.public_dict(
                labs[scenario.lab_key]["snapshot"],
                available=labs[scenario.lab_key]["status"] == "READY",
                unavailable_reason=labs[scenario.lab_key]["reason"],
            )
            for scenario in SCENARIOS
        ],
    }


def start_fault_scenario(scenario_id: str, duration_seconds: int) -> dict[str, Any]:
    scenario = _BY_ID.get(scenario_id)
    if scenario is None:
        raise ValueError("fault scenario not found")
    bounded_duration = min(max(int(duration_seconds), 15), 300)
    payload = {**scenario.start_defaults, "duration_seconds": bounded_duration}
    snapshot = _request_json(
        "POST", scenario.start_path, payload, lab_key=scenario.lab_key
    )
    return {
        "scenario": scenario.public_dict(snapshot),
        "status": "RUNNING",
        "started_at": datetime.now(UTC).isoformat(),
        "auto_stop_seconds": bounded_duration,
        "diagnosis_request": {
            "query": scenario.diagnosis_query,
            "mode": "AUTONOMOUS",
            "auto_scope": True,
            "skill_policy": "AUTO",
            "budget": {
                "min_diagnosis_rounds": scenario.minimum_diagnosis_rounds,
                "max_diagnosis_rounds": 4,
            },
        },
        "minimum_diagnosis_rounds": scenario.minimum_diagnosis_rounds,
        "investigation_stages": list(scenario.investigation_stages),
    }


def stop_fault_scenario(scenario_id: str) -> dict[str, Any]:
    scenario = _BY_ID.get(scenario_id)
    if scenario is None:
        raise ValueError("fault scenario not found")
    snapshot = _request_json(
        "POST", scenario.stop_path, {}, lab_key=scenario.lab_key
    )
    return {
        "scenario": scenario.public_dict(snapshot),
        "status": "STOPPED",
        "stopped_at": datetime.now(UTC).isoformat(),
    }
