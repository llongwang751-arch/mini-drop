"""以独立格式适配器实现显式的分析生命周期。

worker 准备并校验输入后，适配器依次 validate、prepare、analyze、build_manifest。
任务 kind、pipeline 与原始格式必须匹配；导入数据会保留来源标记，不冒充现场采集。
"""

from dataclasses import dataclass
from pathlib import Path
import platform

from .errors import AnalysisError
from .events import analyze_io, analyze_memory, analyze_resources, analyze_script
from .external import analyze_jfr, analyze_memray, optimize_bolt, symbolize_native
from .folded import Limits, analyze_folded
from .hprof import analyze_hprof
from .native import analyze_bolt, native_cpu, native_heap
from .pipeline import decode_input
from .pprof import analyze_pprof
from .progress import write_stage
from .perf import analyze_perf_script, analyze_perf_data


@dataclass(frozen=True)
class Pipeline:
    pipeline: str
    kinds: tuple[str, ...]
    formats: tuple[str, ...]
    source: str
    high_memory: bool = False

    def validate(self, context):
        if context["kind"] not in self.kinds or context["format"] not in self.formats:
            raise AnalysisError(
                "UNSUPPORTED_FORMAT", "Task kind, pipeline and raw format do not match"
            )
        if (
            context["format"] in {"perf_script", "perf_data"}
            and context["kind"] != "PERF_CPU"
        ):
            raise AnalysisError(
                "UNSUPPORTED_FORMAT", "Native perf formats require PERF_CPU"
            )
        path = Path(context["raw_path"])
        if not path.is_file() or path.stat().st_size > Limits().max_input_bytes:
            raise AnalysisError(
                "INPUT_LIMIT_EXCEEDED", "Raw artifact exceeds analysis byte limit"
            )
        if (
            context.get("binary_path")
            and Path(context["binary_path"]).read_bytes()[:4] != b"\x7fELF"
        ):
            raise AnalysisError("INVALID_INPUT", "Auxiliary native binary must be ELF")

    def prepare(self, context):
        # 工作目录与不可变输入由 worker 预先准备；这里不执行由 profile 控制的命令或路径。
        self.validate(context)
        return Path(context["raw_path"]).read_bytes()

    def analyze(self, raw, context):
        """按已验证的原始格式分派到专用解析器，返回聚合树与对应类型的 sections。

        BOLT 可处理受控 ELF 并生成优化文件，但这里不会执行上传二进制；
        所有分支最终补齐 source/provenance，导入记录不能伪装成采集端自检数据。
        """
        kind, fmt = context["kind"], context["format"]
        params = {
            **context.get("params", {}),
            **context.get("params", {}).get("options", {}),
        }
        work_dir = Path(context["work_dir"])
        if fmt == "folded":
            artifact = context["artifact"]
            decoded = decode_input(
                raw,
                artifact["content_type"],
                artifact["sha256"],
                artifact["size"],
                Limits(),
            )
            parsed = analyze_folded(decoded)
        elif fmt == "perf_script":
            metadata = context["artifact"].get("runner_metadata") or context[
                "artifact"
            ].get("metadata", {})
            trusted = (
                metadata.get("perf")
                if metadata.get("source") != "import"
                and context.get("params", {}).get("source") != "import"
                else None
            )
            parsed = analyze_perf_script(raw, collector_perf=trusted)
        elif fmt == "perf_data":
            parsed = analyze_perf_data(raw, path=context["raw_path"], work_dir=work_dir)
        elif fmt == "pprof":
            parsed = analyze_pprof(raw, kind=kind, params=params)
        elif fmt == "jsonl":
            parsed = analyze_io(raw) if kind == "IO_TRACE" else analyze_memory(raw)
        elif fmt == "json":
            parsed = (
                analyze_resources(raw)
                if kind == "RESOURCE_SNAPSHOT"
                else analyze_script(raw)
            )
        elif fmt == "memray":
            parsed = analyze_memray(context["raw_path"])
        elif fmt == "hprof":
            parsed = analyze_hprof(
                raw,
                work_dir=context.get("index_dir"),
                limits=context.get("hprof_limits"),
                on_progress=lambda stage: write_stage(work_dir, stage),
            )
        elif fmt == "jfr":
            parsed = analyze_jfr(context["raw_path"], work_dir=work_dir)
        elif fmt == "gperf_cpu":
            parsed = native_cpu(raw)
        elif fmt in {"gperf_heap", "jeprof_heap"}:
            parsed = native_heap(raw, kind=kind, params=params)
        elif fmt == "bolt_fdata":
            parsed = analyze_bolt(raw)
            if context.get("binary_path") and params.get("optimize", True):
                optimization = optimize_bolt(
                    context["raw_path"], context["binary_path"], work_dir=work_dir
                )
                parsed["sections"]["binary"]["optimization"] = optimization
                parsed["extra_outputs"] = [
                    {"name": "optimized.elf", "content_type": "application/x-elf"}
                ]
            elif not context.get("binary_path"):
                parsed["sections"]["binary"]["limitations"].append(
                    "未附目标 ELF，仅分析 fdata，未生成优化二进制。"
                )
        else:
            raise AnalysisError(
                "UNSUPPORTED_FORMAT", "No decoder is registered for this raw format"
            )
        if fmt in {"gperf_cpu", "gperf_heap", "jeprof_heap"} and context.get(
            "binary_path"
        ):
            parsed = symbolize_native(parsed, context["binary_path"], work_dir=work_dir)
        source = {
            "PERF_CPU": "perf",
            "PROC_CPU": "procfs",
            "IMPORT_COLLAPSED": "import",
        }.get(kind, self.source)
        if fmt == "jfr":
            source = "jfr"
        imported = (
            kind == "IMPORT_COLLAPSED"
            or context.get("params", {}).get("source") == "import"
            or context["artifact"].get("metadata", {}).get("source") == "import"
        )
        parsed["metadata"].update(
            {
                "source": "import" if imported else source,
                "profile_source": source,
                "provenance": "import" if imported else "collector_artifact",
                "input_format": fmt,
                "pipeline": self.pipeline,
                "queue_class": "high_memory" if self.high_memory else "small",
                "python_version": platform.python_version(),
                "collector": context["artifact"].get("runner_metadata")
                or context["artifact"].get("metadata", {}),
            }
        )
        return parsed

    def build_manifest(self, context, parsed):
        return {
            "source": parsed["metadata"]["source"],
            "input_format": context["format"],
            "pipeline": self.pipeline,
            "schema_version": 1,
            "metadata": parsed["metadata"],
            "limits": context.get("limits", {}),
            "input_artifacts": [
                {
                    key: str(artifact[key])
                    if key == "id" and artifact.get(key) is not None
                    else artifact.get(key)
                    for key in ("id", "name", "format", "size", "sha256", "metadata")
                }
                for artifact in context.get("inputs", [])
            ],
        }

    def cleanup(self, context):
        # 完整私有工作目录由持有者清理；只有以摘要命名的可恢复 HPROF 索引
        # 能留在有容量限制的缓存中。
        return None


