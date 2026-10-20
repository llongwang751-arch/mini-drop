#!/usr/bin/env python3
"""只用合成 Docker 元数据执行真实夹具清理代码，不访问真实 daemon。"""
import ast
import pathlib
import shutil
import subprocess
from types import SimpleNamespace
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def bash_path():
    if __import__("os").name == "nt":
        git = shutil.which("git")
        selected = pathlib.Path(git).resolve().parents[1] / "bin/bash.exe" if git else None
        if selected and selected.is_file():
            return str(selected)
        raise RuntimeError("Use native Git Bash for this portable fixture test, not WSL")
    selected = shutil.which("bash")
    if not selected:
        raise RuntimeError("Bash is required to verify the actual shell cleanup")
    return selected


class FixtureCleanupTests(unittest.TestCase):
    def shell_cleanup(self, metadata):
        source = (ROOT / "core/tests/rpc_isolated.sh").read_text(encoding="utf-8")
        start = source.index("cleanup() {\n")
        end = source.index("\n}\n", start) + 3
        # 该合成命令不调用 Docker，也不修改文件系统。
        stub = """
owner=expected
prefix=fixture
network=fixture-network
removed=()
docker() {
  if test "$1" = inspect || { test "$1" = network && test "$2" = inspect; }; then
    printf '%s\\n' METADATA
  elif test "$1" = rm || { test "$1" = network && test "$2" = rm; }; then
    removed+=("${@: -1}")
  else
    return 80
  fi
}
""".replace("METADATA", "'" + metadata + "'")
        result = subprocess.run([bash_path(), "--noprofile", "--norc", "-c", stub + source[start:end] + '\ncleanup\nprintf "%s\\n" "${removed[@]}"\n'],
                                capture_output=True, text=True, timeout=5, check=True)
        return [line for line in result.stdout.splitlines() if line]

    def python_cleanup(self, metadata, recovery=False):
        script = "upload_recovery_isolated.py" if recovery else "compat_isolated.py"
        source = ast.parse((ROOT / "core/tests" / script).read_text(encoding="utf-8"))
        function = next(item for item in source.body if isinstance(item, ast.FunctionDef) and item.name == ("run_case" if recovery else "run_pair"))
        attempt = next(item for item in function.body if isinstance(item, ast.Try))
        calls = []

        def command(arguments, check=True):
            if "inspect" in arguments:
                return metadata
            calls.append(arguments)
            return ""

        # 只执行恢复夹具中真实的 Docker 清理语句，不包含后续独立文件系统/日志清理；
        # 此合成元数据测试不得删除任何宿主文件。
        statements = []
        for statement in attempt.finalbody:
            if isinstance(statement,ast.Import):
                break
            statements.append(statement)
        module = ast.fix_missing_locations(ast.Module(body=statements, type_ignores=[]))
        exec(compile(module, "actual-compat-cleanup", "exec"),
             {"command":command, "containers":["fixture-server", "fixture-agent"],
              "network":"fixture-network", "owner":"expected"})
        return calls

    def test_shell_owned_resources_are_removed(self):
        self.assertEqual(len(self.shell_cleanup("expected")), 7)

    def test_shell_foreign_resources_are_preserved(self):
        self.assertEqual(self.shell_cleanup("foreign"), [])

    def test_shell_missing_owner_is_preserved(self):
        self.assertEqual(self.shell_cleanup(""), [])

    def test_python_owned_resources_are_removed(self):
        self.assertEqual(len(self.python_cleanup("expected")), 3)

    def test_python_foreign_resources_are_preserved(self):
        self.assertEqual(self.python_cleanup("foreign"), [])

    def test_python_missing_owner_is_preserved(self):
        self.assertEqual(self.python_cleanup(""), [])

    def test_recovery_owned_resources_are_removed(self):
        self.assertEqual(len(self.python_cleanup("expected",recovery=True)),3)

    def test_recovery_foreign_resources_are_preserved(self):
        self.assertEqual(self.python_cleanup("foreign",recovery=True),[])

    def test_recovery_missing_owner_is_preserved(self):
        self.assertEqual(self.python_cleanup("",recovery=True),[])

    def helper_cleanup(self, metadata):
        source = ast.parse((ROOT / "core/tests/helper_ipc_isolated.py").read_text(encoding="utf-8"))
        function = next(item for item in source.body if isinstance(item,ast.FunctionDef) and item.name=="main")
        block = next(item for item in function.body if isinstance(item,ast.With))
        attempt = next(item for item in block.body if isinstance(item,ast.Try))
        calls = []

        def command(arguments, check=True):
            if arguments[1]=="inspect" and "io.drop.owner" in arguments[3]:
                return SimpleNamespace(stdout=metadata,stderr="")
            calls.append(arguments)
            return SimpleNamespace(stdout="synthetic metadata",stderr="")

        module = ast.fix_missing_locations(ast.Module(body=attempt.finalbody,type_ignores=[]))
        exec(compile(module,"actual-helper-cleanup","exec"),
             {"command":command,"PREFIX":"fixture-helper","OWNER":"expected",
              "REPORT":SimpleNamespace(exists=lambda:True)})
        return calls

    def test_helper_owned_resources_are_removed(self):
        self.assertEqual(len(self.helper_cleanup("expected")),3)

    def test_helper_foreign_resources_are_not_read_or_removed(self):
        self.assertEqual(self.helper_cleanup("foreign"),[])

    def test_helper_missing_owner_is_not_read_or_removed(self):
        self.assertEqual(self.helper_cleanup(""),[])

    def test_actual_rpc_script_has_valid_bash_syntax(self):
        subprocess.run([bash_path(), "--noprofile", "--norc", "-n", str(ROOT / "core/tests/rpc_isolated.sh")],
                       capture_output=True, text=True, timeout=5, check=True)


if __name__ == "__main__":
    unittest.main()
