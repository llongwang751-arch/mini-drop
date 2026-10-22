"""使用假的 Docker CLI 验证夹具归属校验的失败路径，不连接 Docker daemon。"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


FAKE_DOCKER = r"""#!/usr/bin/env sh
set -eu
printf '%s\n' "$*" >> "$FAKE_STATE/commands"
last=''
for value in "$@"; do last=$value; done
case "$1 $2" in
  'container inspect') test -f "$FAKE_STATE/$last" ;;
  'network inspect'|'inspect --format')
    test -f "$FAKE_STATE/$last" || exit 1
    cat "$FAKE_STATE/$last"
    ;;
  'network create') printf '%s' "$ANALYZER_TEST_PREFIX" > "$FAKE_STATE/$last" ;;
  'network rm'|'rm -f') rm -f -- "$FAKE_STATE/$last" ;;
  *)
    if [ "$1" = run ]; then
      name=''; detached=0
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --name) shift; name=$1 ;;
          --detach) detached=1 ;;
        esac
        shift
      done
      printf '%s' "$ANALYZER_TEST_PREFIX" > "$FAKE_STATE/$name"
      if [ "$detached" = 0 ]; then
        if [ "${FOREIGN_STORAGE:-0}" = 1 ]; then
          printf foreign > "$FAKE_STATE/$ANALYZER_TEST_PREFIX-minio"
        fi
        exit 17
      fi
    elif [ "$1" != exec ]; then
      exit 99
    fi
    ;;
esac
"""


class IsolatedRunnerSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell = shutil.which("bash")
        git_shell = Path("C:/Program Files/Git/bin/bash.exe")
        if os.name == "nt" and git_shell.is_file():
            cls.shell = str(git_shell)
        if not cls.shell:
            raise RuntimeError(
                "Bash is required for the integration runner safety test"
            )
        cls.script = Path(__file__).with_name("run-isolated-integration.sh").resolve()

    def run_fixture(self, *, collision=None, foreign=False, live_path=""):
        with tempfile.TemporaryDirectory(prefix="drop-runner-safety-") as temporary:
            root = Path(temporary)
            commands = root / "bin"
            state = root / "state"
            commands.mkdir()
            state.mkdir()
            docker = commands / "docker"
            docker.write_text(FAKE_DOCKER, encoding="utf8", newline="\n")
            docker.chmod(0o755)
            prefix = "drop-analyzer-fixture-safety"
            if collision:
                (state / f"{prefix}-{collision}").write_text("foreign")
            environment = {
                **os.environ,
                "PATH": str(commands) + os.pathsep + os.environ["PATH"],
                "FAKE_STATE": str(state).replace("\\", "/"),
                "ANALYZER_TEST_PREFIX": prefix,
                "FOREIGN_STORAGE": "1" if foreign else "0",
                "TEST_LIVE_CAPTURE_DIR": live_path,
            }
            result = subprocess.run(
                [self.shell, str(self.script).replace("\\", "/")],
                env=environment,
                cwd=root,
                text=True,
                capture_output=True,
                timeout=15,
            )
            trace = (state / "commands").read_text() if (state / "commands").exists() else ""
            remaining = {
                path.name: path.read_text()
                for path in state.iterdir()
                if path.name != "commands"
            }
            return result, trace, remaining

    def test_existing_container_collision_is_never_removed(self):
        result, trace, remaining = self.run_fixture(collision="postgres")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("rm -f", trace)
        self.assertNotIn("network create", trace)
        self.assertEqual(set(remaining.values()), {"foreign"})

    def test_existing_network_collision_is_never_removed(self):
        result, trace, remaining = self.run_fixture(collision="network")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("network rm", trace)
        self.assertNotIn("network create", trace)
        self.assertEqual(set(remaining.values()), {"foreign"})

    def test_test_failure_preserves_status_and_cleans_owned_resources(self):
        result, trace, remaining = self.run_fixture()
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertFalse(remaining)
        self.assertIn("network create --internal --label", trace)
        for limit in ("384m", "256m", "768m"):
            self.assertIn(f"--memory-swap={limit}", trace)

    def test_cleanup_refuses_a_resource_with_a_different_label(self):
        result, trace, remaining = self.run_fixture(foreign=True)
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(remaining, {"drop-analyzer-fixture-safety-minio": "foreign"})
        self.assertNotIn("rm -f drop-analyzer-fixture-safety-minio", trace)

    def test_live_capture_mount_rejects_escape_and_foreign_names_before_docker(self):
        for path in ("/tmp/profile", ".local/../../private", ".local/drop-quality-chain-123/capture", ".local/drop-quality-chain-0123456789abcdeg/capture", ".local/drop-quality-chain-0123456789abcdef/capture,readonly=false"):
            with self.subTest(path=path):
                result, trace, remaining = self.run_fixture(live_path=path)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(trace)
                self.assertFalse(remaining)


if __name__ == "__main__":
    unittest.main(verbosity=2)
