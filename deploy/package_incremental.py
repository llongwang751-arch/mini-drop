#!/usr/bin/env python3
"""为已有服务器源码树准备经过扫描的增量交付包。

默认 draft 不生成归档；文档冻结后，final 模式重新读取当前文件字节并固定清单。
这里只在本地打包，不上传或修改已部署文件；部署者应保留白名单之外的现有数据。
"""
import argparse
from collections import Counter
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
PLAN = Path(__file__).with_name('incremental-20261020.json')
BLOCK_PARTS = {'.git', '.local', '.env', 'secrets', 'node_modules', '__pycache__',
               '.pytest_cache', '.ruff_cache', '.mypy_cache', '.tools', '.cache',
               'build', 'cache', 'benchmarks', 'playwright-report', 'authstate'}
BLOCK_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.pyc', '.o', '.a', '.so', '.exe',
                  '.data', '.prof', '.pprof', '.folded', '.bin', '.zip', '.gz', '.xz'}
SIGNATURE = re.compile(rb'https?://[^\s<>"\x27]+[?&](?:X-Amz-Signature|X-Goog-Signature|signature|sig)=[A-Za-z0-9%+/=_-]{32,}[^\s<>"\x27]*', re.I)
PRIVATE_KEY = re.compile(rb'-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\s]{64,}-----END (?:[A-Z0-9]+ )?PRIVATE KEY-----')
DEEPSEEK_KEY = re.compile(rb'\bsk-[a-zA-Z0-9]{32,}\b')


def known_secrets():
    """在本机私有目录读取已知敏感值，仅用于逐字节扫描；报告只给命中数量，不输出值或位置。"""
    values = []
    key = ROOT / '.local/diagnosis-20261020/ai_api_key'
    login = ROOT / '.local/diagnosis-20261020/腾讯云Drop登录信息.txt'
    if not key.is_file() or not login.is_file():
        raise RuntimeError('required private scan inputs unavailable')
    values.append(key.read_bytes().strip())
    lines = login.read_text(encoding='utf-8-sig').splitlines()
    passwords = [line[3:].strip() for line in lines if line.startswith(('密码：', '密码:'))]
    if len(passwords) != 1:
        raise RuntimeError('invalid private scan input shape')
    values.append(passwords[0].encode())
    old = ROOT / '.local/admin_password'
    if old.is_file():
        values.append(old.read_bytes().strip())
    if any(len(v) < 8 for v in values):
        raise RuntimeError('invalid private scan input length')
    return tuple(set(values))


def allowed(path):
    rel = path.relative_to(ROOT)
    parts = {p.lower() for p in rel.parts}
    name = path.name.lower()
    return (not path.is_symlink() and not parts.intersection(BLOCK_PARTS)
            and not name.startswith('.env') and not any(x in name for x in ('cookie', 'auth-state', 'authstate', 'storage-state', 'storagestate'))
            and path.suffix.lower() not in BLOCK_SUFFIXES
            and path.resolve().is_relative_to(ROOT.resolve()))


def snapshot(plan):
    """按计划白名单读取当前文件，校验读取前后大小与时间并固定 bytes，避免打包途中源文件变化。"""
    files = set()
    for pattern in plan['files']:
        files.update(p for p in ROOT.glob(pattern) if p.is_file())
    for tree in plan['trees']:
        base = ROOT / tree['path']
        if not base.is_dir():
            raise RuntimeError('required whitelist tree unavailable')
        files.update(p for p in base.rglob('*') if p.is_file() and p.suffix.lower() in tree['suffixes'])
    values = {}
    for path in sorted(files):
        if not allowed(path):
            continue
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(content) != after.st_size:
            raise RuntimeError('whitelisted file changed during snapshot')
        values[path.relative_to(ROOT).as_posix()] = content
    for required in plan['required']:
        if required not in values:
            raise RuntimeError('required whitelisted file unavailable')
    return values


