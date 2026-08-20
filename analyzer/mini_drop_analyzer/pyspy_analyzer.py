"""Python py-spy speedscope analyzer: speedscope JSON -> top.json + flamegraph.json.

py-spy exports stacks in the speedscope "sampled" file format. This analyzer
parses the profile and emits the same top.json + flamegraph.json shapes as the
perf analyzer, so the Web and the Drop Insight evidence chain stay uniform.

用法:
  python -m analyzer.mini_drop_analyzer.pyspy_analyzer \
    --task-id task_xxx --speedscope /tmp/task_xxx/pyspy-speedscope.json \
    --output-dir DIR

退出码: 0=成功, 1=失败
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

MAX_TREE_DEPTH = 50


def load_speedscope(data: bytes | str) -> dict:
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    document = json.loads(data)
    if not isinstance(document, dict):
        raise ValueError("speedscope 顶层必须是 JSON 对象")
    return document


def _frame_name(frames: list[dict], index: int) -> str:
    if 0 <= index < len(frames):
        frame = frames[index]
        if isinstance(frame, dict):
            name = frame.get("name")
            if isinstance(name, str) and name:
                return name
    return f"frame_{index}"


def analyze_speedscope(document: dict, *, limit: int = 20) -> dict:
    shared = document.get("shared", {})
    frames = shared.get("frames", []) if isinstance(shared, dict) else []
    if not isinstance(frames, list):
        frames = []

    profiles = document.get("profiles", [])
    if not isinstance(profiles, list):
        profiles = []
    profile = None
    for candidate in profiles:
        if isinstance(candidate, dict) and candidate.get("type") == "sampled":
            profile = candidate
            break
    if profile is None and profiles:
        profile = profiles[0] if isinstance(profiles[0], dict) else None
    if profile is None:
        raise ValueError("speedscope 中缺少 sampled profile")

    samples = profile.get("samples", [])
    weights = profile.get("weights", [])
    if not isinstance(samples, list) or not isinstance(weights, list):
        raise ValueError("speedscope sampled profile 缺少 samples/weights")

    resolved: list[tuple[list[str], int]] = []
    total = 0
    for sample_index, stack in enumerate(samples):
        if not isinstance(stack, list):
            continue
        weight = weights[sample_index] if sample_index < len(weights) else 1
        try:
            weight = max(0, int(weight))
        except (TypeError, ValueError):
            weight = 1
        total += weight
        frames_in_stack = [
            _frame_name(frames, int(frame_index))
            for frame_index in stack
            if isinstance(frame_index, (int, float, str))
        ]
        if frames_in_stack:
            resolved.append((frames_in_stack, weight))

    counter: dict[str, int] = {}
    for frames_in_stack, weight in resolved:
        for frame in frames_in_stack:
            counter[frame] = counter.get(frame, 0) + weight
    top = [
        {
            "name": name,
            "samples": count,
            "percent": round(count / total * 100, 1) if total else 0,
        }
        for name, count in sorted(counter.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    ]

    # py-spy speedscope samples are leaf-first; reverse to root-to-leaf for the
    # d3 flame tree (consistent with the perf analyzer's collapsed stacks).
    root: dict = {"name": "root", "value": 0, "children": []}
    node_map: dict[tuple[str, ...], dict] = {(): root}
    for frames_in_stack, weight in resolved:
        root["value"] += weight
        ordered = list(reversed(frames_in_stack))
        depth = min(len(ordered), MAX_TREE_DEPTH)
        for i in range(depth):
            prefix = tuple(ordered[: i + 1])
            parent_key = tuple(ordered[:i])
            parent = node_map.get(parent_key)
            if parent is None:
                break
            if prefix not in node_map:
                node: dict = {"name": ordered[i], "value": 0}
                parent.setdefault("children", []).append(node)
                node_map[prefix] = node
            node_map[prefix]["value"] += weight

    return {"top": top, "flamegraph": root, "sample_count": total}


def main() -> None:
    parser = argparse.ArgumentParser(description="py-spy speedscope Analyzer")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--speedscope", required=True, help="speedscope JSON 文件路径")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    input_path = Path(args.speedscope)
    if not input_path.is_file() or input_path.stat().st_size == 0:
        print(json.dumps({"status": "FAILED", "error": "speedscope 文件不存在或为空"}))
        raise SystemExit(1)

    try:
        document = load_speedscope(input_path.read_bytes())
        result = analyze_speedscope(document)
    except Exception as exc:  # pragma: no cover - defensive
        print(json.dumps({"status": "FAILED", "error": f"speedscope 解析失败: {str(exc)[:200]}"}))
        raise SystemExit(1)

    output_dir = Path(args.output_dir) / args.task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "top.json").write_text(
        json.dumps(result["top"], indent=2, ensure_ascii=False)
    )
    (output_dir / "flamegraph.json").write_text(
        json.dumps(result["flamegraph"], separators=(",", ":"), ensure_ascii=False)
    )
    print(json.dumps({
        "task_id": args.task_id,
        "status": "SUCCESS",
        "top_functions": result["top"][:5],
        "sample_count": result["sample_count"],
        "output_files": {
            "flamegraph_json": str(output_dir / "flamegraph.json"),
            "top_json": str(output_dir / "top.json"),
        },
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
