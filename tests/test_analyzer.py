import unittest

from minidrop.analyzer import analyze


class AnalyzerTests(unittest.TestCase):
    def test_result_is_visualizable_and_attribution_has_evidence(self):
        result = analyze({"stacks": [{"stack": ["main", "malloc"], "value": 9}], "samples": []})
        self.assertEqual(result["flamegraph"]["value"], 9)
        self.assertEqual(result["top"][0]["name"], "main")
        self.assertEqual(result["attribution"][0]["rule"], "(malloc|alloc|gc)")
        self.assertIn("evidence", result["attribution"][0])
