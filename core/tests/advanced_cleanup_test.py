"""Execute actual cleanup functions with imported Docker metadata; no Docker."""
import ast
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest


def functions(command):
    source = ast.parse((Path(__file__).with_name("helper_advanced_isolated.py")).read_text(encoding="utf-8"))
    body = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in ("remove_owned", "cleanup_owned")]
    namespace = {"command": command, "OWNER": "imported-owner", "subprocess": subprocess}
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), "actual-advanced-cleanup", "exec"), namespace)
    return namespace


class AdvancedCleanupTests(unittest.TestCase):
    def test_removal_timeout_rechecks_ownership_and_then_recovers(self):
        calls = []
        def command(args, **kwargs):
            calls.append(args)
            if args[1] == "inspect": return SimpleNamespace(returncode=0, stdout="imported-owner", stderr="")
            if sum(a[1] == "rm" for a in calls) == 1: raise subprocess.TimeoutExpired("imported-docker", 40)
            return SimpleNamespace(returncode=0)
        self.assertTrue(functions(command)["remove_owned"]("imported-fixture"))
        self.assertEqual(sum(a[1] == "inspect" for a in calls), 2)

    def test_foreign_owner_after_timeout_prevents_second_removal(self):
        calls = []
        def command(args, **kwargs):
            calls.append(args)
            if args[1] == "inspect": return SimpleNamespace(returncode=0, stdout="imported-owner" if len(calls) == 1 else "foreign", stderr="")
            raise subprocess.TimeoutExpired("imported-docker", 40)
        self.assertFalse(functions(command)["remove_owned"]("imported-fixture"))
        self.assertEqual(sum(a[1] == "rm" for a in calls), 1)

    def test_one_unavailable_container_does_not_skip_other_cleanup(self):
        removed = []
        def command(args, **kwargs):
            if args[-1] == "unavailable": raise subprocess.TimeoutExpired("imported-docker", 40)
            if args[1] == "inspect": return SimpleNamespace(returncode=0, stdout="imported-owner", stderr="")
            removed.append(args[-1]); return SimpleNamespace(returncode=0)
        self.assertFalse(functions(command)["cleanup_owned"](["unavailable", "other-fixture"]))
        self.assertEqual(removed, ["other-fixture"])

    def test_missing_container_is_success_but_daemon_failure_retains_spool(self):
        for stderr, expected in [("Error: No such object: imported-fixture", True), ("error: no such object: imported-fixture", True), ("Cannot connect to daemon", False)]:
            self.assertEqual(functions(lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=stderr))["remove_owned"]("imported-fixture"), expected)


if __name__ == "__main__":
    unittest.main()
