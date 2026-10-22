"""从显式构建镜像复制静态制品，不启动服务，不读取任何运行容器或卷。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid


def command(argv):
    try:
        result = subprocess.run(argv, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError('BUILD_BINDING_CONTROL_FAILED') from None
    if result.returncode or len(result.stdout) + len(result.stderr) > 1024 * 1024:
        raise RuntimeError('BUILD_BINDING_CONTROL_FAILED')
    return result.stdout


def extract(images, output):
    if not output.is_absolute() or output.exists():
        raise RuntimeError('NEW_ABSOLUTE_BUILD_OUTPUT_REQUIRED')
    output.mkdir(parents=True, mode=0o700)
    token = uuid.uuid4().hex
    label = 'drop.quality.static.extract'
    image_ids = {}
    for role in ('core', 'api', 'web'):
        image_id = command(['docker', 'image', 'inspect', images[role], '--format', '{{.Id}}']).decode().strip()
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', image_id):
            raise RuntimeError('BUILD_IMAGE_ID_REQUIRED')
        image_ids[role] = image_id
        name = 'drop-quality-extract-' + token[:12] + '-' + role
        try:
            command(['docker', 'create', '--name', name, '--label', label + '=' + token, '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--entrypoint', '/bin/true', image_id])
            if role == 'core':
                (output / 'core').mkdir()
                for binary in ('drop_server', 'drop_agent', 'drop_workload'):
                    command(['docker', 'cp', name + ':/usr/local/bin/' + binary, str(output / 'core' / binary)])
            elif role == 'api':
                command(['docker', 'cp', name + ':/usr/local/bin/drop-api', str(output / 'drop-api')])
            else:
                (output / 'web').mkdir()
                command(['docker', 'cp', name + ':/usr/share/nginx/html/.', str(output / 'web')])
        finally:
            try:
                owner = command(['docker', 'inspect', '--format', '{{index .Config.Labels "' + label + '"}}', name]).decode().strip()
                if owner == token:
                    command(['docker', 'rm', name])
            except RuntimeError:
                raise RuntimeError('BUILD_BINDING_CLEANUP_FAILED') from None
    bindings = {'api_binary': str(output / 'drop-api'), 'core_binaries': str(output / 'core'), 'web_dist': str(output / 'web')}
    (output / 'bindings.json').write_text(json.dumps(bindings) + '\n')
    (output / 'images.json').write_text(json.dumps(images) + '\n')
    receipt = {'schema_version': 1, 'provenance': 'static extraction from explicitly selected build images; no service started', 'images': image_ids,
               'artifact_sha256': {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest() for p in output.rglob('*') if p.is_file() and p.name not in ('bindings.json', 'images.json')},
               'limitation': 'Image extraction alone does not prove current-source compilation; retain preceding CI or local build receipts.'}
    (output / 'binding-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('core', 'api', 'web', 'analyzer'):
        parser.add_argument('--' + name + '-image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    images = {name: getattr(args, name + '_image') for name in ('core', 'api', 'web', 'analyzer')}
    images['nginx'] = images['web']
    try:
        extract(images, args.output)
    except Exception:
        print(json.dumps({'passed': False, 'error_code': 'BUILD_BINDING_EXTRACTION_FAILED'}))
        return 1
    print(json.dumps({'passed': True, 'services_started': 0}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
