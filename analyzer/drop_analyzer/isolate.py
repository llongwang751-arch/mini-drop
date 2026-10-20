"""Parent-side resource and cancellation boundary around an untrusted profile."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from .errors import AnalysisError
from .disk import WorkspaceBudget
from .progress import ProgressReader


def terminate(process):
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def process_group_usage(group):
    if not sys.platform.startswith("linux"):
        return None
    count, ticks, rss = 0, 0, 0
    for path in Path("/proc").iterdir():
        if not path.name.isdecimal():
            continue
        try:
            fields = (path / "stat").read_text().rsplit(") ", 1)[1].split()
            if int(fields[2]) == group:
                count += 1
                ticks += sum(int(fields[index]) for index in (11, 12, 13, 14))
                rss += max(0, int(fields[21])) * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            continue
    return {
        "processes": count,
        "cpu_seconds": ticks / os.sysconf("SC_CLK_TCK"),
        "rss_bytes": rss,
    }


def analyze_isolated(context, *, check_lease, on_progress=None):
    work_dir = Path(context["work_dir"])
    disk = WorkspaceBudget(
        work_dir,
        max_bytes=context["limits"].get("temp_bytes", 6 * 32 * 1024**2),
        min_free_bytes=context["limits"].get("min_free_bytes", 16 * 1024**2),
        index_dir=context.get("index_dir") if context["kind"] == "JAVA_HEAP" else None,
        index_digest=context.get("artifact", {}).get("sha256"),
    )
    request, response, errors = (
        work_dir / "analysis-request.json",
        work_dir / "analysis-response.json",
        work_dir / "analysis.stderr",
    )
    request.write_text(
        json.dumps(context, ensure_ascii=False, default=str), encoding="utf8"
    )
    started = time.monotonic()
    progress = ProgressReader(work_dir, on_progress)
    timeout = context["limits"]["wall_seconds"]
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "PATH",
            "PYTHONPATH",
            "SYSTEMROOT",
            "SystemRoot",
            "WINDIR",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
        }
    }
    environment["HOME"] = str(work_dir)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    environment.update(TMPDIR=str(work_dir), TMP=str(work_dir), TEMP=str(work_dir))
    disk.check()
    observed_peak_rss, observed_processes = 0, 0
    with response.open("wb") as output, errors.open("wb") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-m", "drop_analyzer.child", str(request)],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=stderr,
            start_new_session=os.name == "posix",
            env=environment,
            cwd=work_dir,
        )
        try:
            while process.poll() is None:
                check_lease()
                disk.check()
                progress.drain()
                usage = process_group_usage(process.pid)
                if usage:
                    observed_peak_rss = max(observed_peak_rss, usage["rss_bytes"])
                    observed_processes = max(observed_processes, usage["processes"])
                    if usage["cpu_seconds"] > context["limits"]["cpu_seconds"]:
                        raise AnalysisError(
                            "ANALYSIS_CPU_LIMIT",
                            "Analysis process group exceeded its CPU budget",
                        )
                    if usage["processes"] > context["limits"].get("processes", 16):
                        raise AnalysisError(
                            "ANALYSIS_PROCESS_LIMIT",
                            "Analysis exceeded its process count budget",
                        )
                if time.monotonic() - started > timeout:
                    raise AnalysisError(
                        "ANALYSIS_TIMEOUT", "Analysis exceeded its wall clock budget"
                    )
                if (
                    response.stat().st_size > context["limits"]["output_bytes"]
                    or errors.stat().st_size > 1024 * 1024
                ):
                    raise AnalysisError(
                        "OUTPUT_LIMIT_EXCEEDED",
                        "Analysis subprocess output exceeded its limit",
                    )
                time.sleep(0.05)
        finally:
            terminate(process)
    check_lease()
    usage = disk.check()
    progress.drain()
    if process.returncode:
        raise AnalysisError(
            "ANALYSIS_RESOURCE_LIMIT",
            "Analysis subprocess was terminated or exceeded its resource budget",
        )
    try:
        value = json.loads(response.read_bytes())
    except (ValueError, RecursionError):
        raise AnalysisError(
            "ANALYSIS_DECODER_FAILED", "Analysis subprocess returned an invalid result"
        )
    if "error" in value:
        error = value["error"]
        raise AnalysisError(
            error["code"], error["message"], retryable=error.get("retryable", False)
        )
    value["metrics"].update(
        temp_disk_bytes=usage["total_bytes"],
        job_disk_bytes=usage["job_disk_bytes"],
        index_growth_bytes=usage["index_growth_bytes"],
        temp_disk_peak_bytes=disk.peak_bytes,
        observed_peak_tree_rss_bytes=observed_peak_rss or None,
        observed_processes=observed_processes or None,
        rss_sampling_interval_ms=50,
    )
    return value
