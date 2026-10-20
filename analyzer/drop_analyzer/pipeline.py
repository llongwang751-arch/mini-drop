"""输入完整性、稳定 JSON 编码与分析制品组装。

先按存储字节核对大小与 SHA-256，再在解压上限内解码；结果及 manifest 分开生成，
manifest 将输入摘要、采集 attempt 和每个输出制品摘要连成可追溯链。
"""

import gzip
import hashlib
import io
import json
import zlib

from . import __version__
from .errors import AnalysisError
from .folded import Limits
from .rules import RULE_VERSION, suggestions

SUPPORTED_KINDS = frozenset(
    {
        "PERF_CPU",
        "PROC_CPU",
        "IMPORT_COLLAPSED",
        "JAVA_CPU",
        "PYTHON_CPU",
        "GO_CPU",
        "GO_HEAP",
        "JAVA_HEAP",
        "PYTHON_MEMORY",
        "IO_TRACE",
        "MEMORY_TRACE",
        "GPERF_CPU",
        "GPERF_HEAP",
        "JEPROF_HEAP",
        "BOLT_PROFILE",
        "SCRIPT",
        "RESOURCE_SNAPSHOT",
    }
)
COLLAPSED_CONTENT_TYPES = frozenset(
    {
        "text/x-collapsed",
        "text/plain",
        "application/x-collapsed",
        "text/x-folded",
        "application/octet-stream",
    }
)


def decode_input(
    raw: bytes,
    content_type: str,
    expected_sha256: str,
    expected_size: int,
    limits: Limits,
) -> bytes:
    """先核对存储原文大小与摘要，再按声明格式解码；压缩后与解压后的字节数分别受限。"""
    if (
        len(raw) != expected_size
        or not expected_sha256
        or hashlib.sha256(raw).hexdigest() != expected_sha256.lower()
    ):
        raise AnalysisError(
            "ARTIFACT_INTEGRITY_ERROR",
            "Raw artifact size or SHA-256 does not match metadata",
        )
    if content_type in {"text/x-collapsed+gzip", "application/gzip"}:
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                decoded = stream.read(limits.max_input_bytes + 1)
        except (OSError, EOFError, zlib.error) as exc:
            raise AnalysisError(
                "INVALID_INPUT", "Invalid or truncated gzip artifact"
            ) from exc
        if len(decoded) > limits.max_input_bytes:
            raise AnalysisError(
                "INPUT_LIMIT_EXCEEDED", "Decoded artifact exceeds byte limit"
            )
        return decoded
    if content_type not in COLLAPSED_CONTENT_TYPES:
        raise AnalysisError(
            "UNSUPPORTED_FORMAT", "This pipeline accepts UTF-8 collapsed stacks only"
        )
    if len(raw) > limits.max_input_bytes:
        raise AnalysisError("INPUT_LIMIT_EXCEEDED", "Raw artifact exceeds byte limit")
    return raw


def json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def build_outputs(
    result: dict,
    *,
    task_id: str,
    job_id: str,
    input_sha256: str,
    public_result: dict | None = None,
    object_prefix: str | None = None,
    attempt_id: str | None = None,
    input_artifact_id: str | None = None,
) -> dict[str, bytes]:
    """把解析结果编码为火焰图、TopN、报告和 manifest，返回文件名到 bytes 的映射。

    固定 gzip mtime 与稳定 JSON 排序有助于摘要复核；规则报告不代替后续 AI 诊断。
    """
    outputs = {
        "flamegraph.json.gz": gzip.compress(
            json_bytes(result["flamegraph"]), compresslevel=6, mtime=0
        ),
        "top.json": json_bytes(result["top"]),
        "report.json": json_bytes(
            {
                "schema_version": "1.0",
                "generator": "deterministic_rules",
                "analyzer_version": __version__,
                "rule_version": RULE_VERSION,
                "metadata": result["metadata"],
                "suggestions": suggestions(result["top"]),
                "limitations": [
                    "CPU 样本权重不等于请求耗时。",
                    "输入没有时间戳、丢样或线程元数据时，不推断这些字段。",
                    "规则建议仅表明样本中的相关信号，需要额外测量确认原因。",
                ],
            }
        ),
    }
    if public_result is not None:
        outputs["top.json"] = json_bytes(
            {
                "schema_version": 1,
                "total_samples": public_result["total_samples"],
                "unit": public_result["summary"]["unit"],
                "top_functions": public_result["top_functions"],
                "self": result["top"]["self"],
                "inclusive": result["top"]["inclusive"],
            }
        )
        outputs["report.json"] = json_bytes(
            {
                "schema_version": 1,
                "generator": "deterministic_rules",
                "analyzer_version": __version__,
                "rule_version": RULE_VERSION,
                "metadata": {
                    **result["metadata"],
                    "unit": public_result["summary"]["unit"],
                },
                "summary": public_result["summary"],
                "suggestions": public_result["suggestions"],
                "sections": public_result.get("sections", {}),
            }
        )
    manifest = {
        "schema_version": "1.0",
        "task_id": task_id,
        "analysis_job_id": job_id,
        "analyzer_version": __version__,
        "input_sha256": input_sha256,
        "pipeline": "collapsed_flamegraph",
        "artifacts": [
            {
                "name": name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for name, data in sorted(outputs.items())
        ],
    }
    if public_result is not None:
        manifest["kind"] = public_result["kind"]
        manifest["summary"] = public_result["summary"]
    manifest["attempt_id"] = attempt_id
    manifest["input_artifact_id"] = input_artifact_id
    if object_prefix:
        for artifact in manifest["artifacts"]:
            artifact["object_key"] = f"{object_prefix}/{artifact['name']}"
    outputs["manifest.json"] = json_bytes(manifest)
    return outputs
