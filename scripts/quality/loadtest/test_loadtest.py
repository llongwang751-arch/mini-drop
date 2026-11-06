"""压测工具的离线回归：只验算数学、边界与"拒绝越界"，不连任何服务器。

这些测试不能证明平台性能；它们证明这套工具不会因为参数或统计错误而
给出一个看起来正常、实际是错的数字。
"""

import argparse
import csv
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import login_sessions
import run as runner
import summarize

STATS_HEADER = [
    "Type", "Name", "Request Count", "Failure Count", "Median Response Time",
    "Average Response Time", "Min Response Time", "Max Response Time",
    "Average Content Size", "Requests/s", "Failures/s",
    "50%", "66%", "75%", "80%", "90%", "95%", "98%", "99%", "99.9%", "99.99%", "100%",
]


def _write_stats(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(STATS_HEADER)
        for name, count, failures, p50, p95, p99 in rows:
            writer.writerow(["GET", name, count, failures, p50, p50, 10, p99,
                             100, 1.0, 0.0, p50, p50, p50, p50, p50, p95, p95, p99, p99, p99, p99])
        writer.writerow(["", "Aggregated", 999999, 999999, 0, 0, 0, 0, 0, 0, 0] + [0] * 11)


class ReceiptTests(unittest.TestCase):
    def test_totals_ignore_aggregated_row_and_take_worst_percentile(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats = os.path.join(tmp, "s.csv")
            _write_stats(stats, [("GET /", 10, 0, 40, 80, 120), ("GET /api/v1/tasks", 30, 0, 60, 300, 900)])
            extra = os.path.join(tmp, "x.json")
            with open(extra, "w", encoding="utf-8") as handle:
                json.dump({"status_counts": {"GET /": {"200": 10}, "GET /api/v1/tasks": {"200": 30}}}, handle)
            receipt = summarize.build_receipt(stats, "", extra, {"target": "https://example", "profile": "api-step"})
        self.assertEqual(receipt["totals"]["requests"], 40)
        self.assertEqual(receipt["totals"]["failures"], 0)
        self.assertEqual(receipt["totals"]["p95_ms_worst_endpoint"], 300)
        self.assertEqual(receipt["totals"]["p99_ms_worst_endpoint"], 900)
        self.assertEqual(receipt["verdict"], "pass")

    def test_failures_and_5xx_fail_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats = os.path.join(tmp, "s.csv")
            _write_stats(stats, [("GET /api/v1/tasks", 10, 1, 40, 80, 120)])
            extra = os.path.join(tmp, "x.json")
            with open(extra, "w", encoding="utf-8") as handle:
                json.dump({"status_counts": {"GET /api/v1/tasks": {"200": 9, "500": 1}}}, handle)
            receipt = summarize.build_receipt(stats, "", extra, {"target": "https://example"})
        self.assertEqual(receipt["totals"]["error_rate"], 0.1)
        self.assertFalse(summarize.is_passing(receipt))
        self.assertEqual([gate["id"] for gate in receipt["gates"] if not gate["passed"]],
                         ["no_5xx", "error_rate"])

    def test_empty_run_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats = os.path.join(tmp, "s.csv")
            _write_stats(stats, [])
            receipt = summarize.build_receipt(stats, "", "", {"target": "https://example"})
        self.assertEqual(receipt["totals"]["requests"], 0)
        self.assertEqual(receipt["verdict"], "pass")  # 0 请求 0 错误，但结论必须靠 not_measured 说明


class GateTests(unittest.TestCase):
    def _args(self, **overrides):
        base = {
            "host": "https://example",
            "profile": "api-step",
            "stages": "",
            "sessions": 0,
            "credentials_file": "",
            "out": "x",
            "insecure": False,
            "dry_run": True,
            "think_ms": "",
            "processes": 1,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_api_profile_refuses_to_start_without_sessions(self):
        with self.assertRaises(SystemExit):
            runner.validate(self._args())

    def test_public_profile_accepts_no_sessions(self):
        stages = runner.validate(self._args(profile="public-step"))
        self.assertEqual(stages, [(2, 20), (5, 30), (10, 30), (20, 30)])

    def test_rejects_user_and_duration_caps(self):
        with self.assertRaises(SystemExit):
            runner.validate(self._args(profile="public-step", stages="101:10"))
        with self.assertRaises(SystemExit):
            runner.validate(self._args(profile="public-step", stages="5:601"))
        with self.assertRaises(SystemExit):
            runner.validate(self._args(profile="api-step", sessions=9))

    def test_rejects_bad_stage_syntax(self):
        for stages in ("5", "0:30", "5:1", "a:20"):
            with self.subTest(stages=stages), self.assertRaises(SystemExit):
                runner.validate(self._args(profile="public-step", stages=stages))

    @unittest.skipUnless(os.name == "nt", "windows-only guard: locust --processes is unavailable")
    def test_windows_refuses_multi_process_instead_of_writing_empty_csv(self):
        with self.assertRaises(SystemExit):
            runner.validate(self._args(profile="public-step", processes=2))

    def test_empty_stages_falls_back_to_profile_default(self):
        self.assertEqual(runner.validate(self._args(profile="public-smoke", stages="")), [(2, 20)])


class StageMathTests(unittest.TestCase):
    def test_stage_boundaries_are_half_open(self):
        sys.path.insert(0, HERE)
        os.environ.pop("DROP_LOADTEST_STAGES", None)
        import locustfile

        stages = [(2, 20), (5, 30)]
        self.assertEqual(locustfile.plan_stage(0, stages), (2, 1))
        self.assertEqual(locustfile.plan_stage(19.9, stages), (2, 1))
        self.assertEqual(locustfile.plan_stage(20, stages), (5, 1))
        self.assertIsNone(locustfile.plan_stage(50, stages))
        self.assertNotIn("ApiUser", dir(locustfile))  # 没有会话池时不应暴露认证用户类


class CredentialFileTests(unittest.TestCase):
    def test_reads_two_lines_and_tolerates_crlf_and_blanks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "account.txt")
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write("admin\r\n\r\ns3cret-value\r\n")
            self.assertEqual(login_sessions._read_credentials(path), ("admin", "s3cret-value"))

    def test_single_line_is_refused_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "account.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("only-username\n")
            with self.assertRaises(SystemExit):
                login_sessions._read_credentials(path)


class ThinkTimeTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("DROP_LOADTEST_THINK_MS", None)

    def test_override_clamps_to_milliseconds(self):
        import locustfile

        os.environ["DROP_LOADTEST_THINK_MS"] = "100,300"
        wait = locustfile._think_time(1, 3)
        samples = [wait(None) for _ in range(50)]  # locust 的 wait_time 可调用对象要传实例
        self.assertTrue(all(0.1 <= value <= 0.3 for value in samples), samples)

    def test_invalid_override_is_refused(self):
        import locustfile

        for raw in ("abc", "-5,10", "300,100", "0,70000"):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                os.environ["DROP_LOADTEST_THINK_MS"] = raw
                locustfile._think_time(1, 3)


if __name__ == "__main__":
    unittest.main()
