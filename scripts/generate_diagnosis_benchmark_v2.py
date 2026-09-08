#!/usr/bin/env python3
"""Generate a new, blinded 540-case diagnosis/Skill benchmark.

The generator is the source of truth.  It intentionally does not read the
retired ``benchmarks/cases`` or ``tests/fixtures`` datasets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "benchmarks" / "diagnosis-v2"
SEED = 20260905
SCHEMA = "mini-drop.diagnosis-benchmark.v2"

PREFIXES = (
    "生产告警显示",
    "值班同学观察到",
    "发布后十分钟",
    "流量保持稳定时",
    "during the incident",
    "the service dashboard reports",
)
SUFFIXES = (
    "请让系统自主选择下一步取证",
    "需要证据和反证，不要直接猜根因",
    "请给出能够复测的调查路径",
    "先做低开销检查，再决定是否深入采样",
)
NOISE = (
    "监控面板刷新过一次",
    "trace id 暂时不可用",
    "未收到明确的发布回滚通知",
    "一个调用方发生过重试",
    "值班记录可能晚一分钟",
)

FAMILY_TEXT = {
    "cpu-hotspot-diagnosis": (
        "CPU_HOTSPOT",
        ("CPU 持续升高且业务函数出现热点", "处理器饱和并伴随 busy loop", "序列化路径消耗大量 cpu"),
    ),
    "dependency-latency-diagnosis": (
        "DOWNSTREAM_DEPENDENCY",
        ("本机资源稳定但下游 RPC 的 P99 明显升高", "依赖服务响应变慢", "upstream latency follows downstream timeout"),
    ),
    "fd-leak-diagnosis": (
        "FD_LEAK",
        ("文件描述符持续增长并接近上限", "socket leak 最终出现 too many open files", "连接关闭后 fd 数量仍不回落"),
    ),
    "gc-pressure-diagnosis": (
        "JVM_GC",
        ("JVM full GC 频率和停顿同时上升", "heap 分配过快导致垃圾回收抖动", "gc pause correlates with latency"),
    ),
    "io-latency-diagnosis": (
        "IO_LATENCY",
        ("磁盘长尾延迟和 iowait 同时升高", "fsync 阻塞并伴随 writeback", "IOPS 正常但单次 I/O 尾延迟异常"),
    ),
    "lock-contention-diagnosis": (
        "LOCK_CONTENTION",
        ("大量线程在 mutex 和 futex 上等待", "自旋锁竞争导致上下文切换激增", "临界区变长后吞吐下降"),
    ),
    "memory-growth-diagnosis": (
        "MEMORY_PRESSURE",
        ("RSS 和 PSS 持续增长并开始使用 swap", "进程疑似 memory leak", "缓存清理后内存仍不回落并接近 OOM"),
    ),
    "network-degradation-diagnosis": (
        "NETWORK_DEGRADATION",
        ("TCP 重传和 RTT 同时上升", "跨节点网络出现丢包和 timeout", "连接重置导致请求尾延迟抖动"),
    ),
    "python-runtime-diagnosis": (
        "PYTHON_RUNTIME",
        ("Python 事件循环被 busy loop 阻塞", "py-spy 显示 GIL 相关用户态热点", "协程调度延迟和 Python CPU 同时升高"),
    ),
}

GENERIC_NEGATIVES = (
    "服务有点慢但没有任何可复现指标",
    "用户说体验不好，时间窗口和目标都不确定",
    "页面偶尔刷新，暂时没有 CPU 内存网络信号",
    "请求失败一次，所有资源图表看起来正常",
    "希望直接告诉我根因，但还没有采集证据",
)

OFFICIAL_REFERENCES = [
    {
        "name": "Microsoft AIOpsLab",
        "url": "https://github.com/microsoft/AIOpsLab",
        "used_for": "separating task, fault, workload, agent action trace, and evaluator in the live benchmark layer",
    },
    {
        "name": "RCAEval",
        "url": "https://github.com/phamquiluan/RCAEval",
        "used_for": "multi-fault taxonomy and separate root-cause service/indicator evaluation in the live evidence layer",
    },
    {
        "name": "OpenTelemetry Demo feature flags",
        "url": "https://opentelemetry.io/docs/demo/feature-flags/",
        "used_for": "repeatable observable demo faults and guided troubleshooting scenarios",
    },
    {
        "name": "Chaos Mesh physical machine chaos",
        "url": "https://chaos-mesh.org/docs/simulate-physical-machine-chaos/",
        "used_for": "bounded-duration and explicitly scoped fault-injection safety controls",
    },
    {
        "name": "Linux perf security",
        "url": "https://docs.kernel.org/admin-guide/perf-security.html",
        "used_for": "capability and least-privilege scenario boundaries",
    },
    {
        "name": "Grafana Pyroscope continuous profiling",
        "url": "https://grafana.com/docs/pyroscope/latest/",
        "used_for": "continuous-versus-on-demand profiling scenarios",
    },
    {
        "name": "gperftools CPU profiler",
        "url": "https://gperftools.github.io/gperftools/cpuprofile.html",
        "used_for": "C/C++ user-space profiler scenarios",
    },
    {
        "name": "LangGraph persistence",
        "url": "https://docs.langchain.com/oss/python/langgraph/persistence",
        "used_for": "durable diagnosis and replay boundaries",
    },
    {
        "name": "HolmesGPT",
        "url": "https://github.com/HolmesGPT/holmesgpt",
        "used_for": "tool-output budgeting and broad troubleshooting comparison",
    },
]


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _query(rng: random.Random, symptom: str, index: int) -> str:
    return "，".join(
        (
            PREFIXES[index % len(PREFIXES)],
            symptom,
            NOISE[rng.randrange(len(NOISE))],
            SUFFIXES[(index * 3) % len(SUFFIXES)],
        )
    )


def build(output: Path) -> dict[str, Any]:
    catalog = json.loads((ROOT / "skills" / "catalog.json").read_text(encoding="utf-8"))
    by_slug = {str(item["slug"]): item for item in catalog["skills"]}
    missing = sorted(set(FAMILY_TEXT) - set(by_slug))
    if missing:
        raise ValueError(f"benchmark taxonomy references missing Skills: {missing}")
    rng = random.Random(SEED)
    public_cases: list[dict[str, Any]] = []
    oracles: list[dict[str, Any]] = []
    all_capabilities = sorted(
        {
            {
                "collect_sys_metrics": "sys_metrics",
                "start_perf_profile": "perf_cpu",
                "start_continuous_profile": "continuous_perf",
                "start_ebpf_io_profile": "ebpf_io",
                "start_pyspy_profile": "pyspy",
                "start_jvm_profile": "java_async",
                "collect_memory_profile": "memory_smaps",
                "collect_go_profile": "go_pprof",
            }.get(tool, tool)
            for item in catalog["skills"]
            for tool in item.get("probe_order") or []
        }
    )

    for skill_index, (slug, (category, symptoms)) in enumerate(FAMILY_TEXT.items()):
        skill = by_slug[slug]
        for index in range(40):
            case_id = f"V2-{skill_index + 1:02d}-POS-{index + 1:03d}"
            public_cases.append(
                {
                    "case_id": case_id,
                    "case_family": "POSITIVE_REUSE",
                    "category": category,
                    "query": _query(rng, symptoms[index % len(symptoms)], index),
                    "target": {
                        "service": f"service-{index % 7}",
                        "environment": "production",
                        "collector_capabilities": all_capabilities,
                    },
                }
            )
            oracles.append(
                {
                    "case_id": case_id,
                    "expected_skill_id": slug,
                    "expected_first_tool": skill["probe_order"][0],
                    "oracle_kind": "ROUTE_MEMORY_SELECTION",
                }
            )
        for index in range(10):
            case_id = f"V2-{skill_index + 1:02d}-NEG-{index + 1:03d}"
            public_cases.append(
                {
                    "case_id": case_id,
                    "case_family": "MISLEADING_OR_UNDERSPECIFIED",
                    "category": category,
                    "query": _query(rng, GENERIC_NEGATIVES[index % len(GENERIC_NEGATIVES)], index + 100),
                    "target": {
                        "service": f"unknown-{index % 3}",
                        "environment": "production",
                        "collector_capabilities": all_capabilities,
                    },
                }
            )
            oracles.append(
                {
                    "case_id": case_id,
                    "expected_skill_id": None,
                    "expected_first_tool": "collect_sys_metrics",
                    "oracle_kind": "SAFE_ABSTENTION",
                }
            )
        for index in range(10):
            case_id = f"V2-{skill_index + 1:02d}-DRIFT-{index + 1:03d}"
            public_cases.append(
                {
                    "case_id": case_id,
                    "case_family": "CAPABILITY_OR_ENVIRONMENT_DRIFT",
                    "category": category,
                    "query": _query(rng, symptoms[index % len(symptoms)], index + 200),
                    "target": {
                        "service": f"service-{index % 7}",
                        "environment": "production-drift",
                        "collector_capabilities": [],
                        "permission_denied": all_capabilities,
                    },
                }
            )
            oracles.append(
                {
                    "case_id": case_id,
                    "expected_skill_id": None,
                    "expected_first_tool": "collect_sys_metrics",
                    "oracle_kind": "SAFE_FALLBACK_ON_DRIFT",
                }
            )

    public_payload = {
        "schema": SCHEMA,
        "version": "2026.09.05-v2",
        "generator_seed": SEED,
        "case_count": len(public_cases),
        "source_policy": {
            "legacy_case_files_read": False,
            "public_oracle_separation": True,
            "synthetic_status": "DETERMINISTIC_SCENARIO_VARIANTS",
            "root_cause_claim": "NOT_MEASURED_WITHOUT_LIVE_EVIDENCE",
        },
        "cases": public_cases,
    }
    oracle_payload = {
        "schema": SCHEMA,
        "version": "2026.09.05-v2",
        "oracles": oracles,
    }
    _write(output / "public" / "cases.json", public_payload)
    _write(output / "private" / "oracles.json", oracle_payload)
    _write(output / "sources.json", {"schema": SCHEMA, "references": OFFICIAL_REFERENCES})
    manifest = {
        "schema": SCHEMA,
        "version": "2026.09.05-v2",
        "case_count": len(public_cases),
        "family_counts": {
            family: sum(1 for row in public_cases if row["case_family"] == family)
            for family in sorted({row["case_family"] for row in public_cases})
        },
        "files": {
            "public/cases.json": _digest(output / "public" / "cases.json"),
            "private/oracles.json": _digest(output / "private" / "oracles.json"),
            "sources.json": _digest(output / "sources.json"),
        },
    }
    _write(output / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    manifest = build(args.output.resolve())
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
