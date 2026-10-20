"""将每份导入的真实格式 profile 送入实际隔离注册表，验证格式与分析链路。"""

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

NAMES = {
    "PERF_CPU": "perf.folded",
    "PROC_CPU": "proc.folded",
    "IMPORT_COLLAPSED": "../golden.folded",
    "JAVA_CPU": "java.folded",
    "PYTHON_CPU": "python.folded",
    "GO_CPU": "go-cpu.pprof",
    "GO_HEAP": "go-heap.pprof",
    "JAVA_HEAP": "java-heap.hprof",
    "PYTHON_MEMORY": "python-memory.bin",
    "IO_TRACE": "io.jsonl",
    "MEMORY_TRACE": "memory.jsonl",
    "GPERF_CPU": "gperf-cpu.profile",
    "GPERF_HEAP": "gperf-heap.profile",
    "JEPROF_HEAP": "jemalloc-heap.profile",
    "BOLT_PROFILE": "bolt.fdata",
    "SCRIPT": "script.json",
    "RESOURCE_SNAPSHOT": "resources.json",
}


def main():
    fixtures = ROOT / "tests" / "fixtures" / "collected"
    results = []
    cases = [
        (kind, path, KIND_PIPELINES[kind].formats[0]) for kind, path in NAMES.items()
    ]
    cases.append(("JAVA_CPU", "java-cpu.jfr", "jfr"))
    if "--jfr-only" in sys.argv:
        cases = [case for case in cases if case[2] == "jfr"]
    for kind, filename, fmt in cases:
        path = (fixtures / filename).resolve()
        row = {"kind": kind, "file": path.name, "input_format": fmt}
        results.append(row)
        if not path.exists():
            row.update(status="missing_fixture")
            continue
        raw = path.read_bytes()
        row.update(input_bytes=len(raw), input_sha256=hashlib.sha256(raw).hexdigest())
        pipeline = KIND_PIPELINES[kind]
        with tempfile.TemporaryDirectory(prefix="verify-profile-") as directory:
            context = {
                "pipeline": pipeline.pipeline,
                "kind": kind,
                "format": fmt,
                "raw_path": str(path),
                "work_dir": directory,
                "index_dir": str(Path(directory) / "indexes"),
                "params": {"source": "import"},
                "artifact": {
                    "metadata": {"source": "import"},
                    "content_type": "text/plain"
                    if fmt == "folded"
                    else "application/octet-stream",
                    "size": len(raw),
                    "sha256": row["input_sha256"],
                },
                "limits": {
                    "address_space_bytes": 512 * 1024**2,
                    "cpu_seconds": 90,
                    "wall_seconds": 120,
                    "output_bytes": 32 * 1024**2,
                },
            }
            binary = {
                "GPERF_CPU": "native-target.elf",
                "GPERF_HEAP": "native-target.elf",
                "JEPROF_HEAP": "jemalloc-target.elf",
                "BOLT_PROFILE": "bolt-target.elf",
            }.get(kind)
            if binary and (fixtures / binary).exists():
                context["binary_path"] = str((fixtures / binary).resolve())
            started = time.monotonic()
            try:
                isolated = analyze_isolated(context, check_lease=lambda: None)
                result = make_result(isolated["parsed"], kind, context["params"])
                row.update(
                    status="success",
                    source=result["summary"]["source"],
                    profile_source=result["summary"]["profile_source"],
                    total_weight=result["total_samples"],
                    unit=result["summary"]["unit"],
                    nodes=result["node_count"],
                    sections=result["sections"],
                    resource_usage=isolated["metrics"],
                    parent_elapsed_seconds=round(time.monotonic() - started, 6),
                )
            except AnalysisError as error:
                row.update(
                    status="failed", error_code=error.code, error_message=str(error)
                )
                if fmt == "jfr":
                    stderr_path = Path(directory) / "jfr.json.stderr"
                    if stderr_path.exists():
                        row["fixture_tool_diagnostic"] = stderr_path.read_text(
                            errors="replace"
                        )[-2000:]
                    stdout_path = Path(directory) / "jfr.json"
                    if stdout_path.exists() and stdout_path.read_bytes().startswith(
                        b"Error occurred"
                    ):
                        row["fixture_vm_diagnostic"] = stdout_path.read_text(
                            errors="replace"
                        )[:1500]
        print(
            json.dumps({key: value for key, value in row.items() if key != "sections"}),
            flush=True,
        )
    output = (
        ROOT
        / "evidence"
        / (
            "native-format-jfr.json"
            if "--jfr-only" in sys.argv
            else "native-format-results.json"
        )
    )
    output.parent.mkdir(exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "provenance": "Imported test fixtures only; real disposable analysis subprocesses. Not production Runner coverage or an SLA.",
                "cases": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf8",
    )
    return int(any(row["status"] != "success" for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
