from __future__ import annotations

import gzip
import json
import subprocess
import sys

from analyzer.mini_drop_analyzer.pprof_analyzer import (
    analyze_profile,
    load_profile,
)
from analyzer.mini_drop_analyzer.profile_pb2 import Function, Line, Location, Profile, Sample
from server.app.analyzer_runner import analyze_pprof_artifacts


def _build_profile_bytes() -> bytes:
    """Synthetic CPU profile: main -> {compute -> worker, worker}."""
    profile = Profile()
    # string_table: 0="", 1="main", 2="compute", 3="worker",
    #               4="app/main.go", 5="app/calc.go", 6="app/worker.go"
    profile.string_table.extend([
        "", "main", "compute", "worker",
        "app/main.go", "app/calc.go", "app/worker.go",
    ])
    profile.function.extend([
        Function(id=1, name=1, filename=4, start_line=10),
        Function(id=2, name=2, filename=5, start_line=20),
        Function(id=3, name=3, filename=6, start_line=30),
    ])
    profile.location.extend([
        Location(id=1, line=[Line(function_id=3)]),  # worker (leaf)
        Location(id=2, line=[Line(function_id=2)]),  # compute
        Location(id=3, line=[Line(function_id=1)]),  # main (root)
    ])
    # pprof samples are leaf-first: worker -> compute -> main
    profile.sample.extend([
        Sample(location_id=[1, 2, 3], value=[100]),
        Sample(location_id=[1, 3], value=[50]),
    ])
    return profile.SerializeToString()


def test_load_profile_handles_gzip_and_raw(tmp_path):
    raw = _build_profile_bytes()
    assert load_profile(raw).string_table[1] == "main"
    assert load_profile(gzip.compress(raw)).string_table[1] == "main"


def test_analyze_profile_rebuilds_top_and_flame_tree():
    result = analyze_profile(load_profile(_build_profile_bytes()))
    assert result["sample_count"] == 150

    by_name = {row["name"]: row for row in result["top"]}
    assert set(by_name) == {"main", "compute", "worker"}
    assert by_name["worker"]["samples"] == 150
    assert by_name["compute"]["samples"] == 100
    assert by_name["main"]["percent"] == 100.0
    # Source-level evidence chain: function -> file -> line.
    assert by_name["worker"]["file"] == "app/worker.go"
    assert by_name["worker"]["line"] == 30
    assert by_name["main"]["file"] == "app/main.go"
    assert by_name["compute"]["line"] == 20

    tree = result["flamegraph"]
    assert tree["value"] == 150
    main = tree["children"][0]
    assert main["name"] == "main" and main["value"] == 150
    compute = next(c for c in main["children"] if c["name"] == "compute")
    assert compute["value"] == 100


def test_pprof_cli_writes_outputs(tmp_path):
    fixture = tmp_path / "profile.pb.gz"
    fixture.write_bytes(gzip.compress(_build_profile_bytes()))
    out = tmp_path / "out"
    proc = subprocess.run(
        [
            sys.executable, "-m", "analyzer.mini_drop_analyzer.pprof_analyzer",
            "--task-id", "task-pprof", "--profile", str(fixture), "--output-dir", str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    top = json.loads((out / "task-pprof" / "top.json").read_text())
    assert top and top[0]["name"] in {"main", "worker"}
    flame = json.loads((out / "task-pprof" / "flamegraph.json").read_text())
    assert flame["name"] == "root" and flame["value"] == 150


def test_pprof_cli_rejects_corrupt_input(tmp_path):
    fixture = tmp_path / "profile.pb.gz"
    fixture.write_bytes(b"not a profile at all")
    out = tmp_path / "out"
    proc = subprocess.run(
        [
            sys.executable, "-m", "analyzer.mini_drop_analyzer.pprof_analyzer",
            "--task-id", "task-pprof", "--profile", str(fixture), "--output-dir", str(out),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1
    assert "FAILED" in proc.stdout


def test_analyzer_runner_pprof_integration(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(tmp_path))
    fixture = tmp_path / "profile.pb.gz"
    fixture.write_bytes(gzip.compress(_build_profile_bytes()))
    artifacts = [
        {
            "artifact_type": "pprof_raw",
            "filename": "profile.pb.gz",
            "local_path": str(fixture),
            "content_type": "application/gzip",
        }
    ]
    outputs = analyze_pprof_artifacts("task-pprof", artifacts)
    assert len(outputs) == 2
    by_type = {item["artifact_type"]: item for item in outputs}
    assert "flamegraph_json" in by_type and "top_json" in by_type
    assert by_type["flamegraph_json"]["metadata"]["schema_version"] == "go_pprof_analysis.v1"
    assert by_type["flamegraph_json"]["metadata"]["top_functions"][0]["name"] in {"main", "worker"}
