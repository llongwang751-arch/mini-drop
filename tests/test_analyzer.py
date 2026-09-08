"""Analyzer 单元测试。

测试覆盖折叠栈解析、火焰图 JSON 树构造、规则匹配、
空数据/缺失工具的错误处理。
"""

import json
import re
import sys
from unittest import mock

import pytest

from analyzer.mini_drop_analyzer.hotmethod_analyzer import (
    _build_flame_tree,
    _folded_stack_quality,
    _load_output_dir,
    _match_rules,
    _parse_top,
    _perf_script,
)
from analyzer.mini_drop_analyzer import hotmethod_analyzer

# 模拟折叠栈文本
_COLLAPSED_SAMPLE = (
    "fib_hotspot;fib_hotspot;fib_hotspot 3500\n"
    "fib_hotspot;fib_hotspot 2000\n"
    "sort_hotspot;sort_hotspot;__libc_start_main 1200\n"
    "sort_hotspot;sort_hotspot 800\n"
    "json_hotspot;json.dumps;json.encoder 500\n"
    "json_hotspot;json.loads 300\n"
)


class TestParseTop:
    """TopN 热点解析。"""

    def test_top_functions_sorted(self, tmp_path):
        collapsed = tmp_path / "collapsed.txt"
        collapsed.write_text(_COLLAPSED_SAMPLE)
        top = _parse_top(collapsed, limit=10)
        # 折叠栈中每个函数在多个栈行中出现，独立函数名 ≥ 6 个
        assert len(top) >= 6
        assert top[0]["name"] == "fib_hotspot"
        assert top[0]["samples"] > top[1]["samples"]

    def test_top_percent_sum(self, tmp_path):
        collapsed = tmp_path / "collapsed.txt"
        collapsed.write_text(_COLLAPSED_SAMPLE)
        top = _parse_top(collapsed)
        # samples 在各栈行间可能重复计数，不检验 sum
        assert top[0]["percent"] > 0

    def test_empty_collapsed_file(self, tmp_path):
        collapsed = tmp_path / "collapsed.txt"
        collapsed.write_text("")
        top = _parse_top(collapsed)
        assert top == []

    def test_malformed_lines_skipped(self, tmp_path):
        collapsed = tmp_path / "collapsed.txt"
        collapsed.write_text("func_a;func_b 100\nbad-line\nfunc_c 50\n\n")
        top = _parse_top(collapsed)
        # func_a, func_b, func_c 共 3 个独立函数名
        assert len(top) == 3

    def test_non_positive_or_frameless_rows_are_not_samples(self, tmp_path):
        collapsed = tmp_path / "collapsed.txt"
        collapsed.write_text("valid;leaf 3\nzero;leaf 0\nnegative;leaf -2\n 4\n")

        quality = _folded_stack_quality(collapsed)
        tree = _build_flame_tree(collapsed)
        top = _parse_top(collapsed)

        assert quality == {
            "sample_count": 3,
            "valid_stack_lines": 1,
            "malformed_lines": 3,
        }
        assert tree["value"] == 3
        assert {row["name"] for row in top} == {"valid", "leaf"}


def test_perf_script_omits_event_period_so_sample_count_is_observation_count(
    tmp_path, monkeypatch
):
    perf_data = tmp_path / "perf.data"
    perf_data.write_bytes(b"perf")
    output = tmp_path / "perf.script.txt"
    captured = {}

    monkeypatch.setattr(
        "analyzer.mini_drop_analyzer.hotmethod_analyzer.shutil.which",
        lambda name: "/usr/bin/perf",
    )

    def fake_run(command, **kwargs):
        captured["command"] = command
        return mock.Mock(returncode=0)

    monkeypatch.setattr(
        "analyzer.mini_drop_analyzer.hotmethod_analyzer.subprocess.run",
        fake_run,
    )

    ok, error = _perf_script(perf_data, output)

    assert ok is True
    assert error == ""
    fields = captured["command"][captured["command"].index("-F") + 1]
    assert "period" not in fields


class TestFlameTree:
    """火焰图 JSON 树构造。"""

    def test_tree_has_root_structure(self, tmp_path):
        collapsed = tmp_path / "collapsed.txt"
        collapsed.write_text("a;b;c 100\na;b 200\nd;e 50\n")
        tree = _build_flame_tree(collapsed)

        assert tree["name"] == "root"
        assert tree["value"] == 350
        assert "children" in tree

    def test_tree_depth_truncation(self, tmp_path):
        """超过 MAX_TREE_DEPTH 深度的栈应被截断。"""
        # 构造 60 层调用栈
        funcs = [f"f{i}" for i in range(60)]
        stack = ";".join(funcs) + " 10\n"
        collapsed = tmp_path / "collapsed.txt"
        collapsed.write_text(stack)

        tree = _build_flame_tree(collapsed)
        # 验证不会超过 50 层
        max_depth = 0

        def walk(node, depth):
            nonlocal max_depth
            max_depth = max(max_depth, depth)
            for child in node.get("children", []):
                walk(child, depth + 1)

        walk(tree, 0)
        assert max_depth <= 50

    def test_empty_input(self, tmp_path):
        collapsed = tmp_path / "collapsed.txt"
        collapsed.write_text("")
        tree = _build_flame_tree(collapsed)
        assert tree["name"] == "root"
        assert tree["value"] == 0


