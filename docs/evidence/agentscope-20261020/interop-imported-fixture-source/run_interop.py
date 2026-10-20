"""Root-only isolated fixture launcher; every tested service drops to its real UID."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time


def demote(uid):
    def apply():
        os.setgroups([10003])
        os.setgid(uid)
        os.setuid(uid)
    return apply


def signal_sdk(pid, signum, environment):
    # CAP_KILL is deliberately absent. Signal from a short-lived process with
    # the target's actual UID, using only the launcher's existing SETUID/GID.
    sent = subprocess.run(
        [sys.executable, "-c", "import os,sys; os.kill(int(sys.argv[1]), int(sys.argv[2]))", str(pid), str(signum)],
        env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, preexec_fn=demote(10002), timeout=5,
    )
    if sent.returncode:
        raise RuntimeError("INTEROP_SDK_SIGNAL_FAILED")


def main():
    if os.getuid() != 0:
        raise RuntimeError("fixture launcher requires root only to assign the real process identities")
    os.setgroups([10003])
    bundle = Path(__file__).resolve().parent
    manifest = json.loads((bundle / "manifest.json").read_text())
    for name, digest in manifest["files"].items():
        if hashlib.sha256((bundle / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("INTEROP_BUNDLE_DIGEST_MISMATCH")
    with tempfile.TemporaryDirectory(prefix="drop-imported-interop-", dir="/tmp") as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        runner_dir, gateway_dir = root / "runner", root / "gateway"
        for directory, uid in ((runner_dir, 10002), (gateway_dir, 10001)):
            directory.mkdir()
        secret = runner_dir / "imported-provider-key"
        secret.write_text("imported-fixture-key-no-live-credential", encoding="utf-8")
        os.chown(secret, 10002, 10002)
        secret.chmod(0o400)
        for directory, uid in ((runner_dir, 10002), (gateway_dir, 10001)):
            os.chown(directory, uid, 10003)
            directory.chmod(0o2750)
        binary = root / "interop-service.test"
        shutil.copyfile(bundle / "interop-service.test", binary)
        binary.chmod(0o555)
        environment = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "PYTHONPATH": "/app:" + str(bundle),
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "OTEL_SDK_DISABLED": "true",
            "HOME": "/nonexistent",
            "GOMEMLIMIT": "192MiB",
            "GOMAXPROCS": "2",
            "INTEROP_RUNNER_SOCKET": str(runner_dir / "control.sock"),
            "INTEROP_GATEWAY_SOCKET": str(gateway_dir / "control.sock"),
            "INTEROP_MARKER_DIR": str(runner_dir),
            "INTEROP_FIXTURE_KEY_FILE": str(secret),
        }
        with (root / "python.log").open("wb") as sdk_log:
            sdk = subprocess.Popen([sys.executable, str(bundle / "fixture_server.py")], env=environment,
                                   stdin=subprocess.DEVNULL, stdout=sdk_log, stderr=sdk_log, preexec_fn=demote(10002))
            try:
                deadline = time.monotonic() + 30
                while not Path(environment["INTEROP_RUNNER_SOCKET"]).is_socket():
                    if sdk.poll() is not None:
                        raise RuntimeError("INTEROP_SDK_SERVER_EXITED")
                    if time.monotonic() > deadline:
                        raise RuntimeError("INTEROP_SDK_SERVER_NOT_READY")
                    time.sleep(0.05)
                result = subprocess.run([str(binary), "-test.v", "-test.run=^TestExternalAgentScopeInterop$", "-test.timeout=90s"],
                                        env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, preexec_fn=demote(10001), timeout=100)
                output = result.stdout.decode("utf-8", errors="replace")
                print(output, end="", flush=True)
                report = {"passed": result.returncode == 0, "fixture": "imported synthetic provider and tool data", "actual_sdk": "agentscope==2.0.10", "actual_transport": "Go UID10001 and Python UID10002 over separate real Unix sockets; gid10003; SO_PEERCRED", "production_mounts": False, "provider_network": False, "provider_key": "imported fake fixture only", "go_binary_sha256": manifest["files"]["interop-service.test"], "output_sha256": hashlib.sha256(result.stdout).hexdigest()}
                print(json.dumps(report, sort_keys=True), flush=True)
                if result.returncode:
                    raise SystemExit(result.returncode)
            finally:
                try:
                    if sdk.poll() is None:
                        signal_sdk(sdk.pid, signal.SIGTERM, environment)
                        try:
                            sdk.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            signal_sdk(sdk.pid, signal.SIGKILL, environment)
                            sdk.wait(timeout=5)
                finally:
                    log = (root / "python.log").read_bytes()
                    print(json.dumps({"sdk_log_bytes": len(log), "sdk_log_sha256": hashlib.sha256(log).hexdigest()}), flush=True)
                    # Reclaim only the launcher's two temporary directories.
                    for directory in (runner_dir, gateway_dir):
                        os.chown(directory, 0, 0)
                        directory.chmod(0o700)


if __name__ == "__main__":
    main()
