"""以显式构造的格式夹具验证 HPROF 对象图与原生 profile 解析边界。"""

import struct
import tempfile
import unittest
import sqlite3
from pathlib import Path

from drop_analyzer.errors import AnalysisError
from drop_analyzer.hprof import analyze_hprof
from drop_analyzer.native import analyze_bolt, native_cpu, native_heap


def number(value, width=4):
    return value.to_bytes(width, "big")


def record(tag, body):
    return bytes([tag]) + bytes(4) + number(len(body)) + body


def hprof_fixture():
    # 显式构造的 HPROF 格式标准图，不标记为 JVM 现场采集。
    raw = b"JAVA PROFILE 1.0.2\0" + number(4) + number(123, 8)
    raw += (
        record(1, number(50) + b"FixtureNode")
        + record(1, number(51) + b"left")
        + record(1, number(52) + b"right")
    )
    raw += record(2, number(1) + number(100) + number(0) + number(50))
    heap = (
        b"\x20"
        + number(100)
        + number(0)
        + bytes(6 * 4)
        + number(8)
        + bytes(4)
        + number(2, 2)
    )
    heap += number(51) + b"\x02" + number(52) + b"\x02"
    for identifier, left, right in ((1, 2, 3), (2, 4, 0), (3, 4, 0), (4, 5, 0)):
        heap += (
            b"\x21"
            + number(identifier)
            + number(0)
            + number(100)
            + number(8)
            + number(left)
            + number(right)
        )
    heap += b"\x23" + number(5) + number(0) + number(64) + b"\x08" + bytes(64)
    heap += b"\xff" + number(1)
    return raw + record(0x1C, heap) + record(0x2C, b"")


class HprofNativeTests(unittest.TestCase):
    def test_hprof_index_version_upgrade_and_corruption_rebuild(self):
        with tempfile.TemporaryDirectory() as root:
            analyze_hprof(hprof_fixture(), work_dir=root)
            database = next(Path(root).glob("*.sqlite3"))
            with sqlite3.connect(database) as connection:
                connection.execute(
                    "UPDATE meta SET value='obsolete' WHERE key='index_schema_version'"
                )
            connection.close()
            parsed = analyze_hprof(hprof_fixture(), work_dir=root)
            self.assertTrue(parsed["metadata"]["index_rebuilt"])
            self.assertFalse(parsed["metadata"]["index_resumed"])
            database.write_bytes(b"corrupt index")
            parsed = analyze_hprof(hprof_fixture(), work_dir=root)
            self.assertTrue(parsed["metadata"]["index_rebuilt"])
            self.assertEqual(parsed["sections"]["heap"]["totals"]["objects"], 6)

    def test_hprof_dominator_shared_references_and_index_resume(self):
        with tempfile.TemporaryDirectory() as root:
            parsed = analyze_hprof(hprof_fixture(), work_dir=root)
            heap = parsed["sections"]["heap"]
            self.assertEqual(heap["totals"]["objects"], 6)
            self.assertEqual(heap["totals"]["shallow_bytes"], 96)
            retained = {
                row["object_id"]: row["retained_bytes"] for row in heap["dominators"]
            }
            self.assertEqual(retained["0x1"], 96)
            self.assertEqual(retained["0x2"], 8)
            self.assertEqual(retained["0x4"], 72)
            self.assertEqual(heap["leak_paths"][0]["path"][0]["root_kind"], "unknown")
            self.assertTrue(
                analyze_hprof(hprof_fixture(), work_dir=root)["metadata"][
                    "index_resumed"
                ]
            )

    def test_hprof_limit_corruption_and_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(AnalysisError) as failure:
                analyze_hprof(hprof_fixture(), work_dir=root, limits={"max_objects": 2})
            self.assertEqual(failure.exception.code, "INPUT_LIMIT_EXCEEDED")
            self.assertEqual(
                analyze_hprof(hprof_fixture(), work_dir=root)["sections"]["heap"][
                    "totals"
                ]["objects"],
                6,
            )
        for raw in (b"not hprof", hprof_fixture()[:-2]):
            with self.assertRaises(AnalysisError):
                analyze_hprof(raw)

    def test_native_cpu_binary_endianness_and_truncation(self):
        for encoding in ("<I", ">I", "<Q", ">Q"):
            raw = b"".join(
                struct.pack(encoding, value)
                for value in [0, 3, 0, 10000, 0, 7, 2, 0x100, 0x200, 0, 1, 0]
            )
            parsed = native_cpu(raw)
            self.assertEqual(parsed["metadata"]["sample_count"], 7)
            self.assertEqual(parsed["metadata"]["period_us"], 10000)
            self.assertEqual(parsed["flamegraph"]["children"][0]["name"], "0x1ff")
            with self.assertRaises(AnalysisError):
                native_cpu(raw[:-1])

    def test_native_heap_truncation_does_not_become_empty_success(self):
        cases = (
            ("GPERF_HEAP", b"heap profile:\n"),
            ("GPERF_HEAP", b"heap profile: 1: 100 [2: 200] @ heapprofile\n"),
            ("JEPROF_HEAP", b"heap_v2/1\n"),
            ("JEPROF_HEAP", b"heap_v2/1\n  t*: 1: 100 [2: 200]\n@ 0x100\n"),
        )
        for kind, raw in cases:
            with self.subTest(kind=kind), self.assertRaises(AnalysisError):
                native_heap(raw, kind=kind)
        empty = native_heap(
            b"heap profile: 0: 0 [0: 0] @ heapprofile\n", kind="GPERF_HEAP"
        )
        self.assertEqual(empty["metadata"]["sample_count"], 0)

    def test_native_heaps_and_bolt_real_format(self):
        raw = b"heap profile: 1: 100 [2: 200] @ heapprofile\n1: 100 [2: 200] @ 0x100 0x200\nMAPPED_LIBRARIES:\n"
        self.assertEqual(
            native_heap(raw, kind="GPERF_HEAP")["metadata"]["sample_count"], 100
        )
        raw = (
            b"heap_v2/1\n  t*: 1: 100 [2: 200]\n@ 0x100 0x200\n  t*: 1: 100 [2: 200]\n"
        )
        self.assertEqual(
            native_heap(raw, kind="JEPROF_HEAP")["metadata"]["sample_count"], 100
        )
        bolt = analyze_bolt(b"1 main 10 1 helper 0 2 10\n1 helper 8 1 main 14 0 7\n")
        self.assertEqual(
            bolt["sections"]["binary"]["totals"], {"branches": 17, "mispredictions": 2}
        )
        self.assertEqual(
            analyze_bolt(b"no_lbr cycles\n1 main 0 42\n")["metadata"]["sample_count"],
            42,
        )


if __name__ == "__main__":
    unittest.main()
