import struct
from pathlib import Path
import tempfile
import unittest
import hashlib
from unittest.mock import patch

from drop_analyzer.errors import AnalysisError
from drop_analyzer.perf import analyze_perf_script, analyze_perf_data, scan_perf_loss
from drop_analyzer.isolate import analyze_isolated
from drop_analyzer.registry import get_pipeline


# Synthetic imported regression fixture, not evidence of an actual Runner capture.
SCRIPT = b"""worker 123/124 12.000000001: cpu-clock:
    7fa1 leaf+0x1 (/lib/a.so)
    4001 main+0x1 (/bin/toy)

worker 123 125 12.000000005: cpu-clock:
    7fb1 leaf+0x1 (/lib/b.so)
    4001 main+0x1 (/bin/toy)

worker 123/124 12.000000009: cpu-clock: 7fa1 [unknown] (/lib/a.so)

"""


def native_perf(records=b"", endian="<"):
    """Construct only synthetic header/record bytes for bounded-parser tests."""
    magic = b"PERFILE2" if endian == "<" else b"2ELIFREP"
    return (
        magic
        + struct.pack(endian + "12Q", 104, 0, 0, 0, 104, len(records), 0, 0, 0, 0, 0, 0)
        + records
    )


class PerfTests(unittest.TestCase):
    def test_perf_script_crosses_actual_child_process_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.profile"
            path.write_bytes(SCRIPT)
            context = {
                "kind": "PERF_CPU",
                "pipeline": "folded_v1",
                "format": "perf_script",
                "raw_path": str(path),
                "work_dir": directory,
                "params": {"source": "import"},
                "artifact": {
                    "size": len(SCRIPT),
                    "sha256": hashlib.sha256(SCRIPT).hexdigest(),
                    "content_type": "text/plain",
                    "metadata": {
                        "source": "import",
                        "perf": {
                            "loss_proof": "complete_native_perf_record_scan",
                            "lost_records": 0,
                            "hardware_lost_samples": 0,
                        },
                    },
                },
                "limits": {
                    "wall_seconds": 10,
                    "cpu_seconds": 5,
                    "address_space_bytes": 128 * 1024**2,
                    "output_bytes": 1024**2,
                },
            }
            result = analyze_isolated(context, check_lease=lambda: None)
            self.assertEqual(result["parsed"]["metadata"]["sample_count"], 3)
            self.assertIsNone(result["parsed"]["metadata"]["lost_samples"])
            for kind in ("PROC_CPU", "IMPORT_COLLAPSED"):
                with self.subTest(kind=kind), self.assertRaises(AnalysisError):
                    get_pipeline("folded_v1").validate({**context, "kind": kind})

    def test_inline_ip_and_repeated_first_callchain_frame_count_once(self):
        raw = b"worker 1/2 1.000000001: cpu-clock: 7fa1 leaf+0x1 (/lib/a.so)\n   7fa1 leaf+0x1 (/lib/a.so)\n   4001 main+0x1 (/bin/toy)\n"
        parsed = analyze_perf_script(raw)
        self.assertEqual(parsed["metadata"]["max_depth"], 3)
        self.assertEqual(parsed["metadata"]["sample_count"], 1)

    def test_conflicting_loss_evidence_cannot_report_zero(self):
        parsed = analyze_perf_script(
            SCRIPT + b"PERF_RECORD_LOST 7\n",
            collector_perf={
                "loss_proof": "complete_native_perf_record_scan",
                "lost_records": 0,
                "hardware_lost_samples": 0,
            },
        )
        self.assertIsNone(parsed["metadata"]["lost_samples"])
        self.assertEqual(
            parsed["sections"]["perf"]["loss_status"], "conflicting_loss_evidence"
        )

    def test_actual_thread_identity_dso_and_nanosecond_times_are_retained(self):
        parsed = analyze_perf_script(SCRIPT)
        section = parsed["sections"]["perf"]
        self.assertEqual(parsed["metadata"]["sample_count"], 3)
        self.assertEqual(
            [
                (row["pid"], row["tid"], row["sample_count"])
                for row in section["threads"]
            ],
            [(123, 124, 2), (123, 125, 1)],
        )
        self.assertEqual(
            section["time_range_ns"],
            {"start": "12000000001", "end": "12000000009", "duration": "8"},
        )
        self.assertTrue(
            all(
                node["node_type"] == "thread"
                for node in parsed["flamegraph"]["children"]
            )
        )
        names = {row["name"] for row in parsed["top"]["self"]}
        self.assertIn("leaf (/lib/a.so)", names)
        self.assertIn("leaf (/lib/b.so)", names)
        self.assertIn("0x7fa1 (/lib/a.so)", names)
        self.assertIsNone(section["lost_samples"])

    def test_complete_native_proof_is_required_for_zero_loss(self):
        parsed = analyze_perf_script(
            SCRIPT,
            collector_perf={
                "loss_proof": "complete_native_perf_record_scan",
                "lost_records": 0,
                "hardware_lost_samples": 0,
                "lost_samples": 0,
            },
        )
        self.assertEqual(parsed["metadata"]["lost_samples"], 0)
        raw = SCRIPT + b"PERF_RECORD_LOST 7\nPERF_RECORD_LOST_SAMPLES 3\n"
        section = analyze_perf_script(raw)["sections"]["perf"]
        self.assertEqual(section["lost_records"], 7)
        self.assertEqual(section["reported_lost_samples"], 3)
        self.assertIsNone(section["lost_samples"])

    def test_corrupt_truncated_and_overdeep_script_is_rejected(self):
        for raw in (
            b"",
            b"worker 1/1 1.000000000: cpu-clock:\n",
            b"bad input",
            SCRIPT + b"corrupt trailing bytes",
            b"worker 1/1 1.000000000: cpu-clock:\n"
            + b"  7fa1 leaf (/lib/a.so)\n" * 129,
        ):
            with self.subTest(size=len(raw)), self.assertRaises(AnalysisError):
                analyze_perf_script(raw)

    def test_native_loss_scan_is_bounded_endian_aware_and_not_sample_invention(self):
        for endian in ("<", ">"):
            records = struct.pack(endian + "IHHQQ", 2, 0, 24, 12, 7) + struct.pack(
                endian + "IHHQ", 13, 0, 16, 3
            )
            proof = scan_perf_loss(native_perf(records, endian))
            self.assertEqual(proof["lost_records"], 7)
            self.assertEqual(proof["hardware_lost_samples"], 3)
            self.assertIsNone(proof["lost_samples"])
        self.assertEqual(scan_perf_loss(native_perf())["lost_samples"], 0)
        self.assertEqual(
            scan_perf_loss(native_perf(struct.pack("<IHH", 81, 0, 8)))["loss_proof"],
            "unknown",
        )
        self.assertEqual(
            scan_perf_loss(native_perf(struct.pack("<IHH", 9, 0, 100)))["loss_proof"],
            "unknown",
        )

    def test_perf_data_uses_only_trusted_bounded_converter_and_same_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.profile"
            raw = native_perf()
            path.write_bytes(raw)
            with patch("drop_analyzer.perf.run_tool", return_value=SCRIPT) as converter:
                parsed = analyze_perf_data(raw, path=path, work_dir=directory)
            argv = converter.call_args.args[0]
            self.assertEqual(argv[0], "perf")
            self.assertIn("--ns", argv)
            self.assertIn("--show-lost-events", argv)
            self.assertEqual(parsed["metadata"]["sample_count"], 3)
            self.assertEqual(parsed["metadata"]["input_format"], "perf_data")
            with (
                patch("drop_analyzer.perf.run_tool") as converter,
                self.assertRaises(AnalysisError),
            ):
                analyze_perf_data(
                    b"not a binary perf file", path=path, work_dir=directory
                )
            converter.assert_not_called()
