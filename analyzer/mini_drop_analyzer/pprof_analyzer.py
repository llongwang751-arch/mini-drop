"""Go pprof profile analyzer: gzip profile.pb.gz -> top.json + flamegraph.json.

Parses the canonical Google pprof protobuf (only the fields needed to rebuild
call stacks) and emits the same d3-flame-graph tree + TopN that the perf
analyzer produces, so the Web and the Drop Insight evidence chain consume a
uniform shape.

用法:
  python -m analyzer.mini_drop_analyzer.pprof_analyzer \
    --task-id task_xxx --profile /tmp/task_xxx/profile.pb.gz --output-dir DIR

退出码: 0=成功, 1=失败
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from analyzer.mini_drop_analyzer.profile_pb2 import Profile

MAX_TREE_DEPTH = 50


def load_profile(data: bytes) -> Profile:
    """Decode a (possibly gzipped) pprof protobuf payload."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    profile = Profile()
    profile.ParseFromString(data)
    return profile


def _function_name(profile: Profile, function_id: int) -> str:
    for function in profile.function:
        if function.id == function_id:
            index = function.name
            if 0 <= index < len(profile.string_table):
                return profile.string_table[index] or f"func_{function_id}"
            return f"func_{function_id}"
    return f"func_{function_id}"


def _function_source(profile: Profile, function_id: int) -> tuple[str, str, int]:
    """Resolve a function id to (name, file, start_line) from the string table."""
    for function in profile.function:
        if function.id != function_id:
            continue
        name = (
            profile.string_table[function.name]
            if 0 <= function.name < len(profile.string_table)
            else ""
        )
        filename = (
            profile.string_table[function.filename]
            if 0 <= function.filename < len(profile.string_table)
            else ""
        )
        return (name or f"func_{function_id}", filename, int(function.start_line))
    return (f"func_{function_id}", "", 0)


def stack_frames(profile: Profile, location_ids: list[int]) -> list[dict]:
    """Resolve location ids to {name, file, line}, leaf-first (pprof order).

    pprof samples list locations innermost-first, so the caller reverses when a
    root-to-leaf order is required for flame trees. Source metadata enables the
    function -> file/line -> source-snippet evidence chain (optimization P1).
    """
    frames: list[dict] = []
    id_to_location = {location.id: location for location in profile.location}
    for location_id in location_ids:
        location = id_to_location.get(location_id)
        if location is None:
            continue
        for line in location.line:
            name, filename, lineno = _function_source(profile, line.function_id)
            frames.append({"name": name, "file": filename, "line": lineno})
    return frames


def analyze_profile(profile: Profile, *, limit: int = 20) -> dict:
    """Return {"top": [...], "flamegraph": tree, "sample_count": int}."""
    resolved: list[tuple[list[dict], int]] = []
    total = 0
    for sample in profile.sample:
        weight = sample.value[0] if sample.value else 1
        total += max(0, int(weight))
        frames = stack_frames(profile, list(sample.location_id))
        if frames:
            resolved.append((frames, max(0, int(weight))))

    # Inclusive top-N: every frame in a sample's stack gets the sample weight,
    # matching the perf analyzer's collapsed-stack semantics. The first-seen
    # file/line for each function becomes its source evidence reference.
    counter: dict[str, int] = {}
    source: dict[str, dict] = {}
    for frames, weight in resolved:
        for frame in frames:
            name = frame["name"]
            counter[name] = counter.get(name, 0) + weight
            source.setdefault(name, {"file": frame["file"], "line": frame["line"]})
    top = [
        {
            "name": name,
            "samples": count,
            "percent": round(count / total * 100, 1) if total else 0,
            **source.get(name, {"file": "", "line": 0}),
        }
        for name, count in sorted(counter.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    ]

    # Flame tree: pprof samples are leaf-first, so reverse to root-to-leaf.
    root: dict = {"name": "root", "value": 0, "children": []}
    node_map: dict[tuple[str, ...], dict] = {(): root}
    for frames, weight in resolved:
        root["value"] += weight
        ordered = list(reversed(frames))
        depth = min(len(ordered), MAX_TREE_DEPTH)
        for i in range(depth):
            frame = ordered[i]
            name = frame["name"]
            prefix = tuple(item["name"] for item in ordered[: i + 1])
            parent_key = tuple(item["name"] for item in ordered[:i])
            parent = node_map.get(parent_key)
            if parent is None:
                break
            if prefix not in node_map:
                node: dict = {
                    "name": name,
                    "value": 0,
                    "file": frame.get("file", ""),
                    "line": frame.get("line", 0),
                }
                parent.setdefault("children", []).append(node)
                node_map[prefix] = node
            node_map[prefix]["value"] += weight

    return {"top": top, "flamegraph": root, "sample_count": total}


def main() -> None:
    parser = argparse.ArgumentParser(description="Go pprof Analyzer")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--profile", required=True, help="profile.pb.gz 文件路径")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    input_path = Path(args.profile)
    if not input_path.is_file() or input_path.stat().st_size == 0:
        print(json.dumps({"status": "FAILED", "error": "profile 不存在或为空"}))
        raise SystemExit(1)

    try:
        profile = load_profile(input_path.read_bytes())
    except Exception as exc:  # pragma: no cover - defensive
        print(json.dumps({"status": "FAILED", "error": f"profile 解析失败: {str(exc)[:200]}"}))
        raise SystemExit(1)

    output_dir = Path(args.output_dir) / args.task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    result = analyze_profile(profile)

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
