"""Measure imported synthetic 10k/50k/100k trees in the real isolated pipeline.

The reproducible star topology stresses unique nodes and output size. It is a
benchmark fixture, never evidence of a business capture or worst-case latency.
Run the entire script in a network-none, 512 MiB container to bound the parent
and child together, in addition to the child's 512 MiB address-space limit.
"""

import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drop_analyzer import __version__
from drop_analyzer.errors import AnalysisError
from drop_analyzer.isolate import analyze_isolated
from drop_analyzer.pipeline import build_outputs
from drop_analyzer.result import make_result


def environment_evidence():
    files = {
        str(path.relative_to(ROOT / "drop_analyzer")): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted((ROOT / "drop_analyzer").rglob("*.py"))
    }
    limits = {}
    for name in (
        "memory.max",
        "memory.peak",
        "memory.swap.max",
        "memory.events",
        "cpu.max",
    ):
        path = Path("/sys/fs/cgroup") / name
        if path.is_file():
            limits[name] = path.read_text().strip()
    return {
        "visible_logical_cpus": os.cpu_count(),
        "cgroup_v2": limits,
        "source_tree_sha256": hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def context_for(directory, raw):
    path = Path(directory) / "imported-benchmark.folded"
    path.write_bytes(raw)
    return {
        "pipeline": "folded_v1",
        "kind": "IMPORT_COLLAPSED",
        "format": "folded",
        "raw_path": str(path),
        "work_dir": directory,
        "params": {"source": "import"},
        "artifact": {
            "metadata": {"source": "import"},
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
            "content_type": "text/plain",
        },
        "limits": {
            "address_space_bytes": 512 * 1024**2,
            "cpu_seconds": 90,
            "wall_seconds": 120,
            "output_bytes": 32 * 1024**2,
        },
    }


def measure(nodes):
    raw = b"\n".join(f"function_{index:06d} 1".encode() for index in range(nodes - 1))
    with tempfile.TemporaryDirectory(prefix="drop-benchmark-") as directory:
        context = context_for(directory, raw)
        started = time.monotonic()
        isolated = analyze_isolated(context, check_lease=lambda: None)
        result = make_result(isolated["parsed"], context["kind"], context["params"])
        outputs = build_outputs(
            isolated["parsed"],
            task_id="benchmark-imported",
            job_id="benchmark-imported",
            input_sha256=context["artifact"]["sha256"],
            public_result=result,
        )
        assert result["node_count"] == nodes
        assert result["summary"]["source"] == "import"
        assert result["summary"]["duration_seconds"] is None
        assert (
            len(json.loads(gzip.decompress(outputs["flamegraph.json.gz"]))["children"])
            == nodes - 1
        )
        return {
            "nodes": nodes,
            "source": "import",
            "topology": "synthetic root plus unique function leaves",
            "input_bytes": len(raw),
            "input_sha256": context["artifact"]["sha256"],
            "pipeline_wall_seconds": round(time.monotonic() - started, 6),
            "child_metrics": isolated["metrics"],
            "public_result_bytes": len(
                json.dumps(result, separators=(",", ":")).encode()
            ),
            "artifact_bytes": {name: len(content) for name, content in outputs.items()},
            "artifact_sha256": {
                name: hashlib.sha256(content).hexdigest()
                for name, content in outputs.items()
            },
        }


def main():
    rows = [measure(nodes) for nodes in (10_000, 50_000, 100_000)]
    raw = b"\n".join(f"function_{index:06d} 1".encode() for index in range(100_000))
    with tempfile.TemporaryDirectory(prefix="drop-benchmark-over-limit-") as directory:
        try:
            analyze_isolated(context_for(directory, raw), check_lease=lambda: None)
        except AnalysisError as error:
            assert error.code == "INPUT_LIMIT_EXCEEDED"
            rejection = {
                "nodes": 100_001,
                "error_code": error.code,
                "retryable": error.retryable,
            }
        else:
            raise AssertionError("The bounded parser accepted 100,001 nodes")
    print(
        json.dumps(
            {
                "provenance": "Actual isolated Linux pipeline with imported synthetic benchmark fixtures; not business sampling.",
                "analyzer_version": __version__,
                "python": platform.python_version(),
                "architecture": platform.machine(),
                "execution_environment": environment_evidence(),
                "child_address_space_limit_bytes": 512 * 1024**2,
                "parent_max_rss_bytes": resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss
                * 1024,
                "limitations": [
                    "One run per size on a shared server; not a percentile or worst-case guarantee.",
                    "Star topology only; deeper trees and external tools have separate resource characteristics.",
                ],
                "cases": rows,
                "over_limit": rejection,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
