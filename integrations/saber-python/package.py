"""只导出用户指定的 Saber 组件及原始测试，不复制环境文件或用户数据。

导出 manifest 绑定文件摘要与可修改范围；受控 harness 单独复制，不能成为模型补丁目标。
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import tarfile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path,
                        help='Path to the user-owned Python Saber repository; read only.')
    parser.add_argument('--output', required=True, type=Path,
                        help='New or empty export directory outside the source repository.')
    args = parser.parse_args(argv)
    source_path = args.source.expanduser()
    if source_path.is_symlink() or not source_path.is_dir():
        parser.error('--source must be an existing, non-symlink Python Saber directory')
    source = source_path.resolve()
    output = args.output.expanduser().absolute()
    if any(part.is_symlink() for part in (output, *output.parents)):
        parser.error('--output must not contain symlinks')
    output = output.resolve()
    if output.is_relative_to(source):
        parser.error('--output must be outside --source to preserve the original repository')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        parser.error('--output must be a new or empty directory')
    output.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in ('internal/rag/fts5_index.py', 'tests/test_fts5_index.py'):
        path = source / name
        if path.is_symlink():
            raise RuntimeError('SOURCE_SYMLINK_REJECTED')
        data = path.read_bytes()
        if re.search(rb'\bsk-[a-zA-Z0-9]{24,}\b', data) or b'-----BEGIN PRIVATE KEY-----' in data:
            raise RuntimeError('SOURCE_CREDENTIAL_PATTERN_REJECTED')
        target = output / 'source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files[name] = hashlib.sha256(data).hexdigest()
    revision = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    manifest = {'id': 'saber-python', 'label': 'Python Saber · 知识库 FTS5 查询', 'source_revision': revision,
                'files': files, 'editable_files': ['internal/rag/fts5_index.py'],
                'readable_files': list(files), 'test_preset': 'saber-fts5-v1', 'source_root': 'source',
                'source_origin': str(source) + '; user-owned source not modified, no .env or user databases read',
                'workload': 'real Saber FTS5 query helpers and SQLite retrieval with synthetic 1K-8K queries and tenant-scoped documents; only fts_tokenize/build_fts_query may change, SQL class and tests frozen; not full app startup'}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copytree(Path(__file__).parent / 'harness', output / 'harness', dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copyfile(Path(__file__).parent / 'Dockerfile', output / 'Dockerfile')
    archive = output.parent / 'saber-context.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for path in sorted(output.rglob('*')):
            if path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                tar.add(path, arcname=path.relative_to(output).as_posix(), recursive=False)
    receipt = {'source_revision': revision, 'source_files': len(files), 'files': files,
               'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'archive_bytes': archive.stat().st_size,
               'real_env_read': False, 'production_data_included': False, 'full_app_startup': False}
    (output.parent / 'saber-source-receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
