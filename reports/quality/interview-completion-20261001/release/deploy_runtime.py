"""Control-only source overlay; retain live secrets, old images and rollback config."""
from pathlib import Path
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

STAGE = Path(__file__).resolve().parent
TAG = STAGE.name.removeprefix('incoming-')
ROOT = Path('/opt/mini-drop-releases') / TAG
SERVICES = ('diagnosis-worker', 'analyzer', 'web')


def output(args):
    return subprocess.check_output(args, text=True).strip()


def inspect(service):
    return json.loads(output(['docker', 'inspect', 'mini-drop-control-' + service + '-1']))[0]


def save_private(path, data):
    def literal(value):
        if isinstance(value, str):
            return value.replace('$', '$$')
        if isinstance(value, list):
            return [literal(v) for v in value]
        if isinstance(value, dict):
            return {k: literal(v) for k, v in value.items()}
        return value
    path.write_text(json.dumps(literal(data), indent=2))
    path.chmod(0o600)


def health(service):
    for _ in range(45):
        obj = inspect(service)
        if obj['State'].get('Health', {}).get('Status') == 'healthy':
            return obj
        time.sleep(2)
    raise RuntimeError('health check failed: ' + service)


def switch(target):
    link = Path('/opt') / ('.mini-drop-switch-' + TAG + '-' + str(time.time_ns()))
    link.symlink_to(target)
    os.replace(link, '/opt/mini-drop-current')


