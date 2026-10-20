"""把格式适配器输出转换为统一公开结果，保留真实单位、来源和解释边界。

规则发现与 AI 诊断分属不同环节；这里的建议仅表示相关信号，不能直接标记为根因。
"""

from .errors import AnalysisError
from .rules import suggestions


def make_result(parsed: dict, kind: str, params: dict) -> dict:
    """从适配器 parsed、任务 kind 和参数生成统一公开结果，保持来源与单位明确。

    用户导入数据不能继承现场频率/时长的可信度；不存在的丢样等字段保持未知，规则发现只指向相关证据。
    """
    sources = {
        "PERF_CPU": "perf",
        "PROC_CPU": "procfs",
        "IMPORT_COLLAPSED": "import",
        "JAVA_CPU": "async-profiler",
        "PYTHON_CPU": "py-spy",
        "GO_CPU": "go-pprof",
        "GO_HEAP": "go-pprof",
        "JAVA_HEAP": "hotspot-hprof",
        "PYTHON_MEMORY": "memray",
        "IO_TRACE": "linux-trace",
        "MEMORY_TRACE": "linux-trace",
        "GPERF_CPU": "gperftools",
        "GPERF_HEAP": "gperftools",
        "JEPROF_HEAP": "jemalloc",
        "BOLT_PROFILE": "llvm-bolt",
        "SCRIPT": "fixed-template",
        "RESOURCE_SNAPSHOT": "procfs",
    }
    if kind not in sources:
        raise AnalysisError(
            "UNSUPPORTED_FORMAT", "Task kind has no implemented analysis pipeline"
        )
    metadata = parsed["metadata"]
    imported = (
        kind == "IMPORT_COLLAPSED"
        or params.get("source") == "import"
        or metadata.get("provenance") == "import"
    )
    limitations = ["采样权重不等于请求延迟。"]
    if metadata.get("format", "collapsed/v1") == "collapsed/v1":
        limitations.append(
            "折叠输入未提供丢样统计、时间戳和独立线程元数据；这些信息未作推断。"
        )
    for section in parsed.get("sections", {}).values():
        limitations.extend(section.get("limitations", []))
    if kind == "PROC_CPU":
        limitations.append(
            "procfs 仅记录线程 CPU tick 增量，火焰树为进程→线程两级，不包含函数调用栈。"
        )
    if imported:
        limitations.append(
            "数据来自用户导入，平台未在目标机器执行采样，无法核实采样环境与采样率。"
        )
    findings = (
        suggestions(parsed["top"], params.get("_diagnostic_rules"))
        if kind
        in {
            "PERF_CPU",
            "IMPORT_COLLAPSED",
            "JAVA_CPU",
            "PYTHON_CPU",
            "GO_CPU",
            "GPERF_CPU",
        }
        else []
    )
    findings = [
        {
            "title": finding["title"],
            "content": finding["action"],
            "severity": finding["severity"],
            "confidence": 0.65,
            "evidence": {
                **finding["evidence"],
                "code": finding["code"],
                "interpretation": "相关信号，需额外测量确认原因",
            },
            "engine": "rules",
            "version": finding["rule_version"],
        }
        for finding in findings
    ]
    result = {
        "schema_version": 1,
        "kind": kind,
        "total_samples": metadata["sample_count"],
        "unique_stacks": metadata["unique_stack_count"],
        "node_count": metadata["node_count"],
        "max_depth": metadata["max_depth"],
        "flamegraph": parsed["flamegraph"],
        "top_functions": [
            {
                "name": row["name"],
                "self": row["self"],
                "total": row["inclusive"],
                "self_percent": round(row["self_ratio"] * 100, 4),
                "total_percent": round(row["inclusive_ratio"] * 100, 4),
            }
            for row in parsed["top"]["self"]
        ],
        "summary": {
            "duration_seconds": params.get("duration_seconds")
            if not imported
            else None,
            "frequency_hz": params.get("frequency_hz")
            if kind == "PERF_CPU" and not imported
            else None,
            "unit": "cpu_ticks"
            if kind == "PROC_CPU"
            else metadata.get("unit", "samples"),
            "source": "import" if imported else metadata.get("source", sources[kind]),
            "profile_source": metadata.get("profile_source", sources[kind]),
            "input_format": metadata.get(
                "input_format", metadata.get("format", "folded")
            ),
            "observation_count": metadata.get(
                "observation_count",
                metadata["sample_count"]
                if kind
                in {
                    "PERF_CPU",
                    "IMPORT_COLLAPSED",
                    "PYTHON_CPU",
                    "JAVA_CPU",
                    "GPERF_CPU",
                }
                else None,
            ),
            "profile_type": metadata.get("profile_type"),
            "lost_samples": metadata.get("lost_samples"),
            "limitations": limitations,
        },
        "suggestions": findings,
        "sections": parsed.get("sections", {}),
        "metadata": metadata,
    }
    if metadata.get("duration_ns") and not imported:
        result["summary"]["duration_seconds"] = metadata["duration_ns"] / 1e9
    return result
