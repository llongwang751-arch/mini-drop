"""Enroll a credential-free immutable snapshot of the user-selected Python CRM."""
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
                        help='Path to the user-owned Python CRM repository; read only.')
    parser.add_argument('--output', required=True, type=Path,
                        help='New or empty export directory outside the source repository.')
    args = parser.parse_args(argv)
    source_path = args.source.expanduser()
    if source_path.is_symlink() or not source_path.is_dir():
        parser.error('--source must be an existing, non-symlink Python CRM directory')
    crm = source_path.resolve()
    output = args.output.expanduser().absolute()
    if any(part.is_symlink() for part in (output, *output.parents)):
        parser.error('--output must not contain symlinks')
    output = output.resolve()
    if output.is_relative_to(crm):
        parser.error('--output must be outside --source to preserve the original repository')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        parser.error('--output must be a new or empty directory')
    output.mkdir(parents=True, exist_ok=True)
    selected = list((crm / 'crm').rglob('*.py')) + list((crm / 'crm/data').rglob('*.json')) + list((crm / 'crm/static').rglob('*.json'))
    selected += [crm / name for name in ('requirements.lock', 'pyproject.toml', 'LICENSE', 'tests/test_brand_palette.py')]
    manifest = {}
    for path in sorted(selected):
        relative = path.relative_to(crm)
        if path.is_symlink() or any(part in {'__pycache__', '.env', '.local', 'tmp'} for part in relative.parts):
            continue
        raw = path.read_bytes()
        if re.search(rb'\bsk-[a-zA-Z0-9]{24,}\b', raw) or (b'-----BEGIN ' + b'PRIVATE KEY-----') in raw:
            raise ValueError('SOURCE_CREDENTIAL_PATTERN_REJECTED')
        target = output / 'source' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        manifest[relative.as_posix()] = hashlib.sha256(raw).hexdigest()
    revision = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    descriptor = {'id': 'crm-python', 'label': 'Python CRM · 品牌配色预览', 'source_revision': revision,
                  'files': manifest, 'editable_files': ['crm/services/brand_palette.py'],
                  'readable_files': ['crm/services/brand_palette.py', 'crm/api/ui_context.py', 'tests/test_brand_palette.py'],
                  'test_preset': 'crm-brand-palette-v1', 'source_root': 'source',
                  'source_origin': 'user-owned ' + str(crm) + '; original not modified',
                  'workload': 'actual palette service; synthetic first-time color previews'}
    (output / 'manifest.json').write_text(json.dumps(descriptor, indent=2), encoding='utf-8')
    shutil.copytree(Path(__file__).parent / 'harness', output / 'harness', dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copyfile(Path(__file__).parent / 'Dockerfile', output / 'Dockerfile')
    shutil.copyfile(crm / 'requirements.lock', output / 'requirements.lock')
    archive = output.parent / 'crm-context.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for path in sorted(output.rglob('*')):
            if path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                tar.add(path, arcname=path.relative_to(output).as_posix(), recursive=False)
    receipt = {'source_revision': revision, 'source_files': len(manifest), 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
               'archive_bytes': archive.stat().st_size, 'real_env_read': False, 'production_data_included': False}
    (output.parent / 'crm-source-receipt.json').write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
