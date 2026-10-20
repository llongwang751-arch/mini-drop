"""严格解析 collapsed stack，限制内存并保证权重可安全写入 JSON。

每行由分号分隔的根到叶调用栈、空格和正十进制采样数构成。采样数是权重，
不是墙钟耗时。函数 inclusive TopN 中，同一采样里的递归函数只累计一次；
火焰树仍保留每个递归栈帧的真实深度。
"""

from dataclasses import dataclass
from typing import Any

from .errors import AnalysisError


@dataclass(frozen=True)
class Limits:
    max_input_bytes: int = 32 * 1024 * 1024
    max_depth: int = 128
    max_nodes: int = 100_000
    max_stacks: int = 500_000
    max_frame_bytes: int = 4096
    max_line_bytes: int = 64 * 1024
    max_total_samples: int = 2**53 - 1


def _limit(message: str) -> None:
    raise AnalysisError("INPUT_LIMIT_EXCEEDED", message)


def analyze_folded(
    raw: bytes, *, limits: Limits | None = None, top_n: int = 100
) -> dict[str, Any]:
    limits = limits or Limits()
    if len(raw) > limits.max_input_bytes:
        _limit("Decoded input exceeds byte limit")
    if not 1 <= top_n <= 1000:
        raise ValueError("top_n must be between 1 and 1000")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise AnalysisError("INVALID_INPUT", "Collapsed input must be UTF-8") from exc

    tree = {"name": "all", "value": 0, "self": 0, "children": {}}
    functions: dict[str, dict[str, Any]] = {}
    unique_stacks: set[tuple[str, ...]] = set()
    node_count, stack_count, max_depth = 1, 0, 0
    for line_number, line in enumerate(text.split("\n"), 1):
        if line.endswith("\r"):
            line = line[:-1]
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > limits.max_line_bytes:
            _limit("Collapsed line exceeds byte limit")
        stack_count += 1
        if stack_count > limits.max_stacks:
            _limit("Collapsed input exceeds stack limit")
        stack_text, separator, count_text = line.rpartition(" ")
        if (
            not separator
            or not count_text
            or not count_text.isascii()
            or not count_text.isdecimal()
            or len(count_text) > 16
        ):
            raise AnalysisError(
                "INVALID_INPUT", f"Invalid sample count at line {line_number}"
            )
        count = int(count_text)
        if count <= 0:
            raise AnalysisError(
                "INVALID_INPUT", f"Non-positive sample count at line {line_number}"
            )
        if tree["value"] + count > limits.max_total_samples:
            _limit("Total sample weight exceeds JSON-safe limit")
        frames = tuple(stack_text.split(";"))
        if len(frames) > limits.max_depth:
            _limit("Stack exceeds depth limit")
        for frame in frames:
            if not frame.strip() or any(
                ord(char) < 32 or ord(char) == 127 for char in frame
            ):
                raise AnalysisError(
                    "INVALID_INPUT", f"Invalid stack frame at line {line_number}"
                )
            if len(frame.encode("utf-8")) > limits.max_frame_bytes:
                _limit("Frame exceeds byte limit")
        unique_stacks.add(frames)
        max_depth = max(max_depth, len(frames))
        tree["value"] += count
        parent = tree
        for frame in frames:
            if frame not in parent["children"]:
                node_count += 1
                if node_count > limits.max_nodes:
                    _limit("Flame tree exceeds node limit")
                parent["children"][frame] = {
                    "name": frame,
                    "value": 0,
                    "self": 0,
                    "children": {},
                }
            parent = parent["children"][frame]
            parent["value"] += count
        parent["self"] += count
        for frame in set(frames):
            row = functions.setdefault(
                frame, {"name": frame, "self": 0, "inclusive": 0}
            )
            row["inclusive"] += count
        functions[frames[-1]]["self"] += count

    if stack_count == 0:
        raise AnalysisError("INVALID_INPUT", "Collapsed input contains no samples")
    total = tree["value"]
    for row in functions.values():
        row["self_ratio"] = row["self"] / total
        row["inclusive_ratio"] = row["inclusive"] / total

    def freeze(node: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": node["name"],
            "value": node["value"],
            "self": node["self"],
            "children": [
                freeze(child) for _, child in sorted(node["children"].items())
            ],
        }

    return {
        "flamegraph": freeze(tree),
        "top": {
            "schema_version": "1.0",
            "total_samples": total,
            "unit": "samples",
            "self": sorted(
                functions.values(), key=lambda row: (-row["self"], row["name"])
            )[:top_n],
            "inclusive": sorted(
                functions.values(), key=lambda row: (-row["inclusive"], row["name"])
            )[:top_n],
        },
        "metadata": {
            "schema_version": "1.0",
            "format": "collapsed/v1",
            "sample_count": total,
            "stack_count": stack_count,
            "unique_stack_count": len(unique_stacks),
            "node_count": node_count,
            "max_depth": max_depth,
            "lost_samples": None,
            "lost_samples_status": "not_reported_by_input",
            "filters": [],
            "unit": "samples",
            "time_range": None,
            "thread_information": "encoded_in_stack_names_if_present",
        },
    }
