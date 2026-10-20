"""在无网络环境交叉解析独立采集的真实玩具 perf-script/perf.data 文件。

输入 manifest 只用于结果比较，不是可信现场元数据；两份采集中的内容都不会作为应用二进制执行。
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drop_analyzer import __version__
from drop_analyzer.isolate import analyze_isolated
from drop_analyzer.perf import scan_perf_loss
from drop_analyzer.pipeline import build_outputs
from drop_analyzer.result import make_result


def source_fingerprint():
    files = {
        path.relative_to(ROOT / "drop_analyzer").as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted((ROOT / "drop_analyzer").rglob("*.py"))
    }
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def verify_case(root, fmt, expected_comms):
    manifest_path = root / fmt / "manifest.json"
    encoded = manifest_path.read_bytes()
    if len(encoded) > 1024**2:
        raise ValueError("Fixture manifest exceeds verification bound")
    manifest = json.loads(encoded)
    if manifest["kind"] != "PERF_CPU" or manifest["format"] != fmt:
        raise ValueError("Fixture contract is not the expected PERF_CPU format")
    name = manifest["name"]
    if Path(name).name != name or not name:
        raise ValueError("Fixture name must be a local filename")
    path = manifest_path.parent / name
    if path.is_symlink() or path.stat().st_size > 32 * 1024**2:
        raise ValueError("Fixture raw path is unsafe or exceeds verification bound")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if (len(raw), digest) != (manifest["size"], manifest["sha256"]):
        raise ValueError("Actual fixture size or SHA256 differs from manifest")
    metadata = manifest.get("metadata", {})
    native_evidence = metadata.get("perf", {})
    expected_samples = native_evidence.get("sample_count")
    if type(expected_samples) is not int or expected_samples <= 0:
        raise ValueError("Core fixture requires a positive native sample_count")
    target_pid = metadata.get("target_pid") or native_evidence.get("filters", {}).get(
        "target_pid"
    )
    if type(target_pid) is not int or target_pid <= 0:
        raise ValueError("Core fixture requires an explicit target PID")
    with tempfile.TemporaryDirectory(prefix="verify-perf-toy-") as work:
        context = {
            "pipeline": "folded_v1",
            "kind": "PERF_CPU",
            "format": fmt,
            "raw_path": str(path.resolve()),
            "work_dir": work,
            "params": {"source": "import"},
            "artifact": {**manifest, "metadata": {"source": "import"}},
            "limits": {
                "address_space_bytes": 512 * 1024**2,
                "cpu_seconds": 90,
                "wall_seconds": 120,
                "output_bytes": 32 * 1024**2,
                "temp_bytes": 192 * 1024**2,
                "min_free_bytes": 16 * 1024**2,
            },
        }
        started = time.monotonic()
        analyzed = analyze_isolated(context, check_lease=lambda: None)
        result = make_result(analyzed["parsed"], "PERF_CPU", context["params"])
        perf = result["sections"]["perf"]
        threads = perf["threads"]
        assert result["summary"]["source"] == "import"
        assert result["total_samples"] == expected_samples
        assert sum(row["sample_count"] for row in threads) == expected_samples
        assert sum(perf["event_counts"].values()) == expected_samples
        assert expected_comms <= {row["comm"] for row in threads}
        assert all(row["pid"] == target_pid and row["tid"] > 0 for row in threads)
        nodes = {node["name"]: node for node in result["flamegraph"]["children"]}
        assert len(nodes) == len(threads)
        for row in threads:
            node = nodes[row["root_name"]]
            assert node["node_type"] == "thread"
            assert (node["pid"], node["tid"], node["comm"], node["value"]) == (
                row["pid"],
                row["tid"],
                row["comm"],
                row["sample_count"],
            )
            for key in ("first_timestamp_ns", "last_timestamp_ns"):
                assert isinstance(row[key], str) and row[key].isdecimal()
            assert int(row["last_timestamp_ns"]) >= int(row["first_timestamp_ns"])
        assert perf["timestamp_unit"] == "nanoseconds"
        assert int(perf["time_range_ns"]["duration"]) > 0
        assert all(" (" in row["name"] for row in result["top_functions"])
        if fmt == "perf_script":
            # 导入 JSON 不能作为“现场零丢样”的可信依据。
            assert perf["lost_samples"] is None
            native_scan = None
        else:
            native_scan = scan_perf_loss(raw)
            assert native_scan["sample_count"] == expected_samples
            assert perf["lost_samples"] == native_scan["lost_samples"]
            assert perf["lost_records"] == native_scan["lost_records"]
        outputs = build_outputs(
            analyzed["parsed"],
            task_id="imported-perf-toy",
            job_id="imported-perf-toy",
            input_sha256=digest,
            public_result=result,
        )
        return {
            "format": fmt,
            "input_bytes": len(raw),
            "input_sha256": digest,
            "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
            "total_samples": result["total_samples"],
            "threads": threads,
            "event_counts": perf["event_counts"],
            "time_range_ns": perf["time_range_ns"],
            "source": result["summary"]["source"],
            "symbolization": result["metadata"]["symbolization"],
            "collector_clock": native_evidence.get("clock"),
            "collector_loss_evidence": {
                key: native_evidence.get(key)
                for key in (
                    "loss_proof",
                    "lost_records",
                    "hardware_lost_samples",
                    "lost_samples",
                )
            },
            "native_input_scan": native_scan,
            "import_loss_status": perf["loss_status"],
            "import_lost_samples": perf["lost_samples"],
            "resource_usage": analyzed["metrics"],
            "artifact_bytes": {name: len(body) for name, body in outputs.items()},
            "artifact_sha256": {
                name: hashlib.sha256(body).hexdigest() for name, body in outputs.items()
            },
            "elapsed_seconds": round(time.monotonic() - started, 6),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    fingerprint = source_fingerprint()
    if fingerprint != args.expected_source:
        raise ValueError("Reviewed Analyzer source fingerprint differs")
    if sys.platform != "linux" or set(
        path.name for path in Path("/sys/class/net").iterdir()
    ) != {"lo"}:
        raise RuntimeError("Run this verifier in a Linux --network none container")
    cases = [
        verify_case(args.root, fmt, {"fixture-alpha", "fixture-beta"})
        for fmt in ("perf_script", "perf_data")
    ]
    report = {
        "provenance": "Actual Core Runner kernel captures of its own two-thread toy, imported into isolated Analyzer. Two independent records are checked against their own manifests; sample totals need not equal one another.",
        "analyzer_version": __version__,
        "source_tree_sha256": fingerprint,
        "network": "none; only loopback interface observed",
        "imported_application_elf_executed": False,
        "cases": cases,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    print(
        json.dumps(
            {
                "success": True,
                "formats": [row["format"] for row in cases],
                "samples": [row["total_samples"] for row in cases],
            }
        )
    )


if __name__ == "__main__":
    main()
