"""验证子进程阶段通道的固定记录、半行写入和输出上限，不触发现场采集。"""

import tempfile
from pathlib import Path
import unittest

from drop_analyzer.errors import AnalysisError
from drop_analyzer.hprof import analyze_hprof
from drop_analyzer.isolate import analyze_isolated
from drop_analyzer.progress import HPROF_STAGES, ProgressReader, write_stage
from test_hprof_native import hprof_fixture
import test_isolation_registry as isolation_tests


class AnalysisProgressTests(unittest.TestCase):
    def test_hprof_stages_are_ordered_for_fresh_and_resumed_indexes(self):
        with tempfile.TemporaryDirectory() as work:
            for _ in range(2):
                stages = []
                analyze_hprof(hprof_fixture(), work_dir=work, on_progress=stages.append)
                self.assertEqual(stages, list(HPROF_STAGES))

    def test_fast_child_emits_all_stages_once_through_actual_process_boundary(self):
        with tempfile.TemporaryDirectory() as work:
            context = isolation_tests.IsolationRegistryTests().context(
                work, hprof_fixture()
            )
            context.update(pipeline="hprof_v1", kind="JAVA_HEAP", format="hprof")
            stages = []
            result = analyze_isolated(
                context, check_lease=lambda: None, on_progress=stages.append
            )
            self.assertEqual(stages, list(HPROF_STAGES))
            self.assertEqual(
                result["parsed"]["sections"]["heap"]["totals"]["objects"], 6
            )

    def test_progress_reader_is_bounded_and_rejects_unregistered_stages(self):
        with tempfile.TemporaryDirectory() as work:
            seen = []
            reader = ProgressReader(work, seen.append)
            write_stage(work, "parse")
            reader.drain()
            reader.drain()
            self.assertEqual(seen, ["parse"])
            with self.assertRaises(AnalysisError):
                write_stage(work, "profile-controlled-text")
            (Path(work) / "analysis-progress.jsonl").write_bytes(b"x" * 4097)
            with self.assertRaises(AnalysisError):
                reader.drain()
