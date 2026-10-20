"""One disposable analysis subprocess. Stdout contains one bounded JSON result."""

import json
from pathlib import Path
import sys
import time


def main():
    context = json.loads(Path(sys.argv[1]).read_text(encoding="utf8"))
    started, cpu_started = time.monotonic(), time.process_time()
    try:
        import resource
        import ctypes

        # Linux PR_SET_NO_NEW_PRIVS: analysis tools cannot gain privilege via
        # setuid/file capabilities, even when a malformed profile reaches them.
        ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0)
        budget = context["limits"]
        resource.setrlimit(
            resource.RLIMIT_AS,
            (budget["address_space_bytes"], budget["address_space_bytes"]),
        )
        resource.setrlimit(
            resource.RLIMIT_CPU, (budget["cpu_seconds"], budget["cpu_seconds"] + 1)
        )
        resource.setrlimit(
            resource.RLIMIT_FSIZE, (budget["output_bytes"], budget["output_bytes"])
        )
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except ImportError:
        resource = None
    from .errors import AnalysisError
    from .registry import get_pipeline

    try:
        pipeline = get_pipeline(context["pipeline"])
        raw = pipeline.prepare(context)
        try:
            parsed = pipeline.analyze(raw, context)
            manifest = pipeline.build_manifest(context, parsed)
        finally:
            pipeline.cleanup(context)
        subprocess_usage = (
            resource.getrusage(resource.RUSAGE_CHILDREN) if resource else None
        )
        analyzer_usage = resource.getrusage(resource.RUSAGE_SELF) if resource else None
        subprocess_cpu = (
            subprocess_usage.ru_utime + subprocess_usage.ru_stime
            if subprocess_usage
            else 0
        )
        metrics = {
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "cpu_seconds": round(time.process_time() - cpu_started + subprocess_cpu, 6),
            "subprocess_cpu_seconds": round(subprocess_cpu, 6),
            "subprocess_max_rss_bytes": subprocess_usage.ru_maxrss * 1024
            if subprocess_usage
            else None,
            "max_rss_bytes": max(analyzer_usage.ru_maxrss, subprocess_usage.ru_maxrss)
            * 1024
            if resource
            else None,
            "temp_disk_bytes": sum(
                path.stat().st_size
                for path in Path(context["work_dir"]).iterdir()
                if path.is_file()
            ),
            "index_cache_bytes": sum(
                path.stat().st_size
                for path in Path(context["index_dir"]).iterdir()
                if path.is_file()
            )
            if context.get("index_dir") and Path(context["index_dir"]).is_dir()
            else 0,
        }
        print(
            json.dumps(
                {"parsed": parsed, "manifest": manifest, "metrics": metrics},
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except AnalysisError as exc:
        print(
            json.dumps(
                {
                    "error": {
                        "code": exc.code,
                        "message": str(exc),
                        "retryable": exc.retryable,
                    }
                }
            )
        )
    except MemoryError:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "ANALYSIS_MEMORY_LIMIT",
                        "message": "Analysis exceeded its address space budget",
                        "retryable": False,
                    }
                }
            )
        )
    except Exception:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "ANALYSIS_DECODER_FAILED",
                        "message": "Profile decoder could not safely analyze this input",
                        "retryable": False,
                    }
                }
            )
        )


if __name__ == "__main__":
    main()
