#!/usr/bin/env python3
"""打包已经测试的 cmake --install 产物，不构建镜像或改写镜像标签。

在部署资源队列中串行运行；仅生成 manifest 的工具快照使用有资源上限的
缓存镜像容器，不挂载凭据，不访问网络。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def run(arguments, timeout=60):
    result = subprocess.run(list(map(str, arguments)), capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Runtime packaging command failed: " + result.stderr[-1500:])
    return result.stdout.strip()


def image_id(name):
    result = run(["docker", "image", "inspect", "--format", "{{.Id}}", name])
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", result):
        raise RuntimeError("Runtime packaging needs an existing cached image with a full ID")
    return result


def copy_regular(source, target, executable=False):
    """只复制有界普通文件，拒绝叶子符号链接及多重硬链接；可执行产物额外核对 ELF 签名。"""
    metadata = source.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or not 0 < metadata.st_size <= 134217728:
        raise RuntimeError("Runtime packaging accepts only bounded ordinary installed files")
    if executable:
        with source.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                raise RuntimeError("Installed runtime executable/library is not ELF")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(0o755 if executable else 0o644)


def tools_manifest(image, launcher):
    """从精确缓存镜像取得工具 manifest，以受限、无网络容器固定实际可执行文件来源。"""
    owner = secrets.token_hex(12)
    name = "drop-core-runtime-manifest-" + owner
    script = "import runpy,sys,pathlib; sys.argv=['tools-install.py','--manifest-only']; runpy.run_path('/fixture/tools-install.py',run_name='__main__'); print(pathlib.Path('/opt/drop-tools/manifest.json').read_text())"
    try:
        result = run(["docker", "run", "--rm", "--name", name, "--label", "io.drop.owner=" + owner,
                      "--label", "io.drop.purpose=isolated-runtime-manifest", "--network", "none", "--cap-drop", "ALL",
                      "--user", "0:10001", "--memory", "192m", "--memory-swap", "192m", "--cpus", ".5", "--pids-limit", "32",
                      "--device-read-bps", "/dev/nvme0n1:8mb", "--device-write-bps", "/dev/nvme0n1:4mb",
                      "-v", f"{ROOT}/core/tools-install.py:/fixture/tools-install.py:ro", "-v", f"{launcher}:/usr/local/bin/drop_gdb_ptrace:ro", "--entrypoint", "/usr/bin/python3", image, "-c", script])
        manifest = json.loads(result)
        if manifest.get("schema_version") != 1 or "perf" not in manifest.get("tools", {}):
            raise RuntimeError("Runtime tool snapshot omitted the trusted perf entry")
        return manifest
    finally:
        inspected = subprocess.run(["docker", "inspect", "--format", '{{index .Config.Labels "io.drop.owner"}}', name], capture_output=True, text=True, timeout=10)
        if inspected.returncode == 0 and inspected.stdout.strip() == owner:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10, check=False)


def main():
    """打包已测试的安装产物和工具摘要，不重新编译或改标签；交付清单用于后续精确绑定。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--core-base", default="drop-core:20261016.1")
    parser.add_argument("--tools-base", default="drop-core-full-tools-base:v2")
    arguments = parser.parse_args()
    installed = arguments.install.resolve(strict=True)
    output = arguments.output.absolute()
    if output.exists() or output.is_symlink() or installed == installed.parent:
        raise RuntimeError("Use a new runtime context directory and a concrete installed prefix")
    bases = {"core":image_id(arguments.core_base), "tools":image_id(arguments.tools_base)}
    output.mkdir(mode=0o700, parents=True)
    required = {"drop_server", "drop_agent", "drop_collector_helper", "drop_gdb_ptrace", "drop_workload", "drop_native_workload"}
    names = set()
    for area in ("bin", "lib", "include/drop"):
        for source in sorted((installed / area).iterdir()):
            if area == "bin": names.add(source.name)
            copy_regular(source, output / "runtime" / area / source.name, area in ("bin", "lib"))
    if not required <= names:
        raise RuntimeError("Installed runtime does not contain every tested service/helper/workload")
    copy_regular(ROOT / "core/Dockerfile.prebuilt", output / "Dockerfile")
    copy_regular(ROOT / "contracts/task-kinds.json", output / "task-kinds.json")
    full = tools_manifest(arguments.tools_base, output / "runtime/bin/drop_gdb_ptrace")
    (output / "tools-manifest.json").write_text(json.dumps(full, indent=2) + "\n")
    # 对最小 base 镜像自身的可执行文件取快照，其字节可能与完整 tools 镜像不同；
    # 不能把一个镜像的 perf 哈希复用给另一个镜像。
    script = 'for profiler in /usr/lib/linux-tools/*/perf; do if test -x "$profiler"; then sha256sum "$profiler"; break; fi; done'
    perf = run(["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL", "--user", "10001:10001",
                "--memory", "32m", "--memory-swap", "32m", "--cpus", ".2", "--entrypoint", "/bin/sh", arguments.core_base, "-c", script])
    digest, path = perf.split(None, 1)
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or not path.startswith("/usr/lib/linux-tools/"):
        raise RuntimeError("Minimal runtime perf snapshot is invalid")
    (output / "core-manifest.json").write_text(json.dumps({"schema_version":1,"tools":{"perf":{"path":path,"sha256":digest}}}, indent=2) + "\n")
    if bases != {"core":image_id(arguments.core_base), "tools":image_id(arguments.tools_base)}:
        raise RuntimeError("A cached base image changed during runtime packaging")
    fingerprint = json.loads(run(["python3", ROOT / "core/tests/source_fingerprint.py"]))
    files = [{"path":file.relative_to(output).as_posix(),"size":file.stat().st_size,"sha256":hashlib.sha256(file.read_bytes()).hexdigest()}
             for file in sorted(output.rglob("*")) if file.is_file()]
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    provenance = {"schema_version":1,"source_sha256":fingerprint["source_sha256"],"base_images":bases,"files":files,
                  "context_sha256":hashlib.sha256(canonical).hexdigest(),"context_hash_scope":"canonical files array before this self-referencing provenance file"}
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps({"context":str(output), "source_sha256":provenance["source_sha256"], "context_sha256":provenance["context_sha256"],"base_images":bases}))


if __name__ == "__main__":
    main()
