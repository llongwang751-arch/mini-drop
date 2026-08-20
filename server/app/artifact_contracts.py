"""Versioned collector/analyzer artifact contracts.

The contract is the boundary between an Agent plugin, the Analyzer Worker and
Drop Insight.  Adding a collector requires an explicit contract instead of
silently treating every uploaded file as equivalent evidence.
"""

from __future__ import annotations

from dataclasses import dataclass


CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class CollectorArtifactContract:
    collector_type: str
    analyzer_type: str
    analyzer_version: str
    accepted_types: frozenset[str]
    required_any: frozenset[str]
    analysis_types: frozenset[str]
    raw_types: frozenset[str] = frozenset()

    def validate(self, artifacts: list[dict]) -> set[str]:
        artifact_types = {
            str(item.get("artifact_type") or "")
            for item in artifacts
            if item.get("artifact_type")
        }
        if not artifact_types:
            raise ArtifactContractError(
                f"{self.collector_type}: Agent 未上报任何采集产物"
            )
        unsupported = artifact_types - self.accepted_types
        if unsupported:
            raise ArtifactContractError(
                f"{self.collector_type}: 出现契约外产物 {sorted(unsupported)}"
            )
        if not artifact_types.intersection(self.required_any):
            raise ArtifactContractError(
                f"{self.collector_type}: 缺少必要产物，至少需要 "
                f"{sorted(self.required_any)} 之一"
            )
        return artifact_types


class ArtifactContractError(ValueError):
    """Collector output does not satisfy its declared versioned contract."""


def _contract(
    collector_type: str,
    *,
    accepted: set[str],
    required_any: set[str],
    analysis: set[str],
    raw: set[str] | None = None,
) -> CollectorArtifactContract:
    return CollectorArtifactContract(
        collector_type=collector_type,
        analyzer_type=f"collector.{collector_type}",
        analyzer_version=CONTRACT_VERSION,
        accepted_types=frozenset(accepted),
        required_any=frozenset(required_any),
        analysis_types=frozenset(analysis),
        raw_types=frozenset(raw or set()),
    )


COLLECTOR_CONTRACTS: dict[str, CollectorArtifactContract] = {
    "perf_cpu": _contract(
        "perf_cpu",
        accepted={
            "raw",
            "flamegraph_json",
            "flamegraph_svg",
            "top_json",
            "suggestions_md",
        },
        required_any={"raw", "flamegraph_json", "flamegraph_svg", "top_json"},
        analysis={"flamegraph_json", "flamegraph_svg", "top_json", "suggestions_md"},
        raw={"raw"},
    ),
    "ebpf_io": _contract(
        "ebpf_io",
        accepted={"ebpf_metrics", "ebpf_raw"},
        required_any={"ebpf_metrics"},
        analysis={"ebpf_metrics"},
        raw={"ebpf_raw"},
    ),
    "pyspy": _contract(
        "pyspy",
        accepted={"flamegraph_svg", "raw", "flamegraph_json", "top_json"},
        required_any={"flamegraph_svg", "raw", "flamegraph_json", "top_json"},
        analysis={"flamegraph_svg", "flamegraph_json", "top_json"},
        raw={"raw"},
    ),
    "continuous_perf": _contract(
        "continuous_perf",
        accepted={
            "continuous_raw",
            "continuous_window",
            "continuous_summary",
            "continuous_flamegraph_json",
            "continuous_flamegraph_svg",
            "continuous_top_json",
        },
        required_any={"continuous_raw", "continuous_summary"},
        analysis={
            "continuous_summary",
            "continuous_flamegraph_json",
            "continuous_flamegraph_svg",
            "continuous_top_json",
        },
        raw={"continuous_raw", "continuous_window"},
    ),
    "java_async": _contract(
        "java_async",
        accepted={"java_flamegraph_html"},
        required_any={"java_flamegraph_html"},
        analysis={"java_flamegraph_html"},
    ),
    "go_pprof": _contract(
        "go_pprof",
        accepted={"pprof_raw", "raw", "flamegraph_svg", "flamegraph_json", "top_json"},
        required_any={"pprof_raw", "raw", "flamegraph_json", "top_json"},
        analysis={"flamegraph_svg", "flamegraph_json", "top_json"},
        raw={"pprof_raw", "raw"},
    ),
    "memory_smaps": _contract(
        "memory_smaps",
        accepted={"memory_json"},
        required_any={"memory_json"},
        analysis={"memory_json"},
    ),
    "sys_metrics": _contract(
        "sys_metrics",
        accepted={"sys_metrics"},
        required_any={"sys_metrics"},
        analysis={"sys_metrics"},
    ),
}


def get_collector_contract(collector_type: str) -> CollectorArtifactContract:
    try:
        return COLLECTOR_CONTRACTS[collector_type]
    except KeyError as exc:
        raise ArtifactContractError(
            f"未注册采集器分析契约: {collector_type}"
        ) from exc
