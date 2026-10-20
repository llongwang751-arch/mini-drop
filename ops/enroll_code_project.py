"""Operator-only enrollment of a credential-free immutable source snapshot.

Not exposed as an Agent tool. Run as root on the Docker host, after reviewing
the snapshot, fixed test preset and runner image built from the checked harness.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess


def enroll(context, root, runner, workload, container, agent):
    if os.geteuid() != 0:
        raise RuntimeError('ROOT_OPERATOR_REQUIRED')
    context, root = Path(context).resolve(), Path(root).resolve()
    manifest = json.loads((context / 'manifest.json').read_text())
    project = manifest['id']
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', project):
        raise RuntimeError('INVALID_PROJECT_ID')
    source = context / 'source'
    if len(manifest['files']) > 4096 or manifest['test_preset'] not in {'crm-brand-palette-v1', 'saber-fts5-v1'}:
        raise RuntimeError('UNSUPPORTED_PRESET')
    revision = hashlib.sha256(json.dumps(manifest['files'], sort_keys=True).encode()).hexdigest()
    if revision != manifest['source_revision']:
        raise RuntimeError('SNAPSHOT_REVISION_MISMATCH')
    total = 0
    for name, expected in manifest['files'].items():
        parts = PurePosixPath(name)
        if parts.is_absolute() or '..' in parts.parts or '\\' in name:
            raise RuntimeError('SNAPSHOT_PATH_REJECTED')
        path = source / name
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            raise RuntimeError('SNAPSHOT_PATH_REJECTED')
        data = path.read_bytes()
        total += len(data)
        if hashlib.sha256(data).hexdigest() != expected or total > 32 * 1024 * 1024:
            raise RuntimeError('SNAPSHOT_CONTENT_REJECTED')
    def image_id(name):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}', name):
            raise RuntimeError('IMAGE_REFERENCE_REJECTED')
        value = subprocess.check_output(['docker', 'image', 'inspect', name, '--format', '{{.Id}}'], text=True).strip()
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', value):
            raise RuntimeError('IMAGE_ID_REJECTED')
        return value
    manifest['runner_image_id'] = image_id(runner)
    if container:
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', container) or not agent or len(agent) > 128:
            raise RuntimeError('RUNTIME_BINDING_REJECTED')
        manifest.update(workload_container=container, agent_id=agent, workload_image_id=image_id(workload or runner))
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != 0 or root.stat().st_mode & 0o077:
        raise RuntimeError('EXECUTOR_ROOT_MUST_BE_ROOT_0700')
    target = root / 'registry' / project
    if target.exists():
        old = json.loads((target / 'manifest.json').read_text())
        if old['source_revision'] != revision:
            raise RuntimeError('EXISTING_PROJECT_VERSION_IMMUTABLE')
        for name, digest in manifest['files'].items():
            if hashlib.sha256((target / 'source' / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError('EXISTING_SOURCE_CHANGED')
    else:
        target.mkdir(mode=0o700, parents=True)
        shutil.copytree(source, target / 'source')
    temporary = target / ('.manifest-' + secrets.token_hex(8))
    temporary.write_text(json.dumps(manifest, indent=2))
    temporary.chmod(0o600)
    temporary.replace(target / 'manifest.json')
    print(json.dumps({'enrolled': project, 'source_revision': revision, 'files': len(manifest['files']), 'runner_image_id': manifest['runner_image_id']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--context', required=True)
    parser.add_argument('--root', default='/opt/drop-code-executor')
    parser.add_argument('--runner-image', required=True)
    parser.add_argument('--workload-image')
    parser.add_argument('--workload-container')
    parser.add_argument('--agent-id')
    args = parser.parse_args()
    enroll(args.context, args.root, args.runner_image, args.workload_image, args.workload_container, args.agent_id)
