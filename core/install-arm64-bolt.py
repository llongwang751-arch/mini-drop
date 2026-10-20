"""安装 Ubuntu Noble 未提供的、固定版本的官方 LLVM ARM 包。

预期版本、大小与 SHA-256 来自 2026-10-16 核对的官方 LLVM Noble ARM64
Packages 索引，不额外加入 APT 软件源或信任密钥。
"""

import hashlib
from pathlib import Path
import subprocess
import tempfile
import urllib.parse
import urllib.request

VERSION = "19.1.7~++20250804090312+cd708029e0b2-1~exp1~20250804210325.79"
BASE = "https://apt.llvm.org/noble/pool/main/l/llvm-toolchain-19/"
PACKAGES = (
    (
        "bolt-19",
        39898870,
        "7d363e84f58b5bb07a42d610f7f6263bcda046db2d261b512f39b5caa6c3e940",
    ),
    (
        "libbolt-19-dev",
        56994,
        "ccef2130f1a4d41e129c58244695050aea55bf5c0012c62583a8a83ff7dd7d88",
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
    # 保留 catalog/manifest 契约。LLVM multicall 二进制会检查 argv[0]，
    # 因此启动 ARM19 程序时仍需保留 perf2bolt 别名。
    for alias, target in (("/usr/bin/llvm-bolt-16", "/usr/bin/llvm-bolt-19"),
                          ("/usr/bin/perf2bolt-16", "/usr/bin/perf2bolt-19")):
        link = Path(alias)
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            raise SystemExit("Refusing to replace an unexpected LLVM executable")
        if not Path(target).is_file():
            raise SystemExit("Official LLVM package omitted its expected executable")
        link.symlink_to(target)
        subprocess.run([str(link), "--version"], check=True)



if __name__ == "__main__":
    main()
