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


def _frame_info(frames: list[dict], index: int) -> dict:
    if 0 <= index < len(frames):
        frame = frames[index]
        if isinstance(frame, dict):
            name = frame.get("name")
            if isinstance(name, str) and name:
                result = {"name": name}
                if isinstance(frame.get("file"), str) and frame["file"]:
                    result["file"] = frame["file"]
                try:
                    if int(frame.get("line") or 0) > 0:
                        result["line"] = int(frame["line"])
                except (TypeError, ValueError):
                    pass
                return result
    return {"name": f"frame_{index}"}


def analyze_speedscope(document: dict, *, limit: int = 20) -> dict:
    shared = document.get("shared", {})
    frames = shared.get("frames", []) if isinstance(shared, dict) else []
    if not isinstance(frames, list):
        frames = []

    profiles = document.get("profiles", [])
    if not isinstance(profiles, list):
        profiles = []
    sampled_profiles = [
        candidate
        for candidate in profiles
        if isinstance(candidate, dict) and candidate.get("type") == "sampled"
    ]
    if not sampled_profiles:
        raise ValueError("speedscope 中缺少 sampled profile")

    resolved: list[tuple[list[dict], int]] = []
    total = 0
    # py-spy emits one sampled speedscope profile per Python thread. Aggregate
    # every thread; selecting profiles[0] usually analyzes only the idle main
    # thread and misses the actual worker hotspot.
    for profile in sampled_profiles:
        samples = profile.get("samples", [])
        weights = profile.get("weights", [])
        if not isinstance(samples, list) or not isinstance(weights, list):
            raise ValueError("speedscope sampled profile 缺少 samples/weights")
        for sample_index, stack in enumerate(samples):
            if not isinstance(stack, list):
                continue
            weight = weights[sample_index] if sample_index < len(weights) else 1
            try:
                numeric_weight = float(weight)
                # py-spy writes sampling intervals in seconds (for example
                # 0.0101 at 99 Hz), not integer occurrence counts.  Each stack is
                # still one observed sample; converting the interval with int()
                # silently turned every sample into zero.
                weight = 1 if 0 < numeric_weight < 1 else max(0, int(numeric_weight))
            except (TypeError, ValueError):
                weight = 1
            total += weight
            frames_in_stack = [
                _frame_info(frames, int(frame_index))
                for frame_index in stack
                if isinstance(frame_index, (int, float, str))
            ]
            if frames_in_stack:
                resolved.append((frames_in_stack, weight))

    # Speedscope sampled stacks are root-first. Top Functions must use leaf/self
    # samples; counting every parent frame makes the whole call chain appear
    # as a set of 100% hotspots and destroys diagnostic discrimination.
    counter: dict[tuple[str, str, int], int] = {}
    for frames_in_stack, weight in resolved:
        if frames_in_stack:
            leaf = frames_in_stack[-1]
            identity = (
                str(leaf.get("name") or "unknown"),
                str(leaf.get("file") or ""),
                int(leaf.get("line") or 0),
            )
            counter[identity] = counter.get(identity, 0) + weight
    top = [
        {
            "name": identity[0],
            **({"file": identity[1]} if identity[1] else {}),
            **({"line": identity[2]} if identity[2] else {}),
            "samples": count,
            "percent": round(count / total * 100, 1) if total else 0,
        }
        for identity, count in sorted(
            counter.items(), key=lambda kv: kv[1], reverse=True
        )[:limit]
    ]

    # Speedscope samples are already root-to-leaf, matching the d3 flame tree.
    root: dict = {"name": "root", "value": 0, "children": []}
    node_map: dict[tuple[str, ...], dict] = {(): root}
    for frames_in_stack, weight in resolved:
        root["value"] += weight
        ordered = frames_in_stack
        depth = min(len(ordered), MAX_TREE_DEPTH)
        for i in range(depth):
            identities = [
                (str(item.get("name") or "unknown"), str(item.get("file") or ""), int(item.get("line") or 0))
                for item in ordered
            ]
            prefix = tuple(identities[: i + 1])
            parent_key = tuple(identities[:i])
            parent = node_map.get(parent_key)
            if parent is None:
                break
            if prefix not in node_map:
                frame = ordered[i]
                node: dict = {"name": frame["name"], "value": 0}
                if frame.get("file"):
                    node["file"] = frame["file"]
                if frame.get("line"):
                    node["line"] = frame["line"]
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
