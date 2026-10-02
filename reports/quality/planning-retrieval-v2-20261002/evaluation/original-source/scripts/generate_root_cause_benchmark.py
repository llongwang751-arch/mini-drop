#!/usr/bin/env python3
"""Generate 540 controlled fault cases and separate private root-cause oracles."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any

from server.app.drop_insight.fault_plaza import SCENARIOS
from server.app.drop_insight.root_cause_benchmark import SCHEMA


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "benchmarks" / "root-cause-v1"
SEED = 20260907

PREFIXES = (
    "值班告警显示",
    "故障窗口内观察到",
    "发布后业务开始出现",
    "稳定流量下服务出现",
    "用户侧持续反馈",
)
NOISE = (
    "链路追踪缺少一个采样片段",
    "仪表盘发生过一次刷新",
    "没有明确的配置变更记录",
    "上游调用方发生过重试",
    "故障时间可能有一分钟偏差",
)
SUFFIXES = (
    "请自主规划多轮取证并寻找反证",
    "不要根据场景名称直接下结论",
    "需要给出可复测的根因证据链",
    "请在两轮工具预算内优先取得高信息量证据",
)

FAMILY_CATEGORY = {
    "CPU": "CPU_HOTSPOT",
    "运行时": "PYTHON_RUNTIME",
    "内存": "MEMORY_PRESSURE",
    "I/O": "IO_LATENCY",
    "资源竞争": "NOISY_NEIGHBOR",
    "负载": "LOAD_SATURATION",
    "队列": "QUEUE_CONGESTION",
    "Go 运行时": "GO_RUNTIME",
    "网络": "NETWORK_DEGRADATION",
    "Java 运行时": "JVM_GC",
    "锁竞争": "LOCK_CONTENTION",
    "下游依赖": "DOWNSTREAM_DEPENDENCY",
    "C++": "CPU_HOTSPOT",
}


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _capability(collector: str) -> str:
    return collector


# 观测语料：按"决定性采集器实际会看到的现象"写成度量/分析器形态。
# 刻意不复制 expected_signals / symptom 的表述，避免观测与评分画像同源
# （旧语料逐字复制 expected_signals，导致文本预测 540/540 全中，见
# diversity audit 与 2026-09-20 口径更正）。占位符由确定性抖动填充，
# 不消耗主随机序列，保证 query 与历史版本逐字节一致。
DECISIVE_OBSERVATIONS: dict[str, list[str]] = {
    "cpu-hotspot": [
        "perf_cpu: self samples {pct1:.1f}% in `_run` (hash digest loop), user-space; kernel {pct2:.1f}%",
        "perf_cpu: top leaf stable across window; chain `_run` -> `_digest_chunk`; no syscall spike",
    ],
    "source-hotspot": [
        "pyspy: line-level self samples concentrate at workload.py:{line} `source_hot_function` ({pct1:.1f}%)",
        "pyspy: source mapping resolves {n} frames to workload.py; no C-extension frame in top list",
    ],
    "memory-pressure": [
        "smaps: Rss {rss1:.0f}MB -> {rss2:.0f}MB across window; Private_Dirty leads; file-backed flat",
        "smaps: growth concentrated in heap anonymous mappings; page-cache share unchanged",
    ],
    "io-write-latency": [
        "ebpf_io: process write {wmb:.0f}MB in window; fsync p95 {p95:.0f}ms; read side idle",
        "ebpf_io: block device await elevated in the same window as process write bytes climbing",
    ],
    "noisy-neighbor": [
        "perf_cpu: no dominant user-space leaf in target (top {pct1:.1f}%); samples spread over runtime frames",
        "perf_cpu: target on-CPU share low despite host saturation; threads mostly runnable-elsewhere",
    ],
    "load-saturation": [
        "perf_cpu: handler frames dominate ({pct1:.1f}%) but flame is flat; no leaf above {pct2:.1f}%",
        "perf_cpu: CPU busy with request-processing shapes; kernel share normal",
    ],
    "queue-backlog": [
        "sys_metrics: producer {pr:.0f}/s vs consumer {cr:.0f}/s; queue depth {qd} rising across window",
        "sys_metrics: consumer CPU {ccpu:.0f}%, process alive; backlog persists with steady producer",
    ],
    "go-cpu-hotspot": [
        "go_pprof: CPU profile top `goCPUHotFunction` {pct1:.1f}% inclusive / {pct2:.1f}% self; source-mapped",
        "go_pprof: GC and syscall frames minor; sample set stable across the window",
    ],
    "go-network-latency": [
        "go_pprof: blocking profile shows {n} goroutines parked on network read (avg wait {dur}ms)",
        "go_pprof: CPU profile flat (top leaf {pct1:.1f}%); waits dominate compute",
    ],
    "go-memory-growth": [
        "smaps: Rss {rss1:.0f}MB -> {rss2:.0f}MB; anonymous mappings lead growth; file-backed flat",
        "smaps: growth tracks allocator regions across GC cycles; swap unused",
    ],
    "go-file-io": [
        "ebpf_io: process write {wmb:.0f}MB; synchronous write path visible; block await p95 {p95:.0f}ms",
        "ebpf_io: device await elevated while process write bytes climb; reads negligible",
    ],
    "java-gc-pressure": [
        "smaps: java heap mapping grows then contracts with collection cycles (saw-tooth), net drift up {rss2:.0f}MB",
        "smaps: eden-scale churn visible as repeated Private_Dirty expansion; other mappings flat",
    ],
    "java-lock-contention": [
        "java_async: lock profile {n} threads blocked on `ReentrantLock` path, avg wait {dur}ms",
        "java_async: parked frames concentrate on one monitor; CPU-sampled frames stay low",
    ],
    "java-downstream-latency": [
        "java_async: wall-clock profile dominated by socket read waits ({pct1:.1f}%); avg {dur}ms per call",
        "java_async: worker threads parked on downstream read; local compute frames minor",
    ],
    "java-offheap-growth": [
        "smaps: Rss {rss1:.0f}MB -> {rss2:.0f}MB while java heap segment grows far less; gap in native mappings",
        "smaps: growth in anonymous native mappings consistent with direct-buffer arena expansion",
    ],
    "java-file-io": [
        "ebpf_io: process write {wmb:.0f}MB; FileChannel.force path visible; fsync p95 {p95:.0f}ms",
        "ebpf_io: device await elevated with synchronous writes; read side negligible",
    ],
    "cpp-cpu-hotspot": [
        "perf_cpu: self samples {pct1:.1f}% in `cpp_cpu_hot_function` (numeric loop), user-space",
        "perf_cpu: leaf stable across window; no futex or syscall concentration",
    ],
    "cpp-lock-contention": [
        "perf_cpu: futex/lock paths concentrate ({pct1:.1f}%); user compute leaf below {pct2:.1f}%",
        "perf_cpu: ctxswitch-heavy shape; waiting dominated by one mutex critical section",
    ],
    "cpp-memory-growth": [
        "smaps: Rss {rss1:.0f}MB -> {rss2:.0f}MB; anonymous mappings lead; bounded vs host total",
        "smaps: growth step-wise with retention phase; file-backed and shared flat",
    ],
    "cpp-file-io": [
        "ebpf_io: process write {wmb:.0f}MB; write/fdatasync syscall path visible; await p95 {p95:.0f}ms",
        "ebpf_io: device await elevated during synchronous write bursts; reads idle",
    ],
    "cpp-downstream-latency": [
        "perf_cpu: socket wait paths visible ({pct1:.1f}%); compute leaf below {pct2:.1f}%",
        "perf_cpu: on-CPU time moderate; latency dominated by loopback round trips",
    ],
}

# 非决定性采集器的背景观测：全库共享、不携带场景区分信息。
BACKGROUND_OBSERVATIONS: dict[str, list[str]] = {
    "sys_metrics": [
        "sys_metrics: host load {load:.1f} on {ncpu} CPUs; memory headroom {memh:.0f}MB; target RSS {rss1:.0f}MB",
    ],
    "perf_cpu": [
        "perf_cpu: top self sample {pct1:.1f}% in runtime allocator frames; kernel {pct2:.1f}%; no pathological leaf",
    ],
    "pyspy": [
        "pyspy: top frame {pct1:.1f}% in idle/wait paths; samples spread; no concentrated compute leaf",
    ],
    "memory_smaps": [
        "smaps: Rss stable at {rss1:.0f}MB across window; Private_Dirty flat; no anomalous mapping growth",
    ],
    "ebpf_io": [
        "ebpf_io: process I/O negligible (write {wkb:.0f}KB); block device await {aw:.1f}ms near baseline",
    ],
    "go_pprof": [
        "go_pprof: CPU profile flat (top leaf {pct1:.1f}%); no blocking burst above baseline",
    ],
    "java_async": [
        "java_async: {event} profile shows no path above {pct1:.1f}%; frames spread across the worker pool",
    ],
    "continuous_perf": [
        "continuous_perf: window samples flat; no dominant leaf above {pct1:.1f}%",
    ],
}

_HARD_CONFOUNDER = (
    "note: unattributed host background burst in the same window; "
    "attribution requires process-scoped evidence"
)


def _jitter(scenario, index: int) -> dict[str, Any]:
    rng = random.Random(f"{scenario.scenario_id}:{index}")
    return {
        "pct1": rng.uniform(58.0, 96.0),
        "pct2": rng.uniform(1.5, 14.0),
        "line": rng.randrange(120, 260),
        "n": rng.randrange(6, 42),
        "rss1": rng.uniform(180.0, 260.0),
        "rss2": rng.uniform(262.0, 340.0),
        "wmb": rng.uniform(40.0, 220.0),
        "wkb": rng.uniform(4.0, 60.0),
        "p95": rng.uniform(45.0, 180.0),
        "aw": rng.uniform(0.4, 2.2),
        "load": rng.uniform(2.0, 9.0),
        "ncpu": rng.randrange(4, 17),
        "memh": rng.uniform(900.0, 3600.0),
        "pr": rng.randrange(220, 460),
        "cr": rng.randrange(60, 150),
        "qd": rng.randrange(900, 9000),
        "ccpu": rng.randrange(22, 61),
        "dur": rng.randrange(180, 420),
        "hmb": rng.uniform(120.0, 240.0),
        "hmb2": rng.uniform(242.0, 380.0),
        "event": rng.choice(("alloc", "wall", "cpu")),
    }


def _observations(scenario, index: int) -> dict[str, list[str]]:
    collectors = list(scenario.recommended_collectors)
    decisive = collectors[1] if len(collectors) > 1 else collectors[0]
    values: dict[str, list[str]] = {collector: [] for collector in collectors}
    fmt = _jitter(scenario, index)
    for line in DECISIVE_OBSERVATIONS.get(scenario.scenario_id, []):
        values[decisive].append(line.format(**fmt))
    for collector in collectors:
        if collector == decisive:
            continue
        for line in BACKGROUND_OBSERVATIONS.get(collector, []):
            values[collector].append(line.format(**fmt))
        values[collector].append(f"{scenario.target_runtime} 受控采集窗口 {index + 1} 已完成")
    if index == 25:
        values[decisive].append(_HARD_CONFOUNDER)
    return values


def build(output: Path) -> dict[str, Any]:
    rng = random.Random(SEED)
    public_cases: list[dict[str, Any]] = []
    oracles: list[dict[str, Any]] = []
    # 21 * 25 = 525; first 15 scenarios receive one extra hard/noisy variant.
    for scenario_index, scenario in enumerate(SCENARIOS):
        variant_count = 26 if scenario_index < 15 else 25
        for index in range(variant_count):
            case_id = f"RC-{scenario_index + 1:02d}-{index + 1:03d}"
            hard = index == 25
            symptom = scenario.symptom
            query = "，".join(
                (
                    PREFIXES[index % len(PREFIXES)],
                    symptom if not hard else f"{scenario.target_runtime} 服务出现相似资源异常，需要排除多个竞争原因",
                    NOISE[rng.randrange(len(NOISE))],
                    SUFFIXES[(index * 3) % len(SUFFIXES)],
                )
            )
            observations = _observations(scenario, index)
            collectors = list(scenario.recommended_collectors)
            decisive_collector = collectors[1] if len(collectors) > 1 else collectors[0]
            public_cases.append(
                {
                    "case_id": case_id,
                    "variant_family": "HARD_CONFOUNDING" if hard else ("NOISY" if index % 3 else "PARAPHRASE"),
                    "category": FAMILY_CATEGORY.get(scenario.family, "CPU_HOTSPOT"),
                    "family_hint": scenario.family,
                    "query": query,
                    "target": {
                        "service": f"controlled-{scenario.target_runtime.casefold()}-{index % 4}",
                        "environment": "controlled-replay",
                        "runtime": scenario.target_runtime,
                        "collector_capabilities": sorted({_capability(item) for item in collectors}),
                    },
                    "observations_by_collector": observations,
                }
            )
            oracles.append(
                {
                    "case_id": case_id,
                    "root_cause_id": scenario.scenario_id,
                    "expected_skill_id": scenario.related_skill,
                    "decisive_collector": decisive_collector,
                    "expected_signals": list(scenario.expected_signals),
                    "minimum_diagnosis_rounds": scenario.minimum_diagnosis_rounds,
                    "truth_source": "server/app/drop_insight/fault_plaza.py",
                }
            )

    if len(public_cases) != 540:
        raise AssertionError(f"expected 540 cases, got {len(public_cases)}")
    public = {
        "schema": SCHEMA,
        "version": "2026.09.07-root-cause-v1",
        "generator_seed": SEED,
        "case_count": len(public_cases),
        "fault_contract_count": len(SCENARIOS),
        "source_policy": {
            "legacy_case_files_read": False,
            "public_oracle_separation": True,
            "fault_contract_source": "server/app/drop_insight/fault_plaza.py",
            "execution_semantics": "DETERMINISTIC_CONTROLLED_REPLAY_NOT_540_LIVE_INJECTIONS",
        },
        "cases": public_cases,
    }
    private = {"schema": SCHEMA, "version": public["version"], "oracles": oracles}
    _write(output / "public" / "cases.json", public)
    _write(output / "private" / "oracles.json", private)
    manifest = {
        "schema": SCHEMA,
        "version": public["version"],
        "case_count": len(public_cases),
        "paired_ab_case_count": 500,
        "fault_contract_count": len(SCENARIOS),
        "files": {
            "public/cases.json": _digest(output / "public" / "cases.json"),
            "private/oracles.json": _digest(output / "private" / "oracles.json"),
        },
    }
    _write(output / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(build(args.output.resolve()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
