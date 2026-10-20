"""使用导入证书与假的 Docker/OpenSSL，执行真实 Bash 事务回归。

Windows 工作区无法创建原生符号链接，harness 仅以带标记文件模拟 ln/readlink；
真实 Linux 链接与 UID 需另行 stage 验收。mv/cmp/install 与 trap 顺序仍在本地真实执行。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASH = (
    Path("C:/Program Files/Git/bin/bash.exe") if os.name == "nt" else Path("/bin/bash")
)


def shell_path(path):
    path = Path(path).resolve().as_posix()
    return "/" + path[0].lower() + path[2:] if os.name == "nt" else path


MOCKS = r"""
set -euo pipefail
flock() { :; }
mv() {
 local destination="${@: -1}"
 if [[ ( "${FAIL_AT:-}" = current && "$destination" = */public-tls/current ) || ( "${FAIL_AT:-}" = edge && "$destination" = */runtime/edge.conf ) ]] && [[ ! -e "$deploy_dir/failed-once" ]]; then
  : > "$deploy_dir/failed-once"; return 1
 fi
 command mv "$@"
}
docker() {
 printf '%s\n' "$*" >> "$deploy_dir/calls"
 case "$*" in
  *'nginx '*' -t')
   if [[ "${FAIL_AT:-}" = config && ! -e "$deploy_dir/failed-once" ]]; then : > "$deploy_dir/failed-once"; return 1; fi ;;
  *'nginx '*' -s reload')
   [[ "${FAIL_AT:-}" != always-reload ]] || return 1
   if [[ "${FAIL_AT:-}" = reload && ! -e "$deploy_dir/failed-once" ]]; then : > "$deploy_dir/failed-once"; return 1; fi ;;
 esac
 return 0
}
curl() {
 printf 'curl-https-probe\n' >> "$deploy_dir/calls"
 if [[ "${FAIL_AT:-}" = probe-once && ! -e "$deploy_dir/failed-once" ]]; then : > "$deploy_dir/failed-once"; return 1; fi
 [[ "${FAIL_AT:-}" != probe ]]
}
sleep() { :; }
openssl() {
 local command="$1"; shift
 local source='' output='' public=0 input_public=0
 while (( $# )); do
  case "$1" in
   -in) source="$2"; shift 2 ;;
   -out) output="$2"; shift 2 ;;
   -pubkey) public=1; shift ;;
   -pubin) input_public=1; shift ;;
   *) shift ;;
  esac
 done
 if [[ "$command" = x509 ]]; then
  [[ "${FAIL_AT:-}" != certificate ]] || return 1
  if [[ "$public" = 1 ]]; then printf 'imported-public-key'; fi
 elif [[ "$command" = pkey ]]; then
  if [[ "$input_public" = 1 ]]; then cat > "$output";
  else cat -- "$source" > "$output"; fi
 fi
}
if [[ "${EMULATE_LINKS:-0}" = 1 ]]; then
 ln() { [[ "$1" = -s && "$2" = -- ]]; printf 'TEST_LINK:%s' "$3" > "$4"; }
 readlink() {
  local value
  value="$(cat -- "${@: -1}" 2>/dev/null)" || return 1
  [[ "$value" = TEST_LINK:* ]] || return 1
  printf '%s' "${value#TEST_LINK:}"
 }