def main():
    assert STAGE.parent == Path('/root') and TAG == json.loads((STAGE / 'manifest.json').read_text())['release_tag']
    old = Path('/opt/mini-drop-current').resolve()
    assert old.parent == ROOT.parent and old != ROOT and not ROOT.exists()
    assert shutil.disk_usage('/').free > 3 * 2**30
    manifest = json.loads((STAGE / 'manifest.json').read_text())
    for name, digest in {**manifest['files'], **manifest['deployment_files']}.items():
        p = (STAGE / 'source' / name).resolve()
        assert p.is_relative_to(STAGE / 'source') and p.is_file()
        assert hashlib.sha256(p.read_bytes()).hexdigest() == digest, name
    before = {s: inspect(s) for s in SERVICES}
    all_ids = output(['docker', 'ps', '-q']).splitlines()
    untouched = {obj['Name']: obj['Id'] for obj in json.loads(output(['docker', 'inspect', *all_ids]))
                 if obj['Name'] not in {'/mini-drop-control-' + s + '-1' for s in SERVICES}}
    shutil.copytree(old, ROOT, symlinks=True)
    for name in {**manifest['files'], **manifest['deployment_files']}:
        dst = ROOT / name
        assert dst.resolve().is_relative_to(ROOT)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(STAGE / 'source' / name, dst)
    private = ROOT / 'private'
    private.mkdir(mode=0o700, exist_ok=True)
    private.chmod(0o700)
    selected = {}
    images = {}
    for service, obj in before.items():
        if service == 'go-hotspot':
            # This older container's Compose env-file labels no longer resolve.
            # Recover its bounded runtime from inspect, without new privileges.
            host = obj['HostConfig']
            assert not obj['Mounts'] and not host['Privileged'] and not host['PidMode']
            assert not host.get('Sysctls') and not host.get('CapAdd') and not host.get('Ulimits')
            assert host['IpcMode'] == 'private' and not host['CpuQuota'] and not host['CpuPeriod']
            assert len(obj['NetworkSettings']['Networks']) == 1
            check = obj['Config']['Healthcheck']
            spec = {'restart': host['RestartPolicy']['Name'], 'read_only': host['ReadonlyRootfs'],
                    'tmpfs': [path + ':' + options for path, options in host['Tmpfs'].items()],
                    'cap_drop': host['CapDrop'], 'security_opt': host['SecurityOpt'],
                    'mem_limit': host['Memory'], 'cpus': host['NanoCpus'] / 1e9,
                    'pids_limit': host['PidsLimit'], 'working_dir': obj['Config']['WorkingDir'],
                    'healthcheck': {'test': check['Test'], 'interval': str(check['Interval']) + 'ns',
                                    'timeout': str(check['Timeout']) + 'ns', 'retries': check['Retries']},
                    'ports': [binding['HostIp'] + ':' + binding['HostPort'] + ':' + port
                              for port, bindings in host['PortBindings'].items() for binding in bindings],
                    'logging': {'driver': host['LogConfig']['Type'], 'options': host['LogConfig']['Config']}}
        else:
            labels = obj['Config']['Labels']
            args = ['docker', 'compose', '-p', 'mini-drop-control']
            for env in (labels.get('com.docker.compose.project.environment_file') or '').split(','):
                if env:
                    args += ['--env-file', env]
            for config in labels['com.docker.compose.project.config_files'].split(','):
                args += ['-f', config]
            base = json.loads(output(args + ['config', '--format', 'json']))
            spec = copy.deepcopy(base['services'][service])
        for key in ('build', 'depends_on', 'profiles', 'pull_policy'):
            spec.pop(key, None)
        rollback = 'mini-drop-rollback:' + service + '-' + TAG
        subprocess.run(['docker', 'tag', obj['Image'], rollback], check=True)
        spec.update(image=rollback, pull_policy='never')
        spec['environment'] = dict(v.split('=', 1) for v in obj['Config']['Env'] if '=' in v)
        selected[service] = spec
        image = 'mini-drop-' + service + ':' + TAG
        images[service] = image
        recipe = STAGE / ('Dockerfile.' + service)
        copies = ('COPY deploy/bin/apiserver /usr/local/bin/mini-drop-apiserver\n' if service == 'apiserver'
                  else 'COPY web/dist/ /usr/share/nginx/html/\n' if service == 'web'
                  else 'COPY server/ /app/server/\nCOPY analyzer/ /app/analyzer/\nCOPY scripts/ /app/scripts/\nCOPY contracts/ /app/contracts/\n')
        if service == 'go-hotspot':
            recipe = STAGE / 'source/demo/go-hotspot/Dockerfile'
        else:
            recipe.write_text('FROM ' + obj['Image'] + '\n' + copies)
        context = STAGE / 'source/demo/go-hotspot' if service == 'go-hotspot' else STAGE / 'source'
        subprocess.run(['docker', 'build', '-f', str(recipe), '-t', image, str(context)], check=True)
        if service == 'go-hotspot':
            subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'sh', image,
                            '-c', 'test -x /usr/local/bin/go-hotspot'], check=True)
        probe = ('from server.app.drop_insight.service import *; '
                 'from server.app.drop_insight.evidence import observed_count; '
                 'from server.app.metric_analyzers import *; '
                 'assert observed_count(None) is None; print("candidate imports passed")')
        if service in {'diagnosis-worker', 'analyzer'}:
            subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python', image, '-c', probe], check=True)
        if service == 'apiserver':
            subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'sh', image, '-c', 'test -x /usr/local/bin/mini-drop-apiserver'], check=True)
            actual = output(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'sha256sum', image,
                             '/usr/local/bin/mini-drop-apiserver']).split()[0]
            assert actual == manifest['deployment_files']['deploy/bin/apiserver']
    network = next(iter(before['web']['NetworkSettings']['Networks']))
    rollback = {'services': selected, 'networks': {'default': {'external': True, 'name': network}}}
    save_private(private / 'rollback.compose.json', rollback)
    runtime = copy.deepcopy(rollback)
    for service, spec in runtime['services'].items():
        spec['image'] = images[service]
        if service == 'diagnosis-worker':
            agent_env = dict(v.split('=', 1) for v in inspect('demo-agent')['Config']['Env'] if '=' in v)
            assert agent_env['AGENT_ID'] == manifest['fault_lab_agent_id']
            spec['environment']['MINI_DROP_FAULT_LAB_AGENT_ID'] = manifest['fault_lab_agent_id']
        for mount in spec.get('volumes', []):
            if isinstance(mount, dict) and mount.get('target') == '/workspace-source':
                mount['source'] = str(ROOT)
    save_private(private / 'runtime.compose.json', runtime)
    (ROOT / 'source-manifest-20260930.json').write_text(json.dumps(manifest, indent=2))
    def compose(file, service):
        subprocess.run(['docker', 'compose', '-p', 'mini-drop-control', '-f', str(private / file),
                        'up', '-d', '--no-deps', '--no-build', '--pull', 'never', service], check=True)
    try:
        for service in reversed(SERVICES):
            compose('runtime.compose.json', service)
            health(service)
        after = {s: inspect(s) for s in SERVICES}
        for name, container_id in untouched.items():
            assert json.loads(output(['docker', 'inspect', name]))[0]['Id'] == container_id, name
        switch(ROOT)
    except BaseException as activation_error:
        rollback_errors = []
        try:
            for service in SERVICES:
                try:
                    compose('rollback.compose.json', service)
                    health(service)
                except BaseException as rollback_error:
                    rollback_errors.append(service + ': ' + type(rollback_error).__name__)
        finally:
            switch(old)
        if rollback_errors:
            raise RuntimeError('rollback requires attention: ' + ', '.join(rollback_errors)) from activation_error
        raise
    summary = {'release': str(ROOT), 'previous_release': str(old), 'status': 'HEALTHY',
               'manifest_sha256': hashlib.sha256((STAGE / 'manifest.json').read_bytes()).hexdigest(),
               'source_head': manifest['git_head'], 'changed_services': list(SERVICES),
               'images': {s: {'old': before[s]['Image'], 'new': after[s]['Image'], 'tag': images[s]} for s in SERVICES},
               'untouched_container_count': len(untouched), 'rollback_config': str(private / 'rollback.compose.json')}
    (ROOT / 'deployment-20260930.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
