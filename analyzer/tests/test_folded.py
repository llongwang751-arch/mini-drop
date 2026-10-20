import gzip
import hashlib
import json
from pathlib import Path
import unittest

from drop_analyzer.errors import AnalysisError
from drop_analyzer.folded import Limits, analyze_folded
from drop_analyzer.pipeline import build_outputs, decode_input
from drop_analyzer import __version__


GOLDEN = Path(__file__).parent / "fixtures" / "golden.folded"


class FoldedTests(unittest.TestCase):
    def test_golden_tree_weights_and_top(self):
        result = analyze_folded(GOLDEN.read_bytes())
        root = result["flamegraph"]
        self.assertEqual(root["value"], 100)
        self.assertEqual(result["metadata"]["sample_count"], 100)
        self.assertEqual(result["metadata"]["stack_count"], 6)
        self.assertEqual(root["children"][0]["name"], "worker")
        main = next(n for n in root["children"][0]["children"] if n["name"] == "main")
        self.assertEqual((main["value"], main["self"]), (80, 5))
        parse = next(n for n in main["children"] if n["name"] == "parse")
        self.assertEqual((parse["value"], parse["self"]), (50, 30))
        self.assertEqual(result["top"]["self"][0]["name"], "parse")
        self.assertEqual(result["top"]["inclusive"][0]["name"], "worker")

    def test_recursive_function_counted_once_per_sample_in_top(self):
        result = analyze_folded(b"root;recursive;recursive;leaf 7\n")
        recursive = next(
            row for row in result["top"]["inclusive"] if row["name"] == "recursive"
        )
        self.assertEqual(recursive["inclusive"], 7)
        self.assertEqual(recursive["inclusive_ratio"], 1)
        self.assertEqual(result["metadata"]["node_count"], 5)

    def test_duplicate_stacks_and_stable_output(self):
        a = analyze_folded(b"z;b 3\na;c 2\nz;b 4\n")
        b = analyze_folded(b"z;b 4\nz;b 3\na;c 2\n")
        self.assertEqual(a, b)
        self.assertEqual(a["metadata"]["unique_stack_count"], 2)

    def test_accepted_unicode_spaces_crlf_and_final_newline(self):
        result = analyze_folded("进程;function with spaces 3\r\n进程;其他 2".encode())
        self.assertEqual(result["flamegraph"]["value"], 5)

    def test_invalid_input_is_permanent(self):
        for value in [
            b"",
            b"\n",
            b"root 0",
            b"root -1",
            b"root 1.5",
            b"root nan",
            b"root",
            b"root;;child 1",
            b";root 1",
            b"root; 1",
            b"root\x00 1",
            b"\xff 1",
            b"root +1",
            b"root 1\ncorrupt",
        ]:
            with self.subTest(value=value):
                with self.assertRaises(AnalysisError) as caught:
                    analyze_folded(value)
                self.assertFalse(caught.exception.retryable)

    def test_resource_boundaries(self):
        cases = [
            (b"root 1", Limits(max_input_bytes=5)),
            (b"r;a;b 1", Limits(max_depth=2)),
            (b"r;a 1\nr;b 1", Limits(max_nodes=3)),
            (b"r 4", Limits(max_total_samples=3)),
            (b"r 1\nr 1", Limits(max_stacks=1)),
            (b"abcdef 1", Limits(max_frame_bytes=5)),
            (b"r 1", Limits(max_line_bytes=2)),
        ]
        for raw, limits in cases:
            with self.subTest(raw=raw, limits=limits):
                with self.assertRaises(AnalysisError) as caught:
                    analyze_folded(raw, limits=limits)
                self.assertEqual(caught.exception.code, "INPUT_LIMIT_EXCEEDED")

    def test_default_node_limit_accepts_100k_and_rejects_next(self):
        # Includes the synthetic root, matching the public result node_count.
        self.assertEqual(Limits().max_nodes, 100_000)
        raw = b"\n".join(f"function_{index:06d} 1".encode() for index in range(99_999))
        result = analyze_folded(raw)
        self.assertEqual(result["metadata"]["node_count"], 100_000)
        self.assertEqual(result["flamegraph"]["value"], 99_999)
        self.assertEqual(len(result["flamegraph"]["children"]), 99_999)
        with self.assertRaises(AnalysisError) as caught:
            analyze_folded(raw + b"\nfunction_over_limit 1")
        self.assertEqual(caught.exception.code, "INPUT_LIMIT_EXCEEDED")

    def test_gzip_hash_size_and_expansion_limits(self):
        raw = b"root;leaf 5\n"
        compressed = gzip.compress(raw, mtime=0)
        digest = hashlib.sha256(compressed).hexdigest()
        self.assertEqual(
            decode_input(
                compressed, "text/x-collapsed+gzip", digest, len(compressed), Limits()
            ),
            raw,
        )
        for hashed, size in [
            ("0" * 64, len(compressed)),
            (digest, len(compressed) + 1),
        ]:
            with self.assertRaises(AnalysisError) as caught:
                decode_input(
                    compressed, "text/x-collapsed+gzip", hashed, size, Limits()
                )
            self.assertEqual(caught.exception.code, "ARTIFACT_INTEGRITY_ERROR")
        with self.assertRaises(AnalysisError) as caught:
            decode_input(
                compressed,
                "text/x-collapsed+gzip",
                digest,
                len(compressed),
                Limits(max_input_bytes=5),
            )
        self.assertEqual(caught.exception.code, "INPUT_LIMIT_EXCEEDED")
        with self.assertRaises(AnalysisError):
            decode_input(
                b"not gzip",
                "text/x-collapsed+gzip",
                hashlib.sha256(b"not gzip").hexdigest(),
                8,
                Limits(),
            )

    def test_outputs_and_evidence_based_rules(self):
        outputs = build_outputs(
            analyze_folded(b"worker;malloc 80\nworker;other 20\n"),
            task_id="t1",
            job_id="j1",
            input_sha256="a" * 64,
        )
        self.assertEqual(
            set(outputs),
            {"flamegraph.json.gz", "top.json", "report.json", "manifest.json"},
        )
        self.assertEqual(
            json.loads(gzip.decompress(outputs["flamegraph.json.gz"]))["value"], 100
        )
        report = json.loads(outputs["report.json"])
        self.assertEqual(report["generator"], "deterministic_rules")
        finding = next(
            r for r in report["suggestions"] if r["code"] == "ALLOCATION_HOTSPOT"
        )
        self.assertEqual(finding["evidence"]["self_samples"], 80)
        self.assertEqual(finding["rule_version"], "1.0.0")
        manifest = json.loads(outputs["manifest.json"])
        self.assertEqual(manifest["input_sha256"], "a" * 64)
        self.assertEqual(manifest["analyzer_version"], __version__)
        self.assertEqual(len(manifest["artifacts"]), 3)
        for artifact in manifest["artifacts"]:
            self.assertEqual(
                hashlib.sha256(outputs[artifact["name"]]).hexdigest(),
                artifact["sha256"],
            )

    def test_insufficient_evidence_does_not_recommend(self):
        outputs = build_outputs(
            analyze_folded(b"worker;malloc 1"),
            task_id="t1",
            job_id="j1",
            input_sha256="a" * 64,
        )
        self.assertEqual(json.loads(outputs["report.json"])["suggestions"], [])


if __name__ == "__main__":
    unittest.main()
