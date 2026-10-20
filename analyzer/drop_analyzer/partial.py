"""解析过程中可保存的有界检查点，与完整结果和诊断结论分别管理。"""

import hashlib
import json
import math
import re
import unicodedata

from .pipeline import SUPPORTED_KINDS

MAX_BYTES = 16 * 1024
MAX_NUMBER = 2**53 - 1
SOURCES = frozenset(
    {
        "unknown",
        "import",
        "perf",
        "procfs",
        "async-profiler",
        "py-spy",
        "go-pprof",
        "hotspot-hprof",
        "memray",
        "linux-trace",
        "gperftools",
        "jemalloc",
        "llvm-bolt",
        "fixed-template",
        "jfr",
    }
)
IDENTIFIERS = ("task_id", "attempt_id", "analysis_job_id", "input_artifact_id")
COUNTS = ("total_samples", "unique_stacks", "node_count", "max_depth")
TOP_FIELDS = {"name", "symbol_sha256", "self", "total", "self_percent", "total_percent"}
FIELDS = {
    "schema_version",
    "completeness",
    *IDENTIFIERS,
    "analysis_run",
    "input_digest",
    "kind",
    "completed_stages",
    "missing_stages",
    "summary",
    "top_functions",
}


def _count(value):
    return type(value) is int and 0 <= value <= MAX_NUMBER


def _number(value):
    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and 0 <= value <= MAX_NUMBER
    )


def _label(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 160
        and not any(unicodedata.category(char).startswith("C") for char in value)
        and len(value.encode("utf-8")) <= 160
    )


def validate_partial(value):
    """持久化前拒绝未知字段、错误类型和超出上限的 JSON。"""
    try:
        if (
            not isinstance(value, dict)
            or set(value) != FIELDS
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or value["completeness"] != "partial"
            or value["kind"] not in SUPPORTED_KINDS
            or type(value["analysis_run"]) is not int
            or not 1 <= value["analysis_run"] <= 2**31 - 1
            or not isinstance(value["input_digest"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["input_digest"])
            or value["completed_stages"] != ["input_verified", "parsed"]
            or value["missing_stages"]
            != ["diagnostic_rules", "result_artifacts", "final_commit"]
        ):
            return False
        for key in IDENTIFIERS:
            identifier = value[key]
            if (
                not isinstance(identifier, str)
                or not re.fullmatch(r"[1-9][0-9]{0,18}", identifier)
                or int(identifier) > 2**63 - 1
            ):
                return False
        summary = value["summary"]
        if (
            not isinstance(summary, dict)
            or set(summary) != {"source", "unit", *COUNTS}
            or summary["source"] not in SOURCES
            or not isinstance(summary["unit"], str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", summary["unit"])
            or not _number(summary["total_samples"])
            or not all(_count(summary[key]) for key in COUNTS if key != "total_samples")
            or summary["unique_stacks"] > 100000
            or summary["node_count"] > 100000
            or summary["max_depth"] > 128
        ):
            return False
        rows = value["top_functions"]
        if not isinstance(rows, list) or len(rows) > 20:
            return False
        for row in rows:
            if (
                not isinstance(row, dict)
                or set(row) != TOP_FIELDS
                or not _label(row["name"])
                or not isinstance(row["symbol_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", row["symbol_sha256"])
                or not _number(row["self"])
                or not _number(row["total"])
                or not row["self"] <= row["total"] <= summary["total_samples"]
            ):
                return False
            for key in ("self_percent", "total_percent"):
                number = row[key]
                if (
                    type(number) not in (int, float)
                    or not math.isfinite(number)
                    or not 0 <= number <= 100
                ):
                    return False
        return (
            len(
                json.dumps(
                    value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                ).encode("utf-8")
            )
            <= MAX_BYTES
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        UnicodeError,
        RecursionError,
    ):
        return False


def build_partial(parsed, job, artifact, *, private_paths=()):
    """不合法的检查点直接省略，不改变完整分析的成功或失败语义。

    函数名是已授权的 profile 数据，不能作为日志字段。标签限制为有界 UTF-8，
    移除控制字符与可信 worker 的私有目录；显示截断前，先绑定原完整符号的摘要。
    """
    try:
        metadata = parsed["metadata"]
        source = (
            "import"
            if job.kind == "IMPORT_COLLAPSED" or job.params.get("source") == "import"
            else metadata.get("source", "unknown")
        )
        unit = (
            "cpu_ticks" if job.kind == "PROC_CPU" else metadata.get("unit", "unknown")
        )
        value = {
            "schema_version": 1,
            "completeness": "partial",
            "task_id": str(job.task_id),
            "attempt_id": str(job.attempt_id),
            "analysis_job_id": str(job.id),
            "analysis_run": job.attempt,
            "input_artifact_id": str(artifact["id"]),
            "input_digest": artifact["sha256"],
            "kind": job.kind,
            "completed_stages": ["input_verified", "parsed"],
            "missing_stages": ["diagnostic_rules", "result_artifacts", "final_commit"],
            "summary": {
                "source": source if source in SOURCES else "unknown",
                "unit": unit
                if isinstance(unit, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", unit)
                else "unknown",
                **{
                    key: metadata[origin]
                    for key, origin in (
                        ("total_samples", "sample_count"),
                        ("unique_stacks", "unique_stack_count"),
                        ("node_count", "node_count"),
                        ("max_depth", "max_depth"),
                    )
                },
            },
            "top_functions": [],
        }
        for row in parsed["top"]["self"][:20]:
            original = row["name"]
            digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
            name = original
            for path in sorted(
                (path for path in private_paths if path), key=len, reverse=True
            ):
                name = name.replace(path, "[analysis-workspace]")
            name = "".join(
                char for char in name if not unicodedata.category(char).startswith("C")
            )
            name = name.encode("utf-8")[:160].decode("utf-8", errors="ignore")
            value["top_functions"].append(
                {
                    "name": name,
                    "symbol_sha256": digest,
                    "self": row["self"],
                    "total": row["inclusive"],
                    "self_percent": round(row["self_ratio"] * 100, 4),
                    "total_percent": round(row["inclusive_ratio"] * 100, 4),
                }
            )
        return value if validate_partial(value) else None
    except (
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        UnicodeError,
        RecursionError,
        AttributeError,
    ):
        return None