fi
deploy_dir="$1"
export deploy_dir
source "$2"
"""


class TLSFixture:
    def __init__(self, path, previous=True):
        self.root = Path(path)
        self.tls = self.root / "tls"
        self.tls.mkdir()
        for file in (ROOT / "deploy/tls").iterdir():
            if file.is_file():
                (self.tls / file.name).write_text(
                    file.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
                )
        (self.root / "runtime").mkdir()
        self.edge = self.root / "runtime/edge.conf"
        self.edge.write_text("IMPORTED_PREVIOUS_EDGE\n", encoding="utf-8")
        self.public = self.root / "secrets/public-tls"
        self.public.mkdir(parents=True)
        self.current = self.public / "current"
        self.old = self.public / ".bundle-previous"
        if previous:
            self.old.mkdir()
            (self.old / "fullchain.pem").write_text("IMPORTED_OLD_CERT")
            (self.old / "privkey.pem").write_text("IMPORTED_OLD_KEY")
            self.link(self.current, self.old.name)
        acme = self.root / "secrets/acme/live/120.24.187.205"
        acme.mkdir(parents=True)
        (acme / "fullchain.pem").write_text("IMPORTED_NEW_CERT")
        (acme / "privkey.pem").write_text("imported-public-key")
        (self.public / "unrelated-marker").write_text("KEEP")

    def link(self, path, target):
        if os.name == "nt":
            path.write_text("TEST_LINK:" + target)
        else:
            path.symlink_to(target, target_is_directory=True)

    def target(self):
        if not self.current.exists() and not self.current.is_symlink():
            return ""
        return (
            self.current.read_text(encoding="utf-8").removeprefix("TEST_LINK:")
            if os.name == "nt"
            else os.readlink(self.current)
        )

    def run(self, script="issue-public.sh", failure=""):
        env = dict(
            os.environ, FAIL_AT=failure, EMULATE_LINKS="1" if os.name == "nt" else "0"
        )
        env["DROP_PYTHON_EXECUTABLE"]=shell_path(sys.executable)
        return subprocess.run(
            [
                str(BASH),
                "--noprofile",
                "--norc",
                "-c",
                MOCKS,
                "tls-test",
                shell_path(self.root),
                shell_path(self.tls / script),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
            timeout=15,
        )

    def assert_no_transaction_leftovers(self, case):
        case.assertEqual(
            list((self.root / "secrets").glob(".public-tls-rollback.*")), []
        )
        case.assertEqual(list((self.root / "runtime").glob(".edge-next.*")), [])
        case.assertEqual(list(self.public.glob("*.current-next")), [])
        case.assertEqual(
            (self.public / "unrelated-marker").read_text(encoding="utf-8"), "KEEP"
        )


class PublicTLSExecutionTests(unittest.TestCase):
    def test_failed_rollback_retains_recovery_files_instead_of_deleting_a_live_bundle(
        self,
    ):
        with tempfile.TemporaryDirectory() as folder:
            fixture = TLSFixture(folder)
            result = fixture.run(failure="always-reload")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("PUBLIC_TLS_ROLLBACK_FAILED", result.stderr)
            self.assertEqual(fixture.target(), fixture.old.name)
            self.assertEqual(len(list(fixture.public.glob(".bundle-*"))), 2)
            backups = list((fixture.root / "secrets").glob(".public-tls-rollback.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(
                (backups[0] / "edge.conf").read_text(encoding="utf-8"),
                "IMPORTED_PREVIOUS_EDGE\n",
            )

    def test_unchanged_renewal_verifies_gateway_without_accumulating_duplicate_bundles(
        self,
    ):
        with tempfile.TemporaryDirectory() as folder:
            fixture = TLSFixture(folder)
            acme = fixture.root / "secrets/acme/live/120.24.187.205"
            for name in ("fullchain.pem", "privkey.pem"):
                (fixture.old / name).write_bytes((acme / name).read_bytes())
            fixture.edge.write_bytes((fixture.tls / "edge-tls.conf").read_bytes())
            result = fixture.run(script="renew-public.sh")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fixture.target(), fixture.old.name)
            self.assertEqual(list(fixture.public.glob(".bundle-*")), [fixture.old])
            self.assertIn(
                "curl-https-probe", (fixture.root / "calls").read_text(encoding="utf-8")
            )
            fixture.assert_no_transaction_leftovers(self)

    def test_success_commits_pair_and_edge_without_success_trap_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = TLSFixture(folder)
            result = fixture.run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(fixture.target(), fixture.old.name)
            bundle = fixture.public / fixture.target()
            self.assertEqual(
                (bundle / "fullchain.pem").read_text(encoding="utf-8"),
                "IMPORTED_NEW_CERT",
            )
            self.assertEqual(
                (bundle / "privkey.pem").read_text(encoding="utf-8"),
                "imported-public-key",
            )
            self.assertIn(
                "/current/fullchain.pem", fixture.edge.read_text(encoding="utf-8")
            )
            self.assertNotIn("RESTORED", result.stderr)
            self.assertEqual(
                (fixture.old / "privkey.pem").read_text(encoding="utf-8"),
                "IMPORTED_OLD_KEY",
            )
            fixture.assert_no_transaction_leftovers(self)

    def test_config_reload_and_https_failures_restore_previous_pair_and_edge(self):
        for failure in ("config", "reload", "probe", "current", "edge"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                fixture = TLSFixture(folder)
                result = fixture.run(failure=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(fixture.target(), fixture.old.name)
                self.assertEqual(
                    fixture.edge.read_text(encoding="utf-8"), "IMPORTED_PREVIOUS_EDGE\n"
                )
                self.assertIn("PREVIOUS_STATE_RESTORED", result.stderr)
                self.assertEqual(
                    sorted(path.name for path in fixture.public.iterdir()),
                    [".bundle-previous", "current", "unrelated-marker"],
                )
                self.assertGreaterEqual(
                    (fixture.root / "calls")
                    .read_text(encoding="utf-8")
                    .count(" -s reload"),
                    1,
                )
                fixture.assert_no_transaction_leftovers(self)

    def test_failed_first_issue_restores_http_and_removes_only_its_new_bundle(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = TLSFixture(folder, previous=False)
            result = fixture.run(failure="probe")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(fixture.current.exists())
            self.assertEqual(
                fixture.edge.read_text(encoding="utf-8"), "IMPORTED_PREVIOUS_EDGE\n"
            )
            self.assertEqual(
                list(fixture.public.iterdir()), [fixture.public / "unrelated-marker"]
            )
            fixture.assert_no_transaction_leftovers(self)

    def test_invalid_certificate_or_mismatched_pair_never_changes_current(self):
        for failure in ("certificate", "mismatched"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                fixture = TLSFixture(folder)
                if failure == "mismatched":
                    (
                        fixture.root / "secrets/acme/live/120.24.187.205/privkey.pem"
                    ).write_text("IMPORTED_DIFFERENT_KEY")
                result = fixture.run(failure=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(fixture.target(), fixture.old.name)
                self.assertEqual(
                    fixture.edge.read_text(encoding="utf-8"), "IMPORTED_PREVIOUS_EDGE\n"
                )
                self.assertNotIn("IMPORTED_DIFFERENT_KEY", result.stderr)
                fixture.assert_no_transaction_leftovers(self)

    def test_asynchronous_reload_waits_for_verified_https_without_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = TLSFixture(folder)
            result = fixture.run(failure="probe-once")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(fixture.target(), fixture.old.name)
            self.assertEqual((fixture.root / "calls").read_text(encoding="utf-8").count("curl-https-probe"), 2)
            fixture.assert_no_transaction_leftovers(self)

    def test_renewal_uses_the_same_atomic_publish_and_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = TLSFixture(folder)
            result = fixture.run(script="renew-public.sh", failure="probe")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(fixture.target(), fixture.old.name)
            self.assertEqual(
                fixture.edge.read_text(encoding="utf-8"), "IMPORTED_PREVIOUS_EDGE\n"
            )
            self.assertIn(
                "run --rm --pull never",
                (fixture.root / "calls").read_text(encoding="utf-8"),
            )
            fixture.assert_no_transaction_leftovers(self)

    def test_outside_pointer_is_rejected_before_reading_or_overwriting_it(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = TLSFixture(folder)
            fixture.current.unlink()
            fixture.link(fixture.current, "../../outside")
            result = fixture.run()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(fixture.target(), "../../outside")
            self.assertEqual(
                fixture.edge.read_text(encoding="utf-8"), "IMPORTED_PREVIOUS_EDGE\n"
            )

    def test_public_entrypoint_only_recreates_named_services_without_build_or_pull(
        self,
    ):
        with tempfile.TemporaryDirectory() as folder:
            fixture = TLSFixture(folder)
            result = fixture.run()
            self.assertEqual(result.returncode, 0, result.stderr)
            commands = (fixture.root / "calls").read_text(encoding="utf-8").splitlines()
            up = [command for command in commands if command.startswith("compose up ")]
            self.assertEqual(
                up, ["compose up -d --no-deps --no-build --pull never web gateway"]
            )


class TLSConfigurationTests(unittest.TestCase):
    def test_templates_hide_signed_url_error_logs_and_share_current_pair(self):
        for name in ("edge-http.conf", "edge-tls.conf"):
            source = (ROOT / "deploy/tls" / name).read_text(encoding="utf-8")
            self.assertIn("error_log /dev/stderr crit;", source)
            self.assertIn("access_log off;", source)
        source = (ROOT / "deploy/tls/edge-tls.conf").read_text(encoding="utf-8")
        self.assertIn("/current/fullchain.pem", source)
        self.assertIn("/current/privkey.pem", source)

    def test_monitoring_reads_only_the_controlled_current_bundle(self):
        source = (ROOT / "deploy/monitoring-status.sh").read_text(encoding="utf-8")
        self.assertIn('certificate="$tls_root/current/fullchain.pem"', source)
        self.assertIn('readlink -- "$tls_root/current"', source)
        self.assertIn('! -L "$tls_root/$current"', source)
        self.assertNotIn("public-tls/fullchain.pem", source)

    def test_internal_rotation_never_builds_or_pulls_and_rejoins_helper_last(self):
        source = (ROOT / "deploy/rotate-internal-certs.sh").read_text(encoding="utf-8")
        lines = [
            line.strip() for line in source.splitlines() if ' "${compose[@]}" up ' in line
        ]
        self.assertEqual(len(lines), 2)
        self.assertTrue(
            all(
                "--no-deps --no-build --pull never --force-recreate" in line
                for line in lines
            )
        )
        self.assertTrue(lines[0].endswith("drop-server api agent || return"))
        self.assertTrue(lines[1].endswith("collector-helper || return"))


if __name__ == "__main__":
    unittest.main()