def scan(values, secrets):
    """扫描选定字节中的已知凭据、私钥、签名 URL 和 API key 形态，只返回计数与通过状态。"""
    counts = Counter(known_credentials=0, pem_private_key=0, signed_url=0, api_key_shape=0)
    for content in values.values():
        counts['known_credentials'] += sum(content.count(secret) for secret in secrets)
        counts['pem_private_key'] += len(PRIVATE_KEY.findall(content))
        counts['signed_url'] += len(SIGNATURE.findall(content))
        counts['api_key_shape'] += len(DEEPSEEK_KEY.findall(content))
    return {'passed': not any(counts.values()), 'counts': dict(counts),
            'known_private_values_checked': len(secrets),
            'scope': 'all selected file bytes; no matching values or locations are logged'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    """先快照与扫描，再生成清单；只有 final 才写归档，并重新打开逐成员校验。

    此命令不执行 SSH 或服务更新；清单中的运行镜像绑定是独立证据，不表示当前源码自动成为已部署版本。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['draft', 'final'], default='draft')
    parser.add_argument('--output', type=Path, default=ROOT / '.local/final-delivery-20261020')
    parser.add_argument('--plan', type=Path, default=PLAN)
    parser.add_argument('--archive-name', default='tx-drop-20261020-final-incremental.tar.gz')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / '.local').resolve()):
        raise RuntimeError('delivery output must remain in the private workspace .local directory')
    plan_path = args.plan.resolve()
    if not plan_path.is_relative_to((ROOT / 'deploy').resolve()) or not re.fullmatch(r'[a-zA-Z0-9._-]+\.tar\.gz', args.archive_name):
        raise RuntimeError('invalid delivery plan or archive name')
    plan = json.loads(plan_path.read_text())
    values = snapshot(plan)
    check = scan(values, known_secrets())
    print(json.dumps({'secret_scan': check}, ensure_ascii=False), flush=True)
    if not check['passed']:
        return 2
    manifest = {'schema_version': 1, 'mode': args.mode, 'delivery_type': 'incremental_source_and_evidence',
                'baseline': 'existing /opt/tx-drop-20261020/source; preserve files outside this whitelist',
                'deployment_performed': False, 'plan_sha256': digest(plan_path.read_bytes()),
                'build_snapshot_boundary': plan.get('build_snapshot_boundary', 'This final handoff includes later test harness, policies, documentation and evidence. It is not the original Core3 build-context snapshot; current harness SHA must not be used as that snapshot SHA.'),
                'component_bindings': plan.get('component_bindings', {}),
                'deployed_core_binding': {'image': 'drop-agent:20261020.3', 'image_id': 'sha256:abb10f7f51f7f33e08248fde4c0d6ec0bbf200f5b4c9cde7248e8701cb25c4dc', 'helper_sha256': 'fa4f2daf655fbf8b78d7276c636d4350b85aee6a91e3e7ea03f6a0f1cd30b4f4'},
                'secret_scan': check, 'file_count': len(values), 'total_bytes': sum(map(len, values.values())),
                'files': [{'path': name, 'size': len(data), 'sha256': digest(data)} for name, data in values.items()]}
    encoded = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / (args.mode + '-manifest.json')
    manifest_path.write_bytes(encoded)
    result = {'mode': args.mode, 'file_count': len(values), 'total_bytes': manifest['total_bytes'],
              'manifest': str(manifest_path), 'manifest_sha256': digest(encoded), 'archive_created': False}
    if args.mode == 'final':
        archive = output / args.archive_name
        temporary = archive.with_suffix(archive.suffix + '.partial')
        with temporary.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as zipped, tarfile.open(fileobj=zipped, mode='w|') as package:
            for name, data in [*values.items(), ('DELIVERY_MANIFEST.json', encoded)]:
                item = tarfile.TarInfo(name)
                item.size = len(data)
                item.mode = 0o755 if name.endswith('.sh') else 0o644
                item.mtime = 0
                item.uid = item.gid = 0
                package.addfile(item, io.BytesIO(data))
        with temporary.open('rb') as stream:
            archive_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        # 重新打开归档，逐个成员与冻结字节核对，防止只验证内存清单而漏掉写包错误。
        with tarfile.open(temporary, 'r:gz') as package:
            members = package.getmembers()
            assert len(members) == len(values) + 1
            for member in members:
                assert member.isfile() and member.name in {*values, 'DELIVERY_MANIFEST.json'}
                expected = encoded if member.name == 'DELIVERY_MANIFEST.json' else values[member.name]
                assert digest(package.extractfile(member).read()) == digest(expected)
        temporary.replace(archive)
        (output / (archive.name + '.sha256')).write_text(archive_hash + '  ' + archive.name + '\n')
        result.update(archive_created=True, archive=str(archive), archive_bytes=archive.stat().st_size, archive_sha256=archive_hash, archive_members_verified=True)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