class TestRules:
    """规则引擎匹配。"""

    def test_fib_hotspot_triggers_rule(self):
        top = [{"name": "fib_hotspot", "samples": 100, "percent": 68.5}]
        result = _match_rules(top)
        assert "Fibonacci" in result
        assert "记忆化" in result

    def test_sort_hotspot_triggers_rule(self):
        top = [{"name": "sort_hotspot", "samples": 50, "percent": 30.0}]
        result = _match_rules(top)
        assert "排序" in result

    def test_json_triggers_rule(self):
        top = [{"name": "json.dumps", "samples": 20, "percent": 10.0}]
        result = _match_rules(top)
        assert "JSON" in result

    def test_no_match_returns_fallback(self):
        top = [{"name": "unknown_func", "samples": 10, "percent": 5.0}]
        result = _match_rules(top)
        assert "未命中" in result

    def test_lock_hotspot_triggers_mutex_rule(self):
        top = [{"name": "pthread_mutex_lock", "samples": 80, "percent": 50.0}]
        result = _match_rules(top)
        assert "锁竞争" in result or "互斥" in result


class TestAnalyzerConfig:
    """Analyzer 配置读取。"""

    def test_load_output_dir_from_config(self, tmp_path):
        config = tmp_path / "config.toml"
        config.write_text('[analyzer]\noutput_dir = "/tmp/custom-analyzer"\n')
        assert _load_output_dir(config) == "/tmp/custom-analyzer"

    def test_load_output_dir_uses_default_when_missing(self, tmp_path):
        assert _load_output_dir(tmp_path / "missing.toml") == "/tmp/mini-drop-analyzer"


def test_cli_rejects_header_only_perf_data_with_machine_readable_reason(
    tmp_path,
    monkeypatch,
    capsys,
):
    perf_data = tmp_path / "perf.data"
    perf_data.write_bytes(b"PERFILE2" + b"\x00" * 7600)
    output_root = tmp_path / "out"

    def fake_perf_script(_source, output):
        output.write_text("")
        return True, ""

    monkeypatch.setattr(hotmethod_analyzer, "_perf_script", fake_perf_script)
    monkeypatch.setattr(
        hotmethod_analyzer,
        "_stackcollapse",
        lambda *_args: pytest.fail("empty perf script must stop before stackcollapse"),
    )
    monkeypatch.setattr(sys, "argv", [
        "hotmethod-analyzer",
        "--task-id", "task-empty",
        "--perf-data", str(perf_data),
        "--output-dir", str(output_root),
    ])

    with pytest.raises(SystemExit) as exc:
        hotmethod_analyzer.main()

    payload = json.loads(capsys.readouterr().out)
    assert exc.value.code == 2
    assert payload["failure_kind"] == "SAMPLE_QUALITY"
    assert payload["reason_code"] == "NO_PERF_SAMPLES"
    assert payload["details"]["perf_data_bytes"] == 7608
    assert "目标进程" in payload["action_hint"]
    assert not (output_root / "task-empty" / "flamegraph.json").exists()


def test_cli_rejects_events_without_foldable_call_stacks(
    tmp_path,
    monkeypatch,
    capsys,
):
    perf_data = tmp_path / "perf.data"
    perf_data.write_bytes(b"non-empty")
    output_root = tmp_path / "out"

    def fake_perf_script(_source, output):
        output.write_text("worker 42 event without a callchain\n")
        return True, ""

    def fake_stackcollapse(_source, output):
        output.write_text("")
        return True, ""

    monkeypatch.setattr(hotmethod_analyzer, "_perf_script", fake_perf_script)
    monkeypatch.setattr(hotmethod_analyzer, "_stackcollapse", fake_stackcollapse)
    monkeypatch.setattr(sys, "argv", [
        "hotmethod-analyzer",
        "--task-id", "task-no-stacks",
        "--perf-data", str(perf_data),
        "--output-dir", str(output_root),
    ])

    with pytest.raises(SystemExit) as exc:
        hotmethod_analyzer.main()

    payload = json.loads(capsys.readouterr().out)
    assert exc.value.code == 2
    assert payload["reason_code"] == "NO_FOLDED_STACKS"
    assert payload["details"]["sample_count"] == 0
    assert "perf record -g" in payload["action_hint"]
