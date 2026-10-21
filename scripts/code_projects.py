"""管理员离线登记通用 Python 项目快照，不对浏览器提供 shell 或上传执行入口。

只复制 manifest 明确列出的源文件，保留原仓库不变；文件摘要生成不可变 source_revision。
镜像必须先由管理员审阅并构建，再以实际 sha256 ID 登记。登记脚本不联网、不构建/启动镜像。
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('drop_code_executor_contract', ROOT / 'ops/code_executor.py')
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


def prepare(source, template, destination, image):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    contract.require(source.is_dir() and not destination.exists() and not destination.is_relative_to(source), 'REGISTRY_DESTINATION_INVALID')
    contract.require(re.fullmatch(r'sha256:[0-9a-f]{64}', image or ''), 'PROJECT_IMAGE_NOT_PINNED')
    manifest = json.loads(Path(template).read_text(encoding='utf-8'))
    names = manifest.get('editable_files', []) + manifest.get('frozen_files', [])
    contract.require(len(names) == len(set(names)), 'PROJECT_FILES_INVALID')
    files = {}
    for name in names:
        contract.enrolled_path(name)
        file = source / name
        contract.require(file.is_file() and not file.is_symlink() and file.resolve().is_relative_to(source)
                         and file.stat().st_size <= 131072
                         and all(not parent.is_symlink() for parent in file.parents if parent != source.parent), 'PROJECT_SOURCE_UNSAFE')
        files[name] = hashlib.sha256(file.read_bytes()).hexdigest()
    manifest['files'] = files
    manifest['source_revision'] = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    manifest['runner_image_id'] = image
    contract.require(isinstance(manifest.get('id'), str) and re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', manifest['id']), 'PROJECT_ID_INVALID')
    contract.validate_generic_manifest(manifest)
    destination.mkdir(mode=0o700, parents=True)
    snapshot = destination / 'source'
    snapshot.mkdir(mode=0o755)
    snapshot.chmod(0o755)
    for name in files:
        target = snapshot / name
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
        target.chmod(0o644)
    for directory in snapshot.rglob('*'):
        if directory.is_dir():
            directory.chmod(0o755)
    (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (destination / 'manifest.json').chmod(0o600)
    return {'id': manifest['id'], 'source_revision': manifest['source_revision'], 'runner_image_id': image,
            'registered_files': len(files), 'output_contract': manifest['output_contract'], 'source_repository_modified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--image-id', required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.manifest, args.destination, args.image_id), ensure_ascii=False))


if __name__ == '__main__':
    main()
