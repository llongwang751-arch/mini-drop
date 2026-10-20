"""Real capless bootstrap regression, with private throwaway secrets only.

Requires a cached Linux image containing bash and openssl. No production paths,
network, daemon socket mounts or credential values enter the test output.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    script = Path(__file__).resolve().parents[1] / "bootstrap.sh"
    with tempfile.TemporaryDirectory(prefix="drop-bootstrap-test-") as temporary:
        root = Path(temporary)
        linked = root / "linked"
        linked.mkdir()
        actual = root / "actual-secrets"
        actual.mkdir(mode=0o700)
        (linked / "secrets").symlink_to("/work/actual-secrets", target_is_directory=True)
        shutil.copyfile(script, linked / "bootstrap.sh")
        flags = ["docker", "run", "--rm", "--pull", "never", "--network", "none", "--cap-drop", "ALL",
                 "--security-opt", "no-new-privileges:true", "--memory", "128m", "--memory-swap", "128m",
                 "--cpus", ".5", "--pids-limit", "32", "--user", "0:0", "--mount", f"type=bind,src={root},dst=/work",
                 "--entrypoint", "/bin/bash", args.image, "/work/linked/bootstrap.sh"]
        before = None
        for iteration in range(2):
            result = subprocess.run(flags, capture_output=True, timeout=45)
            if result.returncode:
                raise RuntimeError("BOOTSTRAP_FIXTURE_FAILED")
            for file in actual.iterdir():
                if file.is_file() and file.stat().st_mode & 0o777 != 0o444:
                    raise RuntimeError("BOOTSTRAP_MOUNTED_SECRET_NOT_READABLE")
            snapshot = {name: hashlib.sha256((actual / name).read_bytes()).hexdigest()
                        for name in ("admin_password", "database_url", "s3_kms_master_key", "tls/ca.crt")}
            if before is not None and snapshot != before:
                raise RuntimeError("BOOTSTRAP_REPLACED_PERSISTENT_IDENTITY")
            before = snapshot
        # Check the same file-bind semantics used by the unprivileged storage service.
        result = subprocess.run(["docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
                                 "--cap-drop", "ALL", "--user", "10001:10001", "--memory", "64m",
                                 "--mount", f"type=bind,src={actual / 's3_kms_master_key'},dst=/fixture-key,readonly",
                                 "--entrypoint", "/usr/bin/test", args.image, "-r", "/fixture-key"],
                                capture_output=True, timeout=15)
        if result.returncode:
            raise RuntimeError("UNPRIVILEGED_MOUNTED_KMS_SECRET_UNREADABLE_EXIT_" + str(result.returncode))
        if (actual / "signing/ca.key").stat().st_mode & 0o777 != 0o600:
            raise RuntimeError("CA_SIGNING_KEY_PERMISSIONS_CHANGED")
    print(json.dumps({"fresh_and_repeated_symlink_bootstrap": "passed", "uid10001_kms_read": "passed",
                      "persistent_identity_preserved": True, "production_access": False}))


if __name__ == "__main__":
    main()
