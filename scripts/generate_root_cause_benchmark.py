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
from server.app.drop_insight.root_cause_benchmark import SCHEMA, TOOL_TO_COLLECTOR


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


def _observations(scenario, index: int) -> dict[str, list[str]]:
    collectors = list(scenario.recommended_collectors)
    values: dict[str, list[str]] = {collector: [] for collector in collectors}
    for signal_index, signal in enumerate(scenario.expected_signals):
        collector = collectors[min(signal_index, len(collectors) - 1)]
        values[collector].append(signal)
    for collector in collectors:
        values[collector].append(f"{scenario.target_runtime} 受控采集窗口 {index + 1} 已完成")
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
