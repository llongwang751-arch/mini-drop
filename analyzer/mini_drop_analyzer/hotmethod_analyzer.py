"""Mini-Drop Analyzer 命令行入口。

从 perf.data 生成 d3-flame-graph 火焰图 JSON 树、TopN 热点函数、
规则建议和 fallback SVG。

用法:
  python -m analyzer.mini_drop_analyzer.hotmethod_analyzer \
    --task-id task_xxx --perf-data /tmp/mini-drop/task_xxx/perf.data \
    --config analyzer/config.example.toml

退出码: 0=成功, 1=失败
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


QUALITY_ERROR_CODE = "ANALYSIS_INPUT_INVALID"
QUALITY_FAILURE_KIND = "SAMPLE_QUALITY"


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini-Drop Analyzer")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--perf-data", required=True, help="perf.data 文件路径")
    parser.add_argument("--config", default="analyzer/config.example.toml")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    task_id = args.task_id
    perf_data = Path(args.perf_data)
    output_root = Path(args.output_dir or _load_output_dir(Path(args.config)))
    output_dir = output_root / task_id
    output_dir.mkdir(parents=True, exist_ok=True)

    if not perf_data.is_file() or perf_data.stat().st_size == 0:
        _fail("perf.data 不存在或为空")

    # 1. perf script → 原始栈文本
    script_path = output_dir / "perf.script.txt"
    ok, err = _perf_script(perf_data, script_path)
    if not ok:
        _fail(f"perf script 失败: {err}")
    if not _has_non_whitespace(script_path):
        _fail_quality(
            "NO_PERF_SAMPLES",
            "perf.data 未包含可供火焰图分析的采样事件",
            "确认目标进程在采样期间仍存活且正在承载负载；适当延长采样时长，"
            "并检查 perf_event_paranoid/CAP_PERFMON 后重新采集。",
            details={
                "perf_data_bytes": perf_data.stat().st_size,
                "perf_script_bytes": script_path.stat().st_size,
            },
        )

    # 2. stackcollapse → 折叠栈
    collapsed_path = output_dir / "collapsed.txt"
    ok, err = _stackcollapse(script_path, collapsed_path)
    if not ok:
        _fail(f"stackcollapse 失败: {err}")

    quality = _folded_stack_quality(collapsed_path)
    if quality["sample_count"] <= 0 or quality["valid_stack_lines"] <= 0:
        _fail_quality(
            "NO_FOLDED_STACKS",
            "perf 事件存在，但没有得到可渲染的正样本调用栈",
            "确认采集命令启用了调用栈（perf record -g），并检查 unwind/帧指针、"
            "符号权限和 stackcollapse 输入格式后重新采集。",
            details={
                **quality,
                "perf_script_bytes": script_path.stat().st_size,
                "collapsed_bytes": collapsed_path.stat().st_size,
            },
        )

    # 3. flamegraph.pl → fallback SVG
    svg_path = output_dir / "flamegraph.svg"
    _flamegraph_svg(collapsed_path, svg_path)

    # 4. 解析折叠栈 → TopN JSON + flamegraph JSON 树
    top_n = _parse_top(collapsed_path)
    top_path = output_dir / "top.json"
    top_path.write_text(json.dumps(top_n, indent=2, ensure_ascii=False))

    flame_tree = _build_flame_tree(collapsed_path)
    tree_path = output_dir / "flamegraph.json"
    tree_text = json.dumps(flame_tree, separators=(",", ":"), ensure_ascii=False)
    tree_path.write_text(tree_text)

    # 4b. 同一份折叠栈再生成调用关系图。火焰图回答“时间落在哪条栈上”，
    # 调用图回答“谁调用谁”，两者不能互相替代。
    call_graph = _build_call_graph(collapsed_path)
    call_graph_path = output_dir / "callgraph.json"
    call_graph_path.write_text(
        json.dumps(call_graph, separators=(",", ":"), ensure_ascii=False)
    )

    # 5. 规则引擎 → suggestions
    suggestions = _match_rules(top_n)
    sugg_path = output_dir / "suggestions.md"
    sugg_path.write_text(suggestions)

    summary = {
        "task_id": task_id,
        "status": "SUCCESS",
        "summary": suggestions.split("\n")[0] if suggestions else "分析完成",
        "sample_count": quality["sample_count"],
        "profile_quality": {
            "status": "USABLE",
            "reason_code": "OK",
            "valid_stack_lines": quality["valid_stack_lines"],
        },
        "top_functions": top_n[:5],
        "output_files": {
            "flamegraph_json": str(tree_path),
            "flamegraph_svg": str(svg_path),
            "top_json": str(top_path),
            "callgraph_json": str(call_graph_path),
            "suggestions_md": str(sugg_path),
        },
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def _fail(msg: str) -> None:
    print(json.dumps({"status": "FAILED", "error": msg}))
    raise SystemExit(1)


def _fail_quality(
    reason_code: str,
    message: str,
    action_hint: str,
    *,
    details: dict | None = None,
) -> None:
    """Emit a stable, machine-readable sample-quality failure.

    ``perf script`` and the FlameGraph Perl tools deliberately return zero for
    an input that contains no samples.  That is a valid command execution but
    not a usable profile.  The structured payload lets the worker persist an
    actionable reason instead of registering empty result artifacts.
    """

    print(json.dumps({
        "status": "FAILED",
        "error_code": QUALITY_ERROR_CODE,
        "failure_kind": QUALITY_FAILURE_KIND,
        "reason_code": reason_code,
        "message": message,
        "action_hint": action_hint,
        "details": details or {},
    }, ensure_ascii=False, separators=(",", ":")))
    raise SystemExit(2)


def _has_non_whitespace(path: Path) -> bool:
    """Check a potentially large analyzer intermediate without loading it all."""

    try:
        with path.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                if chunk.strip():
                    return True
    except OSError:
        return False
    return False


def _load_output_dir(config_path: Path) -> str:
    """读取 analyzer 配置中的 output_dir，缺失时返回默认值。

    当前只需要一个配置项，使用轻量解析避免为 Python 3.10 额外引入 tomli。
    """
    default = "/tmp/mini-drop-analyzer"
    if not config_path.is_file():
        return default

    in_analyzer = False
    for raw_line in config_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            in_analyzer = line == "[analyzer]"
            continue
        if in_analyzer and line.startswith("output_dir"):
            _, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            return value or default
    return default


# ---------------------------------------------------------------------------
# 步骤 1: perf script
# ---------------------------------------------------------------------------


def _perf_script(perf_data: Path, output: Path) -> tuple[bool, str]:
    perf = shutil.which("perf")
    if perf is None:
        return False, "perf 命令不可用"
    try:
        subprocess.run(
            [
                perf,
                "script",
                # Do not emit the perf event period.  stackcollapse-perf treats
                # that field as a weight; for sampled CPU profiles this turned
                # a few hundred observations into billions of fake "samples"
                # and incorrectly inflated AI evidence quality scores.
                "-F",
                "comm,pid,tid,time,event,ip,sym,dso",
                "-i",
                str(perf_data),
            ],
            stdout=output.open("w"),
            stderr=subprocess.PIPE,
            check=True,
            timeout=60,
        )
        return True, ""
    except subprocess.CalledProcessError as exc:
        return False, exc.stderr.decode("utf-8", errors="replace")[:200]
    except subprocess.TimeoutExpired:
        return False, "perf script 超时"


# ---------------------------------------------------------------------------
# 步骤 2: stackcollapse-perf.pl
# ---------------------------------------------------------------------------


def _stackcollapse(input_path: Path, output_path: Path) -> tuple[bool, str]:
    script = Path(__file__).resolve().parent.parent / "scripts" / "stackcollapse-perf.pl"
    if not script.is_file():
        return False, f"stackcollapse-perf.pl 未找到: {script}"
    try:
        subprocess.run(
            ["perl", str(script), str(input_path)],
            stdout=output_path.open("w"),
            stderr=subprocess.PIPE,
            check=True,
            timeout=30,
        )
        return True, ""
    except subprocess.CalledProcessError as exc:
        return False, exc.stderr.decode("utf-8", errors="replace")[:200]
    except subprocess.TimeoutExpired:
        return False, "stackcollapse 超时"


# ---------------------------------------------------------------------------
# 步骤 3: flamegraph.pl → SVG
# ---------------------------------------------------------------------------


def _flamegraph_svg(collapsed: Path, output: Path) -> None:
    script = Path(__file__).resolve().parent.parent / "scripts" / "flamegraph.pl"
    if not script.is_file():
        output.write_text(_fallback_svg("flamegraph.pl 未找到"))
        return
    try:
        subprocess.run(
            ["perl", str(script), str(collapsed)],
            stdout=output.open("w"),
            stderr=subprocess.PIPE,
            check=True,
            timeout=60,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        output.write_text(_fallback_svg("火焰图生成失败"))


def _fallback_svg(msg: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="120">'
        f'<text x="20" y="60" font-size="16" fill="#999">{msg}</text>'
        "</svg>\n"
    )


# ---------------------------------------------------------------------------
# 步骤 4: 解析折叠栈
# ---------------------------------------------------------------------------

MAX_TREE_DEPTH = 50


def _folded_stack_quality(collapsed: Path) -> dict[str, int]:
    """Return conservative quality counters for folded-stack input.

    Only a non-empty stack with a strictly positive integer weight represents
    a renderable observation.  Blank, malformed, zero and negative rows must
    never inflate the flame-tree root or make an empty capture look usable.
    """

    sample_count = 0
    valid_stack_lines = 0
    malformed_lines = 0
    with collapsed.open("r", encoding="utf-8", errors="replace") as stream:
        for raw_line in stream:
            line = raw_line.rstrip()
            if not line:
                continue
            if " " not in line:
                malformed_lines += 1
                continue
            stack, _, count_str = line.rpartition(" ")
            try:
                count = int(count_str)
            except ValueError:
                malformed_lines += 1
                continue
            frames = [frame.strip() for frame in stack.split(";") if frame.strip()]
            if count <= 0 or not frames:
                malformed_lines += 1
                continue
            sample_count += count
            valid_stack_lines += 1
    return {
        "sample_count": sample_count,
        "valid_stack_lines": valid_stack_lines,
        "malformed_lines": malformed_lines,
    }


def _parse_top(collapsed: Path, limit: int = 20) -> list[dict]:
    """从折叠栈文本解析 TopN 热点函数。

    折叠栈格式（一行一个栈）：
      func1;func2;func3 1234
    """
    counter: dict[str, int] = {}
    total = 0
    with collapsed.open("r") as fh:
        for line in fh:
            if " " not in line:
                continue
            stack, _, count_str = line.rstrip().rpartition(" ")
            try:
                count = int(count_str)
            except ValueError:
                continue
            funcs = [func.strip() for func in stack.split(";") if func.strip()]
            if count <= 0 or not funcs:
                continue
            total += count
            for func in funcs:
                counter[func] = counter.get(func, 0) + count

    entries = sorted(counter.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [
        {"name": name, "samples": cnt, "percent": round(cnt / total * 100, 1) if total else 0}
        for name, cnt in entries
    ]


def _build_flame_tree(collapsed: Path) -> dict:
    """从折叠栈构建 d3-flame-graph JSON 树。

    返回格式：{"name":"root","value":total,"children":[...]}
    深度超过 MAX_TREE_DEPTH 时截断。
    """
    root: dict = {"name": "root", "value": 0, "children": []}
    node_map: dict[str, dict] = {"": root}

    with collapsed.open("r") as fh:
        for line in fh:
            if " " not in line:
                continue
            stack, _, count_str = line.rstrip().rpartition(" ")
            try:
                count = int(count_str)
            except ValueError:
                continue
            funcs = [f.strip() for f in stack.split(";") if f.strip()]
            if count <= 0 or not funcs:
                continue
            root["value"] += count

            depth = min(len(funcs), MAX_TREE_DEPTH)
            for i in range(depth):
                func = funcs[i]
                prefix = ";".join(funcs[: i + 1])
                parent_key = ";".join(funcs[:i]) if i > 0 else ""
                parent = node_map.get(parent_key)
                if parent is None:
                    break

                if prefix not in node_map:
                    node: dict = {"name": func, "value": 0}
                    node.setdefault("children", [])
                    parent.setdefault("children", []).append(node)
                    node_map[prefix] = node
                node_map[prefix]["value"] += count

    return root


def _build_call_graph(collapsed: Path, limit_nodes: int = 120) -> dict:
    """Build a bounded caller/callee graph from folded stacks.

    ``inclusive_samples`` counts every stack containing the frame, while
    ``self_samples`` counts only leaf samples.  Edges are directed caller ->
    callee and deduplicated across all stacks.  Keeping the hottest bounded
    node set prevents a single noisy profile from freezing the browser.
    """
    inclusive: dict[str, int] = {}
    self_samples: dict[str, int] = {}
    edges: dict[tuple[str, str], int] = {}
    total = 0
    with collapsed.open("r", encoding="utf-8") as fh:
        for line in fh:
            if " " not in line:
                continue
            stack, _, count_str = line.rstrip().rpartition(" ")
            try:
                count = int(count_str)
            except ValueError:
                continue
            frames = [frame.strip() for frame in stack.split(";") if frame.strip()]
            if not frames or count <= 0:
                continue
            total += count
            self_samples[frames[-1]] = self_samples.get(frames[-1], 0) + count
            for frame in set(frames):
                inclusive[frame] = inclusive.get(frame, 0) + count
            for caller, callee in zip(frames, frames[1:]):
                if caller != callee:
                    key = (caller, callee)
                    edges[key] = edges.get(key, 0) + count

    hottest = sorted(inclusive, key=lambda name: (-inclusive[name], name))[:limit_nodes]
    retained = set(hottest)
    nodes = [
        {
            "id": name,
            "name": name,
            "inclusive_samples": inclusive[name],
            "self_samples": self_samples.get(name, 0),
            "percent": round(inclusive[name] / total * 100, 2) if total else 0,
        }
        for name in hottest
    ]
    links = [
        {
            "source": caller,
            "target": callee,
            "samples": samples,
            "percent": round(samples / total * 100, 2) if total else 0,
        }
        for (caller, callee), samples in sorted(
            edges.items(), key=lambda item: (-item[1], item[0])
        )
        if caller in retained and callee in retained
    ]
    return {
        "schema_version": "perf_callgraph.v1",
        "total_samples": total,
        "truncated": len(inclusive) > limit_nodes,
        "node_limit": limit_nodes,
        "nodes": nodes,
        "links": links,
    }


# ---------------------------------------------------------------------------
# 步骤 5: 规则引擎
# ---------------------------------------------------------------------------

_DEFAULT_RULES: list[dict[str, str]] = [
    {"regex": r"(?i)fib",         "advice": "检测到 Fibonacci 递归热点，建议改用迭代 + 记忆化或查表法替代"},
    {"regex": r"(?i)sort",        "advice": "排序开销较高，检查数据集大小，考虑原地排序或基数排序替代"},
    {"regex": r"(?i)json",        "advice": "JSON 编解码占用 CPU 显著，检查是否存在不必要的重复序列化"},
    {"regex": r"(?i)malloc",      "advice": "malloc 调用频繁，考虑使用内存池或 jemalloc 分配器"},
    {"regex": r"(?i)lock|mutex",  "advice": "锁竞争热点，检查临界区长度，考虑无锁结构或读写锁替代"},
    {"regex": r"(?i)strcmp|strncpy|strlen",
                                  "advice": "字符串操作密集，考虑使用 string_view 或预计算长度"},
    {"regex": r"(?i)io_submit|blk_mq|vfs_read|vfs_write",
                                  "advice": "内核 IO 路径出现热点，建议结合 eBPF 采集器确认 IO 延迟"},
]


def _match_rules(top_n: list[dict]) -> str:
    lines: list[str] = []
    for entry in top_n[:10]:
        for rule in _DEFAULT_RULES:
            if re.search(rule["regex"], entry["name"]):
                lines.append(f"- **{entry['name']}** ({entry['percent']}%): {rule['advice']}")
    if not lines:
        lines.append("- 未命中预置规则，建议结合 AI 归因深入分析")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
