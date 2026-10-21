"""兼容可信 py-spy raw 中的显式空栈，保留权重并暴露质量边界。

py-spy 0.4.1 将帧名直接用分号拼接；未解析到帧时仍可能输出带正权重的空栈。
这里只用明确的未知占位表示缺失信息，不删除样本、猜测函数或改变通用 collapsed 语法。
来源检查在 registry 分派前完成，用户导入和其他采集器不得借此放宽空帧校验。
参考：https://github.com/benfred/py-spy/blob/v0.4.1/src/flamegraph.rs
"""

from .errors import AnalysisError
from .folded import Limits, analyze_folded


QUALITY_SOURCE = "collector_pyspy_raw_v1"
UNKNOWN_STACK = "[py-spy unresolved stack]"
UNKNOWN_FRAME = "[py-spy unresolved frame]"


def trusted_pyspy_source(context: dict) -> bool:
    """只信任 worker 从当前 attempt 关联得到的 runner_metadata，不回退用户制品声明。

    task/attempt 与制品完整性的绑定仍由 worker 和 repository 负责；本函数不自行授权，
    只在可信采集来源明确且三种分派标识一致时选择有限格式兼容。
    """
    if (
        context.get("pipeline") != "python_cpu_v1"
        or context.get("kind") != "PYTHON_CPU"
        or context.get("format") != "folded"
    ):
        return False
    artifact, params = context.get("artifact"), context.get("params", {})
    if not isinstance(artifact, dict) or not isinstance(params, dict):
        return False
    metadata = artifact.get("metadata", {})
    runner = artifact.get("runner_metadata")
    if not isinstance(metadata, dict) or not isinstance(runner, dict):
        return False
    if any(value.get("source") == "import" for value in (params, metadata, runner)):
        return False
    return (
        type(runner.get("schema_version")) is int
        and runner["schema_version"] == 1
        and runner.get("kind") == "PYTHON_CPU"
        and runner.get("runner") == "py-spy"
        and runner.get("provenance") != "import"
    )


def analyze_pyspy_raw(raw: bytes, *, limits: Limits | None = None, top_n: int = 100):
    """替换显式缺失项后继续调用严格解析器，保持每行及总采样权重不变。

    输入、每行、深度和标准化后的总量都先受限，防止大量空帧被长占位展开。
    非空白帧里的控制字符、错误权重等仍由原解析器拒绝，不尝试修复业务符号。
    """
    limits = limits or Limits()
    if len(raw) > limits.max_input_bytes:
        raise AnalysisError("INPUT_LIMIT_EXCEEDED", "Decoded input exceeds byte limit")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise AnalysisError("INVALID_INPUT", "Collapsed input must be UTF-8") from exc
    lines, normalized_bytes = [], 0
    stack_lines, placeholders, unresolved_weight = 0, 0, 0
    nonempty_lines = 0
    for number, line in enumerate(text.split("\n"), 1):
        if line.endswith("\r"):
            line = line[:-1]
        if not line.strip():
            continue
        nonempty_lines += 1
        if nonempty_lines > limits.max_stacks:
            raise AnalysisError(
                "INPUT_LIMIT_EXCEEDED", "Collapsed input exceeds stack limit"
            )
        if len(line.encode("utf-8")) > limits.max_line_bytes:
            raise AnalysisError(
                "INPUT_LIMIT_EXCEEDED", "Collapsed line exceeds byte limit"
            )
        stack, separator, count_text = line.rpartition(" ")
        if (
            not separator
            or not count_text
            or not count_text.isascii()
            or not count_text.isdecimal()
            or len(count_text) > 16
        ):
            raise AnalysisError(
                "INVALID_INPUT", f"Invalid sample count at line {number}"
            )
        count = int(count_text)
        if count <= 0:
            raise AnalysisError(
                "INVALID_INPUT", f"Non-positive sample count at line {number}"
            )
        if stack.count(";") + 1 > limits.max_depth:
            raise AnalysisError("INPUT_LIMIT_EXCEEDED", "Stack exceeds depth limit")
        frames = stack.split(";")
        if any(frame in {UNKNOWN_STACK, UNKNOWN_FRAME} for frame in frames):
            raise AnalysisError(
                "INVALID_INPUT", "Raw py-spy input uses a reserved unresolved marker"
            )
        missing = sum(frame == "" for frame in frames)
        if missing:
            stack_lines += 1
            placeholders += missing
            unresolved_weight += count
            # 空的完整栈表示连真实深度也未知，不能把它伪装成一个真实Python调用帧。
            frames = (
                [UNKNOWN_STACK]
                if stack == ""
                else [frame or UNKNOWN_FRAME for frame in frames]
            )
            line = ";".join(frames) + " " + count_text
        encoded_bytes = len(line.encode("utf-8")) + 1
        if (
            encoded_bytes - 1 > limits.max_line_bytes
            or normalized_bytes + encoded_bytes > limits.max_input_bytes
        ):
            raise AnalysisError(
                "INPUT_LIMIT_EXCEEDED", "Normalized py-spy input exceeds byte limit"
            )
        normalized_bytes += encoded_bytes
        lines.append(line)
    normalized = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    parsed = analyze_folded(normalized, limits=limits, top_n=top_n)
    quality = {
        "schema_version": 1,
        "source": QUALITY_SOURCE,
        "normalized": placeholders > 0,
        "unresolved_stack_lines": stack_lines,
        "placeholder_count": placeholders,
        "unresolved_sample_weight": unresolved_weight,
        "total_sample_weight": parsed["metadata"]["sample_count"],
    }
    parsed["metadata"]["pyspy_raw_quality"] = quality
    limitations = [
        "质量计数只统计py-spy raw中显式为空的栈或帧，不能据此认定其他丢样或解析缺失为零。"
    ]
    if placeholders:
        limitations.append(
            "未解析栈或帧的样本权重保留在显式unknown占位中；占位项不是真实业务函数，不能用于函数归因或源码行定位。"
        )
    parsed["sections"] = {
        "sampling_quality": {**quality, "limitations": limitations}
    }
    return parsed
