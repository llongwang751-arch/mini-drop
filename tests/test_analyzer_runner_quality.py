"""Fail-closed profile quality checks at the Analyzer Worker boundary."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from server.app import analyzer_runner


def _quality_failure(reason_code: str) -> bytes:
    return json.dumps({
        "status": "FAILED",
        "error_code": "ANALYSIS_INPUT_INVALID",
        "failure_kind": "SAMPLE_QUALITY",
        "reason_code": reason_code,
        "message": "profile has no samples",
        "action_hint": "run the target under load and collect again",
        "details": {"sample_count": 0},
    }).encode()


def test_raw_perf_runner_preserves_structured_empty_sample_failure(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
    perf_data = tmp_path / "perf.data"
    perf_data.write_bytes(b"PERFILE2" + b"\x00" * 7600)
    monkeypatch.setattr(
        analyzer_runner.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=2,
            stdout=_quality_failure("NO_PERF_SAMPLES"),
            stderr=b"",
        ),
    )

    with pytest.raises(analyzer_runner.AnalyzerQualityError) as exc:
        analyzer_runner.analyze_raw_perf_artifacts(
            "task-empty",
            [{
                "artifact_type": "raw",
                "filename": "perf.data",
                "local_path": str(perf_data),
            }],
        )

    payload = json.loads(str(exc.value))
    assert payload["error_code"] == "ANALYSIS_INPUT_INVALID"
    assert payload["failure_kind"] == "SAMPLE_QUALITY"
    assert payload["reason_code"] == "NO_PERF_SAMPLES"
    assert payload["details"]["sample_count"] == 0
    assert payload["action_hint"]


def test_output_collector_rejects_zero_sample_flamegraph(tmp_path):
    output_dir = tmp_path / "task-empty"
    output_dir.mkdir()
    (output_dir / "flamegraph.json").write_text(
        json.dumps({"name": "root", "value": 0, "children": []})
    )
    (output_dir / "top.json").write_text("[]")
    (output_dir / "flamegraph.svg").write_text("<svg></svg>")

    with pytest.raises(analyzer_runner.AnalyzerQualityError) as exc:
        analyzer_runner._collect_analyzer_outputs(output_dir)

    assert json.loads(str(exc.value))["reason_code"] == "NO_PROFILE_SAMPLES"


def test_output_collector_marks_valid_profile_as_usable(tmp_path):
    output_dir = tmp_path / "task-valid"
    output_dir.mkdir()
    (output_dir / "flamegraph.json").write_text(
        json.dumps({
            "name": "root",
            "value": 12,
            "children": [{"name": "work", "value": 12}],
        })
    )
    (output_dir / "top.json").write_text(json.dumps([
        {"name": "work", "samples": 12, "percent": 100.0},
    ]))
    (output_dir / "flamegraph.svg").write_text("<svg></svg>")
    (output_dir / "callgraph.json").write_text(json.dumps({
        "schema_version": "perf_callgraph.v1",
        "total_samples": 12,
        "nodes": [{
            "name": "work",
            "inclusive_samples": 12,
            "self_samples": 10,
            "percent": 100.0,
        }],
        "links": [],
    }))
    (output_dir / "suggestions.md").write_text("inspect work")

    outputs = analyzer_runner._collect_analyzer_outputs(output_dir)

    assert outputs
    for artifact in outputs:
        metadata = artifact["metadata"]
        assert metadata["sample_count"] == 12
        assert metadata["profile_quality"] == {
            "status": "USABLE",
            "reason_code": "OK",
            "sample_count": 12,
            "renderable_function_count": 1,
        }
        assert metadata["top_functions"][0]["self_samples"] == 10
        assert metadata["top_functions"][0]["self_percent"] == 83.33


def test_async_profiler_html_is_decoded_without_executing_javascript(
    tmp_path,
    monkeypatch,
):
    html = """<html><head><title>Allocation Flame Graph</title></head><body>
<script>
const cpool = ['all', ' Hotspot.allocate'];
unpack(cpool);
n(3,120)
u(11,90)
</script></body></html>"""
    path = tmp_path / "java-flamegraph.html"
    path.write_text(html, encoding="utf-8")
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))

    updates = analyzer_runner.analyze_java_flamegraph_artifacts(
        [{
            "id": 7,
            "artifact_type": "java_flamegraph_html",
            "local_path": str(path),
            "metadata": {"collector_plugin": "java_async"},
        }]
    )

    metadata = updates[7]
    assert metadata["sample_count"] == 120
    assert metadata["profile_event"] == "alloc"
    assert metadata["top_functions"][0] == {
        "name": "Hotspot.allocate",
        "samples": 90,
        "percent": 75.0,
    }
    assert metadata["profile_quality"]["status"] == "USABLE"


def test_async_profiler_event_is_read_from_the_real_h1_heading(tmp_path, monkeypatch):
    html = """<!DOCTYPE html><html><head></head><body>
<h1>Allocation profile</h1>
<script>
const cpool = ['all', ' Hotspot.allocate'];
unpack(cpool);
n(3,64)
u(11,64)
</script></body></html>"""
    path = tmp_path / "java-flamegraph.html"
    path.write_text(html, encoding="utf-8")
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))

    updates = analyzer_runner.analyze_java_flamegraph_artifacts(
        [{
            "id": 8,
            "artifact_type": "java_flamegraph_html",
            "local_path": str(path),
            "metadata": {"collector_plugin": "java_async"},
        }]
    )

    assert updates[8]["profile_event"] == "alloc"


def test_java_flamegraph_merges_same_window_jvm_counters(tmp_path, monkeypatch):
    html = """<html><head><title>Allocation profile</title></head><body><script>
const cpool = ['all', ' Hotspot.allocate'];
unpack(cpool);
n(3,120)
u(11,90)
</script></body></html>"""
    flamegraph = tmp_path / "java-flamegraph.html"
    counters = tmp_path / "jvm-gc-metrics.json"
    flamegraph.write_text(html, encoding="utf-8")
    counters.write_text(
        json.dumps({
            "schema_version": "jvm_gc_metrics.v1",
            "event": "alloc",
            "before_captured_at_unix_ms": 1000,
            "after_captured_at_unix_ms": 16000,
            "before": {
                "runtime": "java", "pid": 7, "host_pid": 77,
                "allocated_bytes": 1024, "gc_count": 3,
                "gc_time_ms": 12, "heap_used_bytes": 4096,
            },
            "after": {
                "runtime": "java", "pid": 7, "host_pid": 77,
                "allocated_bytes": 8192, "gc_count": 8,
                "gc_time_ms": 31, "heap_used_bytes": 4608,
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))

    updates = analyzer_runner.analyze_java_flamegraph_artifacts([
        {
            "id": 9,
            "artifact_type": "java_flamegraph_html",
            "local_path": str(flamegraph),
            "metadata": {},
        },
        {
            "id": 10,
            "artifact_type": "jvm_gc_metrics",
            "local_path": str(counters),
            "metadata": {},
        },
    ])

    assert updates[10]["schema_version"] == "jvm_gc_metrics.v1"
    assert updates[10]["sample_count"] == 2
    assert updates[10]["delta"] == {
        "allocated_bytes": 7168,
        "gc_count": 5,
        "gc_time_ms": 19,
        "heap_used_bytes": 512,
    }
    assert updates[9]["jvm_gc_counters"]["gc_activity_observed"] is True
