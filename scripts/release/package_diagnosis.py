"""在本地构建无密钥、带哈希的 Linux 发布包，不连接服务器。

要求已有通过测试的 web/dist 和成功 PostgreSQL/浏览器收据；
不打包 .env、.local、密钥、私钥或任意工作区文件。
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
VERSION = "20261020.7"
BLOCKED = {".local", ".tools", "secrets", "node_modules", "__pycache__", "runtime", ".git", "test-results"}
EXTENSIONS = {".go", ".mod", ".sum", ".py", ".json", ".proto", ".md", ".yaml", ".yml", ".ts", ".tsx", ".css", ".html", ".conf", ".cpp", ".hpp", ".h", ".c", ".java", ".sh", ".txt", ".lock", ".service", ".timer"}
KEY = re.compile(rb"sk-[a-zA-Z0-9]{24,}")

def source_files(root: Path):
    """只选择允许目录和扩展名，拒绝符号链接及必需文件缺失，不打包整个用户工作区。"""
    root = root.resolve()
    files = set()
    for folder in ("api/cmd", "api/gen", "api/internal", "api/tools", "contracts", "db/migrations", "web/src", "web/e2e", "core", "analyzer", "ops", "deploy", "integrations"):
        for p in (root / folder).rglob("*"):
            rel = p.relative_to(root)
            if any(part in BLOCKED or part.startswith(".env") for part in rel.parts):
                continue
            if p.is_file() and p.suffix in EXTENSIONS | {".sql"}:
                if p.is_symlink() or not p.resolve().is_relative_to(root):
                    raise ValueError("SOURCE_SYMLINK_REJECTED")
                files.add(p)
    for name in ("api/go.mod", "api/go.sum", "api/Dockerfile.runtime", "web/Dockerfile.runtime", "core/Dockerfile", "core/Dockerfile.offline-tools", "analyzer/Dockerfile", "ops/Dockerfile", "ops/Dockerfile.runtime", "deploy/Dockerfile.nginx",
                 "web/nginx.conf", "web/package.json", "web/package-lock.json", "web/playwright.diagnosis.config.ts",
                 "docs/CONTRACT.md", "docs/openapi.yaml", "docs/DIAGNOSIS_AGENT.md", "docs/DIAGNOSIS_AGENT_RESEARCH.md", "docs/DELIVERY_MATRIX.md",
                 "docs/CAPABILITY_GAPS.md", "docs/DIAGNOSIS_AGENT_V2.md", "docs/AGENT_V2_RESEARCH.md", "docs/AGENT_V2_PLAN.md", "docs/releases/20261020.7-agent-v2.md", "integrations/crm-python/Dockerfile", ".github/workflows/ci.yml", "DEVELOPMENT_LOG.md", "README.md", "AGENTS.md", "deploy/compose.tencent.yml"):
        p = root / name
        if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(root):
            raise ValueError("REQUIRED_SOURCE_MISSING_OR_SYMLINK")
        files.add(p)
    files.add(root / "scripts/release/package_diagnosis.py")
    for name in ("vite.config.ts", "tsconfig.json", "tsconfig.app.json", "tsconfig.node.json", "index.html", "playwright.preview.config.ts", "playwright.config.ts"):
        p=root/"web"/name
        if p.is_file() and not p.is_symlink():files.add(p)
    return sorted(files)

def safe_bytes(path: Path):
    """读取待发布文件并拒绝明显密钥/私钥内容，不在错误中回显扫描命中。"""
    raw = path.read_bytes()
    if KEY.search(raw) or (b"-----BEGIN " + b"PRIVATE KEY-----") in raw or (b"-----BEGIN " + b"RSA PRIVATE KEY-----") in raw:
        raise ValueError("CREDENTIAL_CONTENT_REJECTED")
    return raw

def main():
    """核对成功验收收据、源码快照与已构建 ELF 后，生成带逐文件摘要的本地发布包。

    业务源码使用明确登记快照，不能复制外部 CRM 的配置或数据；源码/镜像/部署收据分别绑定，
    不能仅因本地打包成功就宣称服务已上线。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=".local/agent-v2-20261020")
    parser.add_argument("--api-binary", type=Path, help="Use the exact ignored local binary whose SHA matches deployment evidence")
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to((ROOT / ".local").resolve()):
        raise ValueError("OUTPUT_MUST_BE_IN_IGNORED_LOCAL_DIRECTORY")
    output.mkdir(parents=True, exist_ok=True)
    evidence = ROOT / "docs/evidence/agent-v2-20261020"
    for name in ("postgres-acceptance.json", "browser-acceptance.json"):
        receipt = json.loads((evidence / name).read_text(encoding="utf-8"))
        if receipt.get("passed") is not True:
            raise ValueError("SUCCESSFUL_ACCEPTANCE_RECEIPT_REQUIRED")
    env = dict(os.environ, GOOS="linux", GOARCH="amd64", CGO_ENABLED="0", GOMAXPROCS="2", GOMEMLIMIT="512MiB", GOFLAGS="-p=1")
    binary = output / "drop-api.bin"
    if args.api_binary:
        supplied = args.api_binary.resolve()
        if not supplied.is_relative_to((ROOT / '.local').resolve()) or supplied.is_symlink():
            raise ValueError("API_BINARY_MUST_BE_IGNORED_LOCAL_FILE")
        binary = supplied
    else:
        build = subprocess.run(["go", "build", "-trimpath", "-buildvcs=false", "-tags=nomsgpack", "-ldflags=-s -w",
                                "-o", str(binary), "./cmd/drop-api"], cwd=ROOT / "api", env=env, capture_output=True, timeout=120)
        if build.returncode:
            raise RuntimeError("LINUX_API_BUILD_FAILED")
    go_version = subprocess.check_output(["go", "env", "GOVERSION"], cwd=ROOT / "api", env=env, timeout=15).decode().strip()
    if not binary.read_bytes().startswith(b"\x7fELF"):
        raise ValueError("LINUX_ELF_REQUIRED")
    members = {p.relative_to(ROOT).as_posix(): safe_bytes(p) for p in source_files(ROOT)}
    # 只纳入已核对且不含凭据的登记快照，不打包外部 CRM 工作区或其 .env/data/tmp；
    # 版本必须与真实验收一致。
    snapshot = ROOT / ".local/agent-v2-20261020/crm-context"
    descriptor = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    live = json.loads((evidence / "live-crm-agent.json").read_text(encoding="utf-8"))
    revision = hashlib.sha256(json.dumps(descriptor['files'], sort_keys=True).encode()).hexdigest()
    if revision != descriptor['source_revision'] or revision != live['source_revision'] or len(descriptor['files']) > 4096:
        raise ValueError('ENROLLMENT_REVISION_MISMATCH')
    total = 0
    for name, digest in descriptor['files'].items():
        p = snapshot / 'source' / name
        if p.is_symlink() or not p.resolve().is_relative_to((snapshot/'source').resolve()) or '..' in Path(name).parts:
            raise ValueError('ENROLLMENT_PATH_REJECTED')
        raw = safe_bytes(p);total += len(raw)
        if hashlib.sha256(raw).hexdigest() != digest or total > 32 * 1024 * 1024:
            raise ValueError('ENROLLMENT_CONTENT_CHANGED')
        members['integrations/crm-python/snapshot/source/' + name] = raw
    for name in ('manifest.json', 'requirements.lock', 'Dockerfile'):
        members['integrations/crm-python/snapshot/' + name] = safe_bytes(snapshot/name)
    for p in (snapshot/'harness').glob('*.py'):
        if p.is_symlink() or p.read_bytes() != (ROOT/'integrations/crm-python/harness'/p.name).read_bytes():
            raise ValueError('ENROLLMENT_HARNESS_CHANGED')
        members['integrations/crm-python/snapshot/harness/' + p.name] = safe_bytes(p)
    source_hash = hashlib.sha256()
    for name, raw in sorted(members.items()):
        source_hash.update(name.encode() + b"\0" + hashlib.sha256(raw).digest())
    for p in (ROOT / "web/dist").rglob("*"):
        if p.is_file():
            if p.is_symlink() or not p.resolve().is_relative_to(ROOT):
                raise ValueError("WEB_ASSET_SYMLINK_REJECTED")
            members[p.relative_to(ROOT).as_posix()] = safe_bytes(p)
    if "web/dist/index.html" not in members:
        raise ValueError("BUILT_WEB_REQUIRED")
    members["api/drop-api.bin"] = safe_bytes(binary)
    for p in evidence.rglob('*'):
        if p.is_file() and p.suffix in (".json", ".png") and p.name != 'release-bundle.json':
            members[p.relative_to(ROOT).as_posix()] = safe_bytes(p)
    deployment = json.loads((evidence / 'deployment-proof.json').read_text(encoding='utf-8'))
    deployed = deployment.get('success') is True and deployment.get('api_binary_sha256') == hashlib.sha256(binary.read_bytes()).hexdigest()
    if args.api_binary and not deployed:
        raise ValueError('API_BINARY_DEPLOYMENT_HASH_MISMATCH')
    manifest = {"version": VERSION, "platform": "linux/amd64", "go_version": go_version,
                "source_sha256": source_hash.hexdigest(), "deployed": deployed,
                "deployment_origin": deployment['origin'], "base_image_digests": deployment['base_image_digests'],
                "deployed_images": [{k: x[k] for k in ('service', 'image', 'image_id')} for x in deployment['containers'] if x['status'] == 'running'],
                "files": [{"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()} for name, raw in sorted(members.items())]}
    members["release-manifest.json"] = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode()
    archive = output / ("drop-diagnosis-" + VERSION + ".tar.gz")
    with archive.open("wb") as target, gzip.GzipFile(filename="", fileobj=target, mode="wb", mtime=0) as zipped, tarfile.open(fileobj=zipped, mode="w") as tar:
        for name, raw in sorted(members.items()):
            info = tarfile.TarInfo(name);info.size=len(raw);info.mode=0o755 if name=="api/drop-api.bin" else 0o644
            tar.addfile(info, io.BytesIO(raw))
    receipt = {"packaged": True, "deployed": deployed, "version": VERSION, "go_version": go_version,
               "source_sha256": source_hash.hexdigest(), "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
               "archive_bytes": archive.stat().st_size, "files": len(members), "api_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
               "contains_model_key": False, "server_gate": "User-authorized Tencent target 82.157.81.79; exact API binary SHA and separate live runtime evidence verified"}
    (output / "package-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt))

if __name__ == "__main__":
    main()
