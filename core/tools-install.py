#!/usr/bin/env python3
"""构建时安装固定版本与摘要的采集工具；Agent runner 运行时不下载网络依赖。"""
import hashlib
import json
import os
import pathlib
import platform
import subprocess
import sys
import tarfile
import urllib.request

ROOT = pathlib.Path('/opt/drop-tools')
ROOT.mkdir(mode=0o755, parents=True, exist_ok=True)
architecture = {'x86_64': 'x64', 'aarch64': 'arm64'}[platform.machine()]
digest = {'x64': '3b13a38a0063f6970d985a379ddaed91bcf37e239a1ea461d09eacf629f3dde1',
          'arm64': 'd0cb9c97c380672b625c06e5a3ed578e990f4674c6aae8b5249f584c4c9ac50e'}[architecture]
if '--manifest-only' not in sys.argv:
    url = f'https://github.com/async-profiler/async-profiler/releases/download/v4.1/async-profiler-4.1-linux-{architecture}.tar.gz'
    archive = ROOT / 'async-profiler.tar.gz'
    offline_archive = os.environ.get('DROP_ASYNC_PROFILER_ARCHIVE')
    if offline_archive:
        # 仅构建期输入：两种来源都必须提供相同的固定摘要。
        with pathlib.Path(offline_archive).open('rb') as source:
            payload = source.read(32 * 1024 * 1024 + 1)
    else:
        with urllib.request.urlopen(url, timeout=90) as response:
            payload = response.read(32 * 1024 * 1024 + 1)
    if len(payload) > 32 * 1024 * 1024:
        raise RuntimeError('async-profiler release exceeds build input limit')
    archive.write_bytes(payload)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
        raise RuntimeError('Official async-profiler release SHA256 mismatch')
    with tarfile.open(archive) as source:
        source.extractall(ROOT, filter='data')
    archive.unlink()
    directory = next(ROOT.glob('async-profiler-4.1*'))
    (ROOT / 'async-profiler').symlink_to(directory)
    subprocess.run(['/usr/bin/python3', '-m', 'venv', str(ROOT / 'venv')], check=True)
    subprocess.run([str(ROOT / 'venv/bin/pip'), 'install', '--disable-pip-version-check',
                    '--index-url', 'https://mirrors.aliyun.com/pypi/simple/',
                    'py-spy==0.4.1', 'memray==1.20.0'], check=True)
tools = {'asprof': ROOT / 'async-profiler/bin/asprof', 'py-spy': ROOT / 'venv/bin/py-spy',
         'memray': ROOT / 'venv/bin/memray', 'jcmd': pathlib.Path('/usr/bin/jcmd'),
         'bpftrace': pathlib.Path('/usr/bin/bpftrace'), 'perf2bolt': pathlib.Path('/usr/bin/perf2bolt-16'),
         'llvm-bolt': pathlib.Path('/usr/bin/llvm-bolt-16'), 'gdb': pathlib.Path('/usr/bin/gdb')}
tools['perf'] = next(pathlib.Path('/usr/lib/linux-tools').glob('*/perf'))
tools['docker'] = pathlib.Path('/usr/bin/docker')
tools['ctr'] = pathlib.Path('/usr/bin/ctr')
manifest = {'schema_version': 1, 'async_profiler_release_sha256': digest, 'tools': {}}
for name, path in tools.items():
    resolved = path.resolve(strict=True)
    manifest['tools'][name] = {'path': str(path), 'sha256': hashlib.sha256(resolved.read_bytes()).hexdigest()}
    support=[]
    if name=='asprof':
        support=list((ROOT/'async-profiler/lib').rglob('*.so'))
    elif name=='memray':
        package=next((ROOT/'venv/lib').glob('python*/site-packages/memray'))
        support=[item for item in package.rglob('*') if item.is_file() and item.suffix in ('.so','.py','.lldb','.gdb')]
    elif name=='gdb':
        launcher=pathlib.Path('/usr/local/bin/drop_gdb_ptrace')
        launcher.resolve(strict=True)
        alias=ROOT/'gdb-bin/gdb'
        alias.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        if alias.exists() or alias.is_symlink():
            if not alias.is_symlink() or alias.resolve(strict=True)!=launcher:
                raise RuntimeError('Unexpected GDB launcher alias')
        else:
            alias.symlink_to(launcher)
        support=[launcher, alias]
    manifest['tools'][name]['support_files']={str(item if name=='gdb' else item.resolve()):hashlib.sha256(item.read_bytes()).hexdigest() for item in support}
manifest['packages'] = subprocess.check_output(['dpkg-query', '-W', '-f=${Package}=${Version}\n'], text=True).splitlines()
(ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
# Agent 在启动和每次运行前核验这些精确的可执行文件字节。
with (ROOT / 'integrity.env').open('w') as output:
    for name, item in manifest['tools'].items():
        env = name.upper().replace('-', '_')
        output.write(f'{env}_EXECUTABLE={item["path"]}\n{env}_SHA256={item["sha256"]}\n')
