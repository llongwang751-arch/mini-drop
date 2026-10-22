"""入口边界的合成控制，不代替实际Go路由+PostgreSQL测试。"""
import importlib.util
import json
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("jd_security_run", Path(__file__).with_name("run.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class SecurityRunnerTests(unittest.TestCase):
    def test_only_explicit_loopback_test_database(self):
        runner.validate_database("postgresql://fixture:fixture@127.0.0.1:5433/postgres?sslmode=disable", "1")
        for dsn, opt in (("postgresql://fixture@82.157.81.79/drop_test", "1"), ("postgresql://fixture@localhost/drop", "1"), ("postgresql://fixture@localhost/drop_test", None), ("postgresql://fixture@localhost/drop_test#bad", "1")):
            with self.subTest(dsn=dsn), self.assertRaises(ValueError):
                runner.validate_database(dsn, opt)

    def test_skip_or_empty_output_is_not_success(self):
        for events in ([], [{"Action": "skip", "Test": "TestPostgresJDSecurityMatrix"}], [{"Action": "pass", "Test": "case"}, {"Action": "fail", "Test": "other"}]):
            stdout = b"\n".join(json.dumps(e).encode() for e in events)
            self.assertFalse(runner.summarize(stdout, b"", 0)["passed"])

    def test_actual_test_terminal_events_are_counted_without_bodies(self):
        stdout = b'{"Action":"output","Output":"private body never copied"}\n{"Action":"pass","Test":"TestPostgresJDSecurityMatrix"}\n'
        receipt = runner.summarize(stdout, b"", 0)
        self.assertTrue(receipt["passed"])
        self.assertNotIn("private body", json.dumps(receipt))


if __name__ == "__main__":
    unittest.main()
