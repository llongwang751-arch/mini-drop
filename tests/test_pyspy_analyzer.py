from __future__ import annotations

import json
import subprocess
import sys

from analyzer.mini_drop_analyzer.pyspy_analyzer import analyze_speedscope, load_speedscope
from server.app.analyzer_runner import analyze_speedscope_artifacts


def _build_speedscope() -> dict:
    # Speedscope sampled profiles use root-first stacks over shared frames.
    return {
        "$schema": "https://www.speedscope.app/file-format-schema.json",
        "activeProfileIndex": 0,
        "profiles": [
            {
                "type": "sampled",
                "name": "python",
                "startValue": 0,
                "endValue": 150,
                "unit": "milliseconds",
                "samples": [
                    [0, 1, 2],  # main -> compute -> worker (leaf)
                    [0, 2],
                ],
                "weights": [100, 50],
            }
        ],
        "shared": {
            "frames": [
                {"name": "main", "file": "app.py", "line": 10},
                {"name": "compute", "file": "app.py", "line": 20},
                {"name": "worker", "file": "app.py", "line": 30},
            ]
        },
        "exporter": "py-spy",
    }


def test_load_speedscope_accepts_bytes():
    document = load_speedscope(json.dumps(_build_speedscope()).encode())
    assert document["activeProfileIndex"] == 0


def test_analyze_speedscope_rebuilds_top_and_flame_tree():
    result = analyze_speedscope(_build_speedscope())
    assert result["sample_count"] == 150
    by_name = {row["name"]: row for row in result["top"]}
    assert set(by_name) == {"worker"}
    assert by_name["worker"]["samples"] == 150

    tree = result["flamegraph"]
    assert tree["value"] == 150
    main = tree["children"][0]
    assert main["name"] == "main" and main["value"] == 150


def test_analyze_speedscope_counts_fractional_sampling_intervals():
    document = _build_speedscope()
    document["profiles"][0]["weights"] = [0.0101, 0.0101]
    result = analyze_speedscope(document)
    assert result["sample_count"] == 2
    assert result["top"][0] == {
        "name": "worker", "file": "app.py", "line": 30,
        "samples": 2, "percent": 100.0,
    }


def test_analyze_speedscope_aggregates_all_thread_profiles():
    document = _build_speedscope()
    document["profiles"].append({
        "type": "sampled",
        "name": "second-thread",
        "samples": [[0, 1]],
        "weights": [50],
    })
    result = analyze_speedscope(document)
    assert result["sample_count"] == 200
    assert {row["name"] for row in result["top"]} == {"worker", "compute"}


def test_pyspy_cli_writes_outputs(tmp_path):
    fixture = tmp_path / "pyspy-speedscope.json"
    fixture.write_text(json.dumps(_build_speedscope()), encoding="utf-8")
    out = tmp_path / "out"
    proc = subprocess.run(
        [
            sys.executable, "-m", "analyzer.mini_drop_analyzer.pyspy_analyzer",
            "--task-id", "task-pyspy", "--speedscope", str(fixture), "--output-dir", str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    top = json.loads((out / "task-pyspy" / "top.json").read_text())
    assert top and top[0]["name"] == "worker"
    flame = json.loads((out / "task-pyspy" / "flamegraph.json").read_text())
    assert flame["name"] == "root" and flame["value"] == 150


def test_pyspy_cli_rejects_invalid_document(tmp_path):
    fixture = tmp_path / "pyspy-speedscope.json"
    fixture.write_text("{ not json", encoding="utf-8")
    out = tmp_path / "out"
    proc = subprocess.run(
        [
            sys.executable, "-m", "analyzer.mini_drop_analyzer.pyspy_analyzer",
            "--task-id", "task-pyspy", "--speedscope", str(fixture), "--output-dir", str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "FAILED" in proc.stdout


def test_analyzer_runner_speedscope_integration(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
    fixture = tmp_path / "pyspy-speedscope.json"
    fixture.write_text(json.dumps(_build_speedscope()), encoding="utf-8")
    artifacts = [
        {
            "artifact_type": "raw",
            "filename": "pyspy-speedscope.json",
            "local_path": str(fixture),
            "content_type": "application/json",
        }
    ]
    outputs = analyze_speedscope_artifacts("task-pyspy", artifacts)
    assert len(outputs) == 2
    by_type = {item["artifact_type"]: item for item in outputs}
    assert "flamegraph_json" in by_type and "top_json" in by_type
    assert by_type["flamegraph_json"]["metadata"]["schema_version"] == "pyspy_analysis.v1"
    assert by_type["flamegraph_json"]["metadata"]["top_functions"][0]["name"] == "worker"
    assert by_type["flamegraph_json"]["metadata"]["top_functions"][0]["file"] == "app.py"
    assert by_type["flamegraph_json"]["metadata"]["top_functions"][0]["line"] == 30