PIPELINES = [
    Pipeline(
        "folded_v1",
        ("PERF_CPU", "PROC_CPU", "IMPORT_COLLAPSED"),
        ("folded", "perf_script", "perf_data"),
        "folded",
    ),
    Pipeline("java_cpu_v1", ("JAVA_CPU",), ("folded", "jfr"), "async-profiler"),
    Pipeline("python_cpu_v1", ("PYTHON_CPU",), ("folded",), "py-spy"),
    Pipeline("pprof_cpu_v1", ("GO_CPU",), ("pprof",), "go-pprof"),
    Pipeline("pprof_heap_v1", ("GO_HEAP",), ("pprof",), "go-pprof", True),
    Pipeline("memray_v1", ("PYTHON_MEMORY",), ("memray",), "memray", True),
    Pipeline("hprof_v1", ("JAVA_HEAP",), ("hprof",), "hotspot-hprof", True),
    Pipeline("io_trace_v1", ("IO_TRACE",), ("jsonl",), "linux-trace"),
    Pipeline("memory_trace_v1", ("MEMORY_TRACE",), ("jsonl",), "linux-trace"),
    Pipeline("gperf_cpu_v1", ("GPERF_CPU",), ("gperf_cpu",), "gperftools"),
    Pipeline("gperf_heap_v1", ("GPERF_HEAP",), ("gperf_heap",), "gperftools", True),
    Pipeline("jeprof_heap_v1", ("JEPROF_HEAP",), ("jeprof_heap",), "jemalloc", True),
    Pipeline("bolt_v1", ("BOLT_PROFILE",), ("bolt_fdata",), "llvm-bolt"),
    Pipeline("script_v1", ("SCRIPT",), ("json",), "fixed-template"),
    Pipeline("resources_v1", ("RESOURCE_SNAPSHOT",), ("json",), "procfs"),
]
REGISTRY = {pipeline.pipeline: pipeline for pipeline in PIPELINES}
KIND_PIPELINES = {kind: pipeline for pipeline in PIPELINES for kind in pipeline.kinds}


def get_pipeline(pipeline):
    if pipeline not in REGISTRY:
        raise AnalysisError(
            "UNSUPPORTED_FORMAT", "Task analysis pipeline is not registered"
        )
    return REGISTRY[pipeline]
