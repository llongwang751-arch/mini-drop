"""Install pinned official LLVM ARM packages absent from Debian Bookworm.

Expected version, size and SHA-256 come from the official LLVM Bookworm ARM64
Packages index, checked 2026-10-16. No extra APT repository or trust key is added.
"""

import hashlib
from pathlib import Path
import subprocess
import tempfile
import urllib.parse
import urllib.request

VERSION = "19.1.7~++20250114103228+cd708029e0b2-1~exp1~20250114103334.78"
BASE = "https://apt.llvm.org/bookworm/pool/main/l/llvm-toolchain-19/"
PACKAGES = (
    (
        "bolt-19",
        33362784,
        "56170c0c9313c7283632484d6b148ae7496eeb13e164de071ed0bd77ad7dd146",
    ),
    (
        "libbolt-19-dev",
        55284,
        "0f47cc8abda57bbfcd6b22614b039f0f2540da9f98cd876c1e1a8c45485b2794",
    ),
)


def main():
    architecture = subprocess.check_output(
        ["dpkg", "--print-architecture"], text=True
    ).strip()
    if architecture != "arm64":
        raise SystemExit("This pinned tool installer is only for ARM64")
    with tempfile.TemporaryDirectory(prefix="drop-bolt-arm64-") as directory:
        downloaded = []
        for name, expected_size, expected_sha in PACKAGES:
            filename = f"{name}_{VERSION}_arm64.deb"
            path = Path(directory) / filename
            digest, size = hashlib.sha256(), 0
            url = BASE + urllib.parse.quote(filename, safe="-_.~")
            with (
                urllib.request.urlopen(url, timeout=90) as response,
                path.open("wb") as output,
            ):
                while block := response.read(256 * 1024):
                    size += len(block)
                    if size > expected_size:
                        raise SystemExit(
                            "Official LLVM package exceeds its pinned size"
                        )
                    digest.update(block)
                    output.write(block)
            if size != expected_size or digest.hexdigest() != expected_sha:
                raise SystemExit(
                    "Official LLVM package failed pinned size/SHA-256 verification"
                )
            path.chmod(0o644)
            downloaded.append(str(path))
        subprocess.run(
            ["apt-get", "install", "-y", "--no-install-recommends", *downloaded],
            check=True,
        )
    link = Path("/usr/local/bin/llvm-bolt")
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        raise SystemExit("Refusing to replace an unexpected LLVM executable")
    link.symlink_to("/usr/lib/llvm-19/bin/llvm-bolt")
    subprocess.run([str(link), "--version"], check=True)


if __name__ == "__main__":
    main()
