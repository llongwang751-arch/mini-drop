"""验证公开结果的来源、计量单位、规则建议和限制说明，输入来自测试夹具。"""

import unittest

from drop_analyzer.folded import analyze_folded
from drop_analyzer.result import make_result


class ResultTests(unittest.TestCase):
    def test_imported_native_kinds_never_claim_live_duration_or_frequency(self):
        for kind in (
            "PERF_CPU",
            "PROC_CPU",
            "JAVA_CPU",
            "GO_CPU",
            "GO_HEAP",
            "JAVA_HEAP",
            "PYTHON_CPU",
            "PYTHON_MEMORY",
            "IO_TRACE",
            "MEMORY_TRACE",
            "GPERF_CPU",
            "GPERF_HEAP",
            "JEPROF_HEAP",
            "BOLT_PROFILE",
            "SCRIPT",
            "RESOURCE_SNAPSHOT",
        ):
            with self.subTest(kind=kind):
                parsed = analyze_folded(b"fixture;leaf 10")
                parsed["metadata"]["duration_ns"] = 10**9
                result = make_result(
                    parsed,
                    kind,
                    {"source": "import", "duration_seconds": 10, "frequency_hz": 99},
                )
                self.assertEqual(result["summary"]["source"], "import")
                self.assertIsNone(result["summary"]["duration_seconds"])
                self.assertIsNone(result["summary"]["frequency_hz"])

    def test_contract_result_and_weights(self):
        result = make_result(
            analyze_folded(b"root;a 80\nroot;b 20\n"),
            "PERF_CPU",
            {"duration_seconds": 10, "frequency_hz": 99},
        )
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["total_samples"], 100)
        self.assertEqual(result["summary"]["source"], "perf")
        self.assertEqual(result["top_functions"][0]["name"], "a")
        self.assertEqual(result["top_functions"][0]["self_percent"], 80)

    def test_procfs_does_not_make_function_claims(self):
        result = make_result(
            analyze_folded(b"process;malloc 100"), "PROC_CPU", {"duration_seconds": 5}
        )
        self.assertEqual(result["suggestions"], [])
        self.assertEqual(result["summary"]["source"], "procfs")
        self.assertEqual(result["summary"]["unit"], "cpu_ticks")
        self.assertTrue(
            any("线程" in line for line in result["summary"]["limitations"])
        )

    def test_import_is_labelled_and_rule_has_numeric_confidence(self):
        result = make_result(analyze_folded(b"root;malloc 100"), "IMPORT_COLLAPSED", {})
        self.assertEqual(result["summary"]["source"], "import")
        self.assertIsNone(result["summary"]["duration_seconds"])
        self.assertGreater(result["suggestions"][0]["confidence"], 0)
        self.assertIn("threshold", result["suggestions"][0]["evidence"])


if __name__ == "__main__":
    unittest.main()
