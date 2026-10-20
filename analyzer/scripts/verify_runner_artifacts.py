"""跨服务只读核验真实玩具 RunnerRegistry 制品，不创建新的采集任务。"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drop_analyzer.errors import AnalysisError
from drop_analyzer.isolate import analyze_isolated
from drop_analyzer.registry import KIND_PIPELINES
from drop_analyzer.result import make_result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    cases = []
    for root in args.roots:
        for path in sorted(root.glob("*/manifest.json")):
            manifest = json.loads(path.read_text())
            kind, fmt = manifest["kind"], manifest["format"]
            row = {
                "kind": kind,
                "format": fmt,
                "collector": manifest.get("metadata", {}).get("runner"),
                "fixture_source": manifest.get("fixture_source"),
                "collection_metadata": {
                    key: manifest.get("metadata", {}).get(key)
                    for key in ("target_pid", "start_time_ticks", "duration_ms")
                },
            }
            cases.append(row)
            filename = manifest["name"]
            if Path(filename).name != filename:
                raise ValueError("Fixture manifest name must be a local filename")
            raw_path = path.parent / filename
            raw = raw_path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            row.update(input_bytes=len(raw), input_sha256=digest)
            if digest != manifest["sha256"] or len(raw) != manifest["size"]:
                raise ValueError("Actual runner artifact integrity mismatch")
            request_path = path.parent / "request.json"
            request = (
                json.loads(request_path.read_text()) if request_path.exists() else {}
            )
            # 这是导入的核验夹具，不是新发起的现场采集。
            params = {**request.get("parameters", {}), "source": "import"}
            with tempfile.TemporaryDirectory(prefix="verify-runner-") as work:
                context = {
                    "pipeline": KIND_PIPELINES[kind].pipeline,
                    "kind": kind,
                    "format": fmt,
                    "raw_path": str(raw_path),
                    "work_dir": work,
                    "index_dir": str(Path(work) / "indexes"),
                    "params": params,
                    "artifact": {**manifest, "metadata": {"source": "import"}},
                    "runner_metadata": manifest.get("metadata", {}),
                    "limits": {
                        "address_space_bytes": 512 * 1024**2,
                        "cpu_seconds": 90,
                        "wall_seconds": 120,
                        "output_bytes": 32 * 1024**2,
                    },
                }
                started = time.monotonic()
                try:
                    analyzed = analyze_isolated(context, check_lease=lambda: None)
                    result = make_result(analyzed["parsed"], kind, params)
                    row.update(
                        status="success",
                        source=result["summary"]["source"],
                        profile_source=result["summary"]["profile_source"],
                        total_weight=result["total_samples"],
                        unit=result["summary"]["unit"],
                        nodes=result["node_count"],
                        sections=result["sections"],
                        metadata=result["metadata"],
                        resource_usage=analyzed["metrics"],
                    )
                except AnalysisError as error:
                    row.update(
                        status="failed", error_code=error.code, message=str(error)
                    )
                row["elapsed_seconds"] = round(time.monotonic() - started, 6)
            print(
                json.dumps(
                    {
                        key: value
                        for key, value in row.items()
                        if key not in {"sections", "metadata"}
                    }
                ),
                flush=True,
            )
    args.output.write_text(
        json.dumps(
            {
                "provenance": "Actual production RunnerRegistry code collected isolated toy business processes; artifacts read-only imported into the frozen Analyzer image. No production business data or restart.",
                "cases": cases,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf8",
    )
    return int(not cases or any(case["status"] != "success" for case in cases))


if __name__ == "__main__":
    raise SystemExit(main())
