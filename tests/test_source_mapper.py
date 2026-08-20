from __future__ import annotations

from pathlib import Path

from server.app.diagnosis.source_mapper import map_hot_functions


def test_maps_python_hot_symbol_to_ast_location(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_text(
        "import time\n\n"
        "class Worker:\n"
        "    def hot_loop(self):\n"
        "        for _ in range(3):\n"
        "            time.sleep(0.01)\n",
        encoding="utf-8",
    )

    result = map_hot_functions(["Worker.hot_loop"], roots=[tmp_path])

    assert result["unresolved_symbols"] == []
    assert result["mappings"][0]["file"] == "service.py"
    assert result["mappings"][0]["qualname"] == "Worker.hot_loop"
    assert result["mappings"][0]["line_start"] == 4
    assert result["mappings"][0]["review_signals"] == ["blocking_or_wait_call", "loop"]


def test_unknown_and_unconfigured_sources_are_explicit(tmp_path: Path) -> None:
    result = map_hot_functions(["missing_function", "[unknown]"], roots=[tmp_path])

    assert result["mappings"] == []
    assert result["unresolved_symbols"] == ["missing_function"]


def test_maps_go_method_and_reports_concurrency_signals(tmp_path: Path) -> None:
    (tmp_path / "worker.go").write_text(
        "package demo\n\n"
        "type Worker struct{}\n\n"
        "func (w *Worker) HotLoop(ch chan int) {\n"
        "    for value := range ch {\n"
        "        go func() { _ = value }()\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    result = map_hot_functions(["Worker.HotLoop"], roots=[tmp_path])

    mapping = result["mappings"][0]
    assert result["unresolved_symbols"] == []
    assert mapping["language"] == "go"
    assert mapping["line_start"] == 5
    assert mapping["line_end"] == 9
    assert "goroutine_or_channel" in mapping["review_signals"]


def test_maps_cpp_qualified_function(tmp_path: Path) -> None:
    (tmp_path / "worker.cpp").write_text(
        "#include <unistd.h>\n\n"
        "int Worker::hot_loop(int count) {\n"
        "    while (count-- > 0) {\n"
        "        usleep(1000);\n"
        "    }\n"
        "    return count;\n"
        "}\n",
        encoding="utf-8",
    )

    result = map_hot_functions(["Worker::hot_loop"], roots=[tmp_path])

    mapping = result["mappings"][0]
    assert result["unresolved_symbols"] == []
    assert mapping["language"] == "cpp"
    assert mapping["qualname"] == "Worker::hot_loop"
    assert mapping["line_start"] == 3
    assert mapping["line_end"] == 8
    assert mapping["review_signals"] == ["blocking_or_wait_call", "loop"]


def test_maps_java_nested_class_symbol_from_real_fixture() -> None:
    source_root = (
        Path(__file__).resolve().parents[1]
        / "external"
        / "opentelemetry-demo"
        / "src"
        / "ad"
        / "src"
        / "main"
        / "java"
        / "oteldemo"
        / "problempattern"
    )

    result = map_hot_functions(
        ["oteldemo.problempattern.CPULoad$Logarithmizer.run"],
        roots=[source_root],
    )

    mapping = result["mappings"][0]
    assert result["unresolved_symbols"] == []
    assert result["scanned_by_language"]["java"] == 1
    assert mapping["language"] == "java"
    assert mapping["file"] == "CPULoad.java"
    assert mapping["qualname"] == "CPULoad.Logarithmizer.run"
    assert mapping["line_start"] == 100
    assert mapping["line_end"] == 104
    assert mapping["review_signals"] == ["loop"]


def test_maps_ruby_method_extent_from_real_fixture() -> None:
    source_root = (
        Path(__file__).resolve().parents[1]
        / "external"
        / "opentelemetry-demo"
        / "src"
        / "email"
    )

    result = map_hot_functions(["send_email"], roots=[source_root])

    mapping = result["mappings"][0]
    assert result["unresolved_symbols"] == []
    assert result["scanned_by_language"]["ruby"] == 1
    assert mapping["language"] == "ruby"
    assert mapping["file"] == "email_server.rb"
    assert mapping["qualname"] == "send_email"
    assert mapping["line_start"] == 61
    assert mapping["line_end"] == 102


def test_ruby_postfix_modifier_does_not_extend_method(tmp_path: Path) -> None:
    (tmp_path / "worker.rb").write_text(
        "def work(items)\n"
        "  items.each do |item|\n"
        "    process(item) if item.ready?\n"
        "  end\n"
        "end\n"
        "\n"
        "def later\n"
        "  wait\n"
        "end\n",
        encoding="utf-8",
    )

    result = map_hot_functions(["work", "missing"], roots=[tmp_path])

    mapping = result["mappings"][0]
    assert mapping["qualname"] == "work"
    assert mapping["line_start"] == 1
    assert mapping["line_end"] == 5
    assert result["unresolved_symbols"] == ["missing"]
