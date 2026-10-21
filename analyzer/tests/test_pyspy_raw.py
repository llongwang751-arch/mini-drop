"""明确导入的缩减夹具验证 py-spy 空栈兼容；不执行采样器、业务或模型。

正例情境模拟 repository 关联的可信 runner_metadata；这不把测试夹具冒充生产采集，
也不允许实际用户导入接口靠制品自报 runner 就进入兼容分支。
"""

from dataclasses import replace
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from drop_analyzer.errors import AnalysisError
from drop_analyzer.folded import Limits, analyze_folded
from drop_analyzer.pyspy_raw import (
    QUALITY_SOURCE,
    UNKNOWN_FRAME,
    UNKNOWN_STACK,
    analyze_pyspy_raw,
    trusted_pyspy_source,
)
from drop_analyzer.registry import get_pipeline
from drop_analyzer.result import make_result


FIXTURES = Path(__file__).parent / "fixtures"


class PySpyRawAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="drop-imported-pyspy-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.raw = (FIXTURES / "pyspy-empty-stack-reduced.folded").read_bytes()

    def context(self, raw=None):
        raw = self.raw if raw is None else raw
        path = self.directory / "explicit-imported-fixture.folded"
        path.write_bytes(raw)
        return {
            "pipeline": "python_cpu_v1",
            "kind": "PYTHON_CPU",
            "format": "folded",
            "raw_path": str(path),
            "work_dir": str(self.directory),
            "params": {"duration_seconds": 8, "frequency_hz": 49},
            "artifact": {
                "content_type": "text/x-collapsed",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": len(raw),
                "metadata": {"fixture": True},
                "runner_metadata": {
                    "schema_version": 1,
                    "kind": "PYTHON_CPU",
                    "runner": "py-spy",
                },
            },
        }

    def test_reduced_real_shape_preserves_all_380_weights_and_records_unknown_one(self):
        provenance = json.loads(
            (FIXTURES / "pyspy-empty-stack-reduced.json").read_text()
        )
        self.assertIs(provenance["imported"], True)
        context = self.context()
        pipeline = get_pipeline("python_cpu_v1")
        pipeline.validate(context)
        parsed = pipeline.analyze(self.raw, context)
        quality = parsed["sections"]["sampling_quality"]
        self.assertEqual(parsed["metadata"]["sample_count"], 380)
        self.assertEqual(parsed["flamegraph"]["value"], 380)
        self.assertEqual(quality["source"], QUALITY_SOURCE)
        self.assertEqual(quality["unresolved_sample_weight"], 1)
        self.assertEqual(quality["unresolved_stack_lines"], 1)
        self.assertEqual(quality["placeholder_count"], 1)
        self.assertIs(quality["normalized"], True)
        unknown = next(
            row for row in parsed["top"]["self"] if row["name"] == UNKNOWN_STACK
        )
        self.assertEqual(unknown["self"], 1)
        self.assertEqual(sum(row["self"] for row in parsed["top"]["self"]), 380)
        self.assertEqual(Path(context["raw_path"]).read_bytes(), self.raw)
        manifest = pipeline.build_manifest(
            {**context, "inputs": [context["artifact"]]}, parsed
        )
        self.assertEqual(
            manifest["input_artifacts"][0]["sha256"], context["artifact"]["sha256"]
        )
        result = make_result(parsed, "PYTHON_CPU", context["params"])
        self.assertEqual(
            result["sections"]["sampling_quality"]["total_sample_weight"], 380
        )
        self.assertTrue(
            any("不是真实业务函数" in text for text in result["summary"]["limitations"])
        )
        self.assertEqual(
            parsed["metadata"]["pyspy_raw_quality"]["unresolved_sample_weight"], 1
        )

    def test_only_trusted_attempt_source_enables_adapter_not_user_metadata_or_kind(self):
        mutations = {
            "missing_attempt_metadata": lambda c: c["artifact"].pop(
                "runner_metadata"
            ),
            "metadata_only_spoof": lambda c: c["artifact"].update(
                metadata=c["artifact"].pop("runner_metadata")
            ),
            "params_import": lambda c: c["params"].update(source="import"),
            "artifact_import": lambda c: c["artifact"]["metadata"].update(
                source="import"
            ),
            "runner_import": lambda c: c["artifact"]["runner_metadata"].update(
                source="import"
            ),
            "provenance_import": lambda c: c["artifact"]["runner_metadata"].update(
                provenance="import"
            ),
            "wrong_runner": lambda c: c["artifact"]["runner_metadata"].update(
                runner="other"
            ),
            "wrong_runner_kind": lambda c: c["artifact"]["runner_metadata"].update(
                kind="JAVA_CPU"
            ),
            "wrong_schema": lambda c: c["artifact"]["runner_metadata"].update(
                schema_version=2
            ),
            "bool_schema": lambda c: c["artifact"]["runner_metadata"].update(
                schema_version=True
            ),
            "wrong_dispatch": lambda c: c.update(pipeline="folded_v1"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                context = self.context()
                mutate(context)
                self.assertFalse(trusted_pyspy_source(context))
                with self.assertRaises(AnalysisError) as failure:
                    get_pipeline("python_cpu_v1").analyze(self.raw, context)
                self.assertEqual(failure.exception.code, "INVALID_INPUT")
        for kind, pipeline in (
            ("IMPORT_COLLAPSED", "folded_v1"),
            ("PERF_CPU", "folded_v1"),
            ("JAVA_CPU", "java_cpu_v1"),
        ):
            with self.subTest(kind=kind):
                context = self.context()
                context.update(kind=kind, pipeline=pipeline)
                self.assertFalse(trusted_pyspy_source(context))
                with self.assertRaises(AnalysisError):
                    get_pipeline(pipeline).analyze(self.raw, context)
        with self.assertRaises(AnalysisError):
            analyze_folded(self.raw)

    def test_known_frames_are_unchanged_and_multiple_unknown_positions_count_weight_once(self):
        clean = analyze_pyspy_raw(b"root;leaf 10\n")
        self.assertEqual(
            clean["flamegraph"], analyze_folded(b"root;leaf 10\n")["flamegraph"]
        )
        quality = clean["metadata"]["pyspy_raw_quality"]
        self.assertIs(quality["normalized"], False)
        self.assertEqual(quality["unresolved_sample_weight"], 0)
        mixed = analyze_pyspy_raw(b";known;;leaf; 7\nroot;ok 3\n")
        quality = mixed["sections"]["sampling_quality"]
        self.assertEqual(quality["placeholder_count"], 3)
        self.assertEqual(quality["unresolved_stack_lines"], 1)
        self.assertEqual(quality["unresolved_sample_weight"], 7)
        self.assertEqual(mixed["flamegraph"]["value"], 10)
        unknown = next(
            row for row in mixed["top"]["inclusive"] if row["name"] == UNKNOWN_FRAME
        )
        self.assertEqual(unknown["inclusive"], 7)
        unknown_only = analyze_pyspy_raw(b" 1\r\n")
        self.assertEqual(unknown_only["metadata"]["sample_count"], 1)
        self.assertEqual(unknown_only["top"]["self"][0]["name"], UNKNOWN_STACK)

    def test_adapter_does_not_relax_invalid_weights_controls_or_reserved_marker_collision(self):
        for raw in (
            b"",
            b" \n",
            b" 0\n",
            b" -1\n",
            b" 1.5\n",
            b" \xd9\xa1\n",
            b"root;\x00 1\n",
            b" ;known 1\n",
            (UNKNOWN_STACK + " 1\n").encode(),
            (UNKNOWN_FRAME + " 1\n").encode(),
            b"root 9007199254740991\n 1\n",
        ):
            with self.subTest(raw_sha256=hashlib.sha256(raw).hexdigest()):
                with self.assertRaises(AnalysisError):
                    analyze_pyspy_raw(raw)

    def test_normalization_expansion_preserves_all_limits(self):
        for raw, limits in (
            (b";" * 32 + b" 1\n", replace(Limits(), max_input_bytes=64)),
            (b" 1\n", replace(Limits(), max_line_bytes=10)),
            (b";; 1\n", replace(Limits(), max_depth=2)),
            (b";a 1\n", replace(Limits(), max_nodes=2)),
            (b"root 1\n 1\n", replace(Limits(), max_stacks=1)),
        ):
            with self.subTest(limits=limits):
                with self.assertRaises(AnalysisError) as failure:
                    analyze_pyspy_raw(raw, limits=limits)
                self.assertEqual(failure.exception.code, "INPUT_LIMIT_EXCEEDED")

    def test_decode_integrity_still_precedes_adapter_and_gzip_keeps_original_digest(self):
        context = self.context()
        context["artifact"]["sha256"] = "0" * 64
        with self.assertRaises(AnalysisError) as failure:
            get_pipeline("python_cpu_v1").analyze(self.raw, context)
        self.assertEqual(failure.exception.code, "ARTIFACT_INTEGRITY_ERROR")
        packed = gzip.compress(self.raw, mtime=0)
        context = self.context(packed)
        context["artifact"]["content_type"] = "text/x-collapsed+gzip"
        parsed = get_pipeline("python_cpu_v1").analyze(packed, context)
        self.assertEqual(parsed["metadata"]["sample_count"], 380)
        self.assertEqual(
            parsed["metadata"]["pyspy_raw_quality"]["unresolved_sample_weight"], 1
        )
        self.assertEqual(
            context["artifact"]["sha256"], hashlib.sha256(packed).hexdigest()
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
