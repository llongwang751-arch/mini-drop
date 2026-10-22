"""合成 go test/coverage 收据验证门禁不能把 skip、缺函数或退化当成通过。"""

import unittest

from check_risks import assess, parse_functions


class RiskGateTests(unittest.TestCase):
    def setUp(self):
        self.policy = {"coverage_scope": "synthetic gate fixture", "required_tests": ["TestRealPG"], "functions": [{"risk_id": "fixture", "file": "drop/api/internal/repository/postgres.go", "function": "Cancel", "minimum_percent": 70}]}
        self.events = [{"Test": "TestRealPG", "Action": "pass"}]
        self.coverage = {("drop/api/internal/repository/postgres.go", "Cancel"): 72}

    def test_only_executed_required_tests_and_correct_source_count(self):
        self.assertTrue(assess(self.policy, self.events, self.coverage)["passed"])
        self.assertFalse(assess(self.policy, [], self.coverage)["passed"])
        self.assertFalse(assess(self.policy, [{"Test": "TestRealPG", "Action": "skip"}], self.coverage)["passed"])
        self.assertFalse(assess(self.policy, self.events + [{"Test": "TestOther", "Action": "fail"}], self.coverage)["passed"])
        self.assertFalse(assess(self.policy, [{"Test": "TestRealPG", "Action": "fail"}] + self.events, self.coverage)["passed"])
        self.assertFalse(assess(self.policy, self.events + [{"Action": "fail", "Package": "synthetic/package"}], self.coverage)["passed"])

    def test_coverage_drop_or_same_named_foreign_function_cannot_pass(self):
        self.assertFalse(assess(self.policy, self.events, {("drop/api/internal/repository/postgres.go", "Cancel"): 69.9})["passed"])
        self.assertFalse(assess(self.policy, self.events, {("other.go", "Cancel"): 100})["passed"])

    def test_go_cover_rows_parse_and_duplicates_reject(self):
        text = "drop/api/internal/repository/postgres.go:189:\tCancel\t72.0%\ntotal:\t(statements)\t27.4%\n"
        self.assertEqual(parse_functions(text), self.coverage)
        with self.assertRaises(ValueError):
            parse_functions(text + text)


if __name__ == "__main__":
    unittest.main()
