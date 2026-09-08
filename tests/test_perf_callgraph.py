import json
from pathlib import Path

from analyzer.mini_drop_analyzer.hotmethod_analyzer import _build_call_graph


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_build_call_graph_has_direction_self_and_inclusive_samples(tmp_path):
    folded = tmp_path / "collapsed.txt"
    folded.write_text("main;service;parse 7\nmain;service;write 3\nmain;idle 2\n")

    graph = _build_call_graph(folded)

    assert graph["schema_version"] == "perf_callgraph.v1"
    assert graph["total_samples"] == 12
    nodes = {item["id"]: item for item in graph["nodes"]}
    assert nodes["main"]["inclusive_samples"] == 12
    assert nodes["parse"]["self_samples"] == 7
    links = {(item["source"], item["target"]): item for item in graph["links"]}
    assert links[("main", "service")]["samples"] == 10
    assert links[("service", "parse")]["samples"] == 7


def test_build_call_graph_is_bounded(tmp_path):
    folded = tmp_path / "collapsed.txt"
    folded.write_text(";".join(f"f{i}" for i in range(10)) + " 1\n")
    graph = _build_call_graph(folded, limit_nodes=4)
    assert graph["truncated"] is True
    assert len(graph["nodes"]) == 4
    assert all(link["source"] in {n["id"] for n in graph["nodes"]} for link in graph["links"])
    json.dumps(graph)


def test_native_perf_collector_recovers_header_only_vm_capture_with_cpu_clock():
    source = (PROJECT_ROOT / "native/agent/src/perf_collector.cpp").read_text()

    assert "kHeaderOnlyThresholdBytes" in source
    assert 'perf_command("cpu-clock:u"' in source
    assert "software_event_recovery" in source
    assert 'requested_event == "cpu-cycles"' in source
