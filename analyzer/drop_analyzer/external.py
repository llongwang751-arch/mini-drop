"""只调用白名单内且有资源限制的解码工具；profile 始终作为数据处理。

工具通过 argv 启动，路径来自作业上下文。上传的 ELF 可供符号解析或 BOLT 优化，
不会在 Analyzer 中作为业务程序执行；缺失观测量明确表示不可用，不猜测为零。
"""

import json
import os
from pathlib import Path
import shutil
import subprocess

from .errors import AnalysisError
from .stacks import stack_result


def run_tool(
    argv, *, cwd, output_name, timeout=60, max_bytes=32 * 1024 * 1024, environment=None
):
    executable = shutil.which(argv[0])
    if not executable:
        raise AnalysisError(
            "ANALYZER_TOOL_MISSING",
            f"Required analysis tool {Path(argv[0]).name} is unavailable",
        )
    executable = str(Path(executable).resolve())
    destination = Path(cwd) / output_name
    error_file = Path(cwd) / (output_name + ".stderr")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(cwd),
        "TMPDIR": str(cwd),
        "TMP": str(cwd),
        "TEMP": str(cwd),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        **(environment or {}),
    }
    try:
        with destination.open("wb") as output, error_file.open("wb") as errors:
            process = subprocess.run(
                [executable, *map(str, argv[1:])],
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=errors,
                env=env,
                timeout=timeout,
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise AnalysisError(
            "ANALYZER_TOOL_TIMEOUT", "Analysis tool exceeded its execution time budget"
        ) from exc
    if (
        destination.stat().st_size > max_bytes
        or error_file.stat().st_size > 1024 * 1024
    ):
        raise AnalysisError(
            "OUTPUT_LIMIT_EXCEEDED", "Analysis tool output exceeded its byte budget"
        )
    if process.returncode:
        raise AnalysisError(
            "ANALYZER_TOOL_FAILED",
            f"Analysis tool {Path(argv[0]).name} rejected the profile",
        )
    return destination.read_bytes()


def analyze_jfr(path, *, work_dir):
    # jfr print 是 OpenJDK 解码器；转换后的 JSON 调用栈仍来自真实 JFR 事件。
    raw = run_tool(
        [
            "jfr",
            "print",
            "--json",
            "--stack-depth",
            "128",
            "--events",
            "jdk.ExecutionSample,jdk.NativeMethodSample,profiler.ExecutionSample",
            path,
        ],
        cwd=work_dir,
        output_name="jfr.json",
        environment={
            "JAVA_TOOL_OPTIONS": "-Xms16m -Xmx128m -Xss256k -XX:+UseSerialGC -XX:MaxMetaspaceSize=64m -XX:MaxDirectMemorySize=16m -XX:ReservedCodeCacheSize=32m -XX:ActiveProcessorCount=1 -XX:-UseCompressedOops -XX:-UseCompressedClassPointers -XX:-UsePerfData",
            "MALLOC_ARENA_MAX": "2",
        },
    )
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        raise AnalysisError("INVALID_INPUT", "JFR tool produced invalid JSON")
    events = value.get("recording", {}).get("events", [])
    if not isinstance(events, list) or len(events) > 200_000:
        raise AnalysisError(
            "INPUT_LIMIT_EXCEEDED", "JFR execution sample count exceeds limit"
        )
    stacks = []
    for event in events:
        fields = event.get("values", {})
        stack = fields.get("stackTrace") or {}
        frames = stack.get("frames", [])
        names = []
        for frame in reversed(frames):
            method = frame.get("method", {})
            cls = method.get("type", {}).get("name", "[unknown]").replace("/", ".")
            names.append(cls + "." + method.get("name", "[unknown]"))
        thread = fields.get("sampledThread") or fields.get("eventThread") or {}
        if thread.get("javaName"):
            names.insert(0, "thread:" + thread["javaName"])
        stacks.append((names, 1))
    if not stacks:
        raise AnalysisError(
            "INVALID_INPUT",
            "JFR recording contains no supported execution sample events",
        )
    return stack_result(
        stacks,
        metadata={
            "format": "jfr",
            "decoded_by": "OpenJDK jfr print",
            "event_count": len(events),
        },
    )


def analyze_memray(path):
    try:
        import memray
    except ImportError as exc:
        raise AnalysisError(
            "ANALYZER_TOOL_MISSING", "memray FileReader is unavailable on this worker"
        ) from exc
    try:
        with memray.FileReader(
            str(path), report_progress=False, max_memory_records=3600
        ) as reader:
            metadata = reader.metadata
            leaked, high, allocations = [], [], 0
            for index, record in enumerate(
                reader.get_leaked_allocation_records(merge_threads=True)
            ):
                if index >= 200_000:
                    raise AnalysisError(
                        "INPUT_LIMIT_EXCEEDED",
                        "memray allocation stack count exceeds limit",
                    )
                frames = [
                    f"{function} ({filename}:{line})"
                    for function, filename, line in reversed(
                        record.stack_trace(max_stacks=128)
                    )
                ]
                leaked.append(
                    {
                        "bytes": record.size,
                        "allocations": record.n_allocations,
                        "stack": frames,
                        "classification": "unfreed_at_capture_end",
                    }
                )
            for index, record in enumerate(
                reader.get_high_watermark_allocation_records(merge_threads=True)
            ):
                if index >= 200_000:
                    raise AnalysisError(
                        "INPUT_LIMIT_EXCEEDED",
                        "memray high watermark stack count exceeds limit",
                    )
                frames = [
                    f"{function} ({filename}:{line})"
                    for function, filename, line in reversed(
                        record.stack_trace(max_stacks=128)
                    )
                ]
                high.append((frames, record.size))
            snapshots = [
                {
                    "time_ns": snapshot.time * 1_000_000,
                    "rss_bytes": snapshot.rss,
                    "live_bytes": snapshot.heap,
                }
                for snapshot in reader.get_memory_snapshots()
            ][:3600]
            total_allocated, total_freed, free_count = None, None, None
            # 聚合 memray 文件本来就不包含完整事件流；无法取得的总量用 null 表示，
            # 不能把缺失信息当作零。
            if (
                getattr(metadata, "file_format", None)
                == memray.FileFormat.ALL_ALLOCATIONS
            ):
                total_allocated, total_freed, free_count = 0, 0, 0
                live = {}
                free_types = {
                    memray.AllocatorType.FREE,
                    memray.AllocatorType.MUNMAP,
                    memray.AllocatorType.PYMALLOC_FREE,
                }
                for index, record in enumerate(reader.get_allocation_records()):
                    if index >= 500_000:
                        raise AnalysisError(
                            "INPUT_LIMIT_EXCEEDED", "memray event count exceeds limit"
                        )
                    if record.allocator in free_types:
                        total_freed += live.pop(record.address, 0)
                        free_count += 1
                    else:
                        total_allocated += record.size
                        allocations += record.n_allocations
                        live[record.address] = record.size
            parsed = stack_result(
                high,
                unit="bytes",
                metadata={
                    "format": "memray",
                    "profile_type": "high_watermark",
                    "tool_version": memray.__version__,
                    "native_traces": bool(
                        getattr(metadata, "has_native_traces", False)
                    ),
                    "start_time": str(metadata.start_time),
                    "end_time": str(metadata.end_time),
                },
            )
            parsed["sections"] = {
                "memory": {
                    "allocated_bytes": total_allocated,
                    "freed_bytes": total_freed,
                    "live_bytes": sum(row["bytes"] for row in leaked),
                    "peak_live_bytes": getattr(
                        metadata, "peak_memory", sum(weight for _, weight in high)
                    ),
                    "allocations": allocations if total_allocated is not None else None,
                    "frees": free_count,
                    "unknown_frees": None,
                    "pagefaults": {"minor": None, "major": None},
                    "lost_events": None,
                    "survivors": sorted(leaked, key=lambda row: -row["bytes"])[:100],
                    "timeseries": snapshots,
                    "limitations": [
                        "memray 记录分配，不记录 page fault；捕获结束仍存活不等于泄漏。",
                        "聚合格式没有逐次 alloc/free 流，对应总量为 null。",
                    ],
                }
            }
            return parsed
    except AnalysisError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise AnalysisError(
            "INVALID_INPUT", "memray FileReader rejected the allocation capture"
        ) from exc


def symbolize_native(parsed, binary_path, *, work_dir):
    addresses = sorted(
        {
            row["name"]
            for row in parsed["top"]["inclusive"]
            if row["name"].startswith("0x")
        }
    )
    if not addresses:
        return parsed
    # 按完整树中的地址进行符号化，而非只看 TopN；地址数量另设上限。
    addresses = set()
    queue = [parsed["flamegraph"]]
    while queue:
        node = queue.pop()
        if node["name"].startswith("0x"):
            addresses.add(node["name"])
        queue.extend(node["children"])
    if len(addresses) > 10_000:
        raise AnalysisError(
            "INPUT_LIMIT_EXCEEDED", "Symbolization address count exceeds limit"
        )
    ordered = sorted(addresses)
    output = run_tool(
        ["addr2line", "-f", "-C", "-e", binary_path, *ordered],
        cwd=work_dir,
        output_name="symbols.txt",
        max_bytes=4 * 1024 * 1024,
    )
    lines = output.decode("utf8", errors="replace").splitlines()
    if len(lines) != 2 * len(ordered):
        raise AnalysisError(
            "ANALYZER_TOOL_FAILED", "Symbolizer output length mismatches addresses"
        )
    symbols = {
        address: lines[2 * index]
        for index, address in enumerate(ordered)
        if lines[2 * index] != "??"
    }
    stacks, queue = [], [(parsed["flamegraph"], [])]
    while queue:
        node, frames = queue.pop()
        if node["name"] != "all":
            frames = [*frames, symbols.get(node["name"], node["name"])]
        if node["self"]:
            stacks.append((frames, node["self"]))
        queue.extend((child, frames) for child in node["children"])
    result = stack_result(
        stacks,
        unit=parsed["metadata"]["unit"],
        metadata={
            **parsed["metadata"],
            "symbolization": "addr2line_partial",
            "resolved_addresses": len(symbols),
            "unresolved_addresses": len(ordered) - len(symbols),
        },
    )
    result["sections"] = parsed.get("sections", {})
    return result


def optimize_bolt(raw_path, binary_path, *, work_dir):
    output_path = Path(work_dir) / "optimized.elf"
    executable = "llvm-bolt" if shutil.which("llvm-bolt") else "llvm-bolt-16"
    output = run_tool(
        [
            executable,
            binary_path,
            "-o",
            output_path,
            "-data",
            raw_path,
            "-reorder-blocks=ext-tsp",
            "-reorder-functions=hfsort+",
            "-split-functions",
            "-split-all-cold",
            "-dyno-stats",
        ],
        cwd=work_dir,
        output_name="bolt-tool.txt",
        timeout=90,
        max_bytes=1024 * 1024,
    )
    if (
        not output_path.exists()
        or output_path.stat().st_size > 32 * 1024 * 1024
        or output_path.read_bytes()[:4] != b"\x7fELF"
    ):
        raise AnalysisError(
            "ANALYZER_TOOL_FAILED", "BOLT did not produce a bounded ELF artifact"
        )
    return {
        "output_file": "optimized.elf",
        "output_size": output_path.stat().st_size,
        "tool": executable,
        "tool_report": output.decode("utf8", errors="replace")[:16384],
        "status": "generated_requires_regression_validation",
    }
