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
from datetime import datetime, timezone

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
        ready = obj['State'].get('Health', {}).get('Status') == 'healthy'
        if service == 'demo-agent' and obj['State'].get('Running') is True:
            started = datetime.fromisoformat(obj['State']['StartedAt'].replace('Z', '+00:00'))
            agent_id = dict(v.split('=', 1) for v in obj['Config']['Env'] if '=' in v)['AGENT_ID']
            pids = [inspect(s)['State']['Pid'] for s in ('python-hotspot','java-hotspot','cpp-hotspot')]
            code = ('import json; from sqlalchemy import select; '
                    'from server.app.database import new_session; '
                    'from server.app.models import AgentModel, ProcessCandidateSnapshotModel, ProcessCandidateModel; '
                    's=new_session(); a=s.get(AgentModel,' + repr(agent_id) + '); '
                    'v=s.execute(select(ProcessCandidateSnapshotModel).where(ProcessCandidateSnapshotModel.agent_id==' + repr(agent_id) + ').order_by(ProcessCandidateSnapshotModel.received_at.desc()).limit(1)).scalar_one_or_none(); '
                    'p=[] if v is None else list(s.scalars(select(ProcessCandidateModel.pid).where(ProcessCandidateModel.snapshot_id==v.id))); '
                    'print(json.dumps({"heartbeat":None if a is None else a.last_heartbeat_at.isoformat(),"snapshot":None if v is None else v.received_at.isoformat(),"complete":False if v is None else v.complete,"pids":p})); s.close()')
            try:
                receipt = json.loads(output(['docker','exec','mini-drop-control-diagnosis-worker-1','python','-c',code]))
                ready = (receipt['complete'] and all(pid in receipt['pids'] for pid in pids)
                         and all(receipt[k] and datetime.fromisoformat(receipt[k]) >= started for k in ('heartbeat','snapshot')))
            except (subprocess.CalledProcessError, ValueError, TypeError):
                ready = False
        if ready:
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
    assert shutil.disk_usage('/').free > json.loads((STAGE / 'manifest.json').read_text())['deployment_capacity']['required_free_bytes']
    manifest = json.loads((STAGE / 'manifest.json').read_text())
    manifest['cpp_binary_sha256'] = output(['docker','exec','mini-drop-control-cpp-hotspot-1','sha256sum','/usr/local/bin/cpp-hotspot']).split()[0]
    cpp_symbol_image = inspect('cpp-hotspot')['Image']
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
    volume_refs = {}
    for service, obj in before.items():
        if service in {'python-hotspot', 'java-hotspot', 'cpp-hotspot', 'demo-agent'}:
            # Recover the currently running bounded runtime when old labels
            # refer to missing env files or no longer include this service.
            host = obj['HostConfig']
            assert not host['Privileged']
            if service != 'demo-agent':
                assert not obj['Mounts'] and not host['PidMode'] and not host.get('CapAdd')
            assert len(obj['NetworkSettings']['Networks']) == 1
            spec = {'restart': host['RestartPolicy']['Name'], 'read_only': host['ReadonlyRootfs'],
                    'tmpfs': [path + ':' + options for path, options in (host.get('Tmpfs') or {}).items()],
                    'cap_drop': host.get('CapDrop') or [], 'cap_add': host.get('CapAdd') or [],
                    'security_opt': host.get('SecurityOpt') or [],
                    'working_dir': obj['Config']['WorkingDir'],
                    'ports': [binding['HostIp'] + ':' + binding['HostPort'] + ':' + port
                              for port, bindings in (host.get('PortBindings') or {}).items() for binding in bindings],
                    'logging': {'driver': host['LogConfig']['Type'], 'options': host['LogConfig']['Config']}}
            network_mode = host['NetworkMode']
            if network_mode in {'host', 'none'} or network_mode.startswith('container:'):
                spec['network_mode'] = network_mode
            if host.get('IpcMode'): spec['ipc'] = host['IpcMode']
            for key, value in [('mem_limit', host['Memory']), ('pids_limit', host.get('PidsLimit')),
                               ('cpu_quota', host['CpuQuota']), ('cpu_period', host['CpuPeriod'])]:
                if value: spec[key] = value
            if host['NanoCpus']: spec['cpus'] = host['NanoCpus'] / 1e9
            if host['PidMode']: spec['pid'] = host['PidMode']
            if host.get('Sysctls'): spec['sysctls'] = host['Sysctls']
            if host.get('Ulimits'):
                spec['ulimits'] = {row['Name']: {'soft': row['Soft'], 'hard': row['Hard']} for row in host['Ulimits']}
            if obj['Config'].get('User'): spec['user'] = obj['Config']['User']
            if obj['Config'].get('Cmd'): spec['command'] = obj['Config']['Cmd']
            if obj['Config'].get('Entrypoint'): spec['entrypoint'] = obj['Config']['Entrypoint']
            if obj['Config'].get('Healthcheck'):
                check = obj['Config']['Healthcheck']
                spec['healthcheck'] = {'test': check['Test'], 'interval': str(check['Interval']) + 'ns',
                                      'timeout': str(check['Timeout']) + 'ns', 'retries': check['Retries']}
                if check.get('StartPeriod'): spec['healthcheck']['start_period'] = str(check['StartPeriod']) + 'ns'
            if obj['Mounts']:
                spec['volumes'] = []
                for index, mount in enumerate(obj['Mounts']):
                    item = {'type': mount['Type'], 'target': mount['Destination'], 'read_only': not mount['RW']}
                    if mount['Type'] == 'volume':
                        label = service.replace('-', '_') + '_volume_' + str(index)
                        volume_refs[label] = {'external': True, 'name': mount['Name']}
                        item['source'] = label
                    else:
                        assert mount['Type'] == 'bind'
                        item['source'] = mount['Source']
                        item['bind'] = {'propagation': mount.get('Propagation') or 'rprivate'}
                    spec['volumes'].append(item)
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
        if service == 'cpp-hotspot':
            recipe = STAGE / 'source/demo/cpp-hotspot/Dockerfile'
            context = STAGE / 'source/demo/cpp-hotspot'
        else:
            context = STAGE / 'source'
            if service == 'python-hotspot':
                copies = 'COPY demo/python-hotspot/app.py /app/app.py\n'
            elif service == 'java-hotspot':
                recipe.write_text('FROM ' + obj['Image'] + ' AS build\n'
                    + 'COPY demo/java-hotspot/Hotspot.java /src/Hotspot.java\n'
                    + 'RUN mkdir -p /classes && javac -encoding UTF-8 -g -d /classes /src/Hotspot.java\n'
                    + 'FROM ' + obj['Image'] + '\nCOPY --from=build /classes/ /app/\n')
            elif service == 'demo-agent':
                copies = 'COPY deploy/bin/mini-drop-native-agent /usr/local/bin/mini-drop-native-agent\nRUN chmod 0755 /usr/local/bin/mini-drop-native-agent\n'
            if service in {'diagnosis-worker', 'analyzer'}:
                recipe.write_text('FROM ' + cpp_symbol_image + ' AS symbols\n'
                    + 'FROM ' + obj['Image'] + '\n'
                    + 'COPY --from=symbols /usr/local/bin/cpp-hotspot /usr/local/bin/cpp-hotspot\n' + copies)
            elif service != 'java-hotspot':
                recipe.write_text('FROM ' + obj['Image'] + '\n' + copies)
        subprocess.run(['docker', 'build', '-f', str(recipe), '-t', image, str(context)], check=True)
        if service == 'demo-agent':
            linkage = output(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'ldd', image,
                              '/usr/local/bin/mini-drop-native-agent'])
            assert 'not found' not in linkage
            actual = output(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'sha256sum', image,
                             '/usr/local/bin/mini-drop-native-agent']).split()[0]
            assert actual == manifest['deployment_files']['deploy/bin/mini-drop-native-agent']
        if service in {'cpp-hotspot', 'diagnosis-worker', 'analyzer'}:
            actual = output(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'sha256sum', image,
                             '/usr/local/bin/cpp-hotspot']).split()[0]
            if service == 'cpp-hotspot':
                manifest['cpp_binary_sha256'] = actual
            else:
                assert actual == manifest['cpp_binary_sha256']
        probe = ('from server.app.drop_insight.service import *; '
                 'from server.app.drop_insight.evidence import observed_count; '
                 'from server.app.metric_analyzers import *; '
                 'assert observed_count(None) is None; import shutil; assert shutil.which("addr2line"); print("candidate imports and source toolchain passed")')
        if service in {'diagnosis-worker', 'analyzer'}:
            subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python', image, '-c', probe], check=True)
        if service == 'apiserver':
            subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'sh', image, '-c', 'test -x /usr/local/bin/mini-drop-apiserver'], check=True)
            actual = output(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'sha256sum', image,
                             '/usr/local/bin/mini-drop-apiserver']).split()[0]
            assert actual == manifest['deployment_files']['deploy/bin/apiserver']
    network = next(iter(inspect('web')['NetworkSettings']['Networks']))
    rollback = {'services': selected, 'networks': {'default': {'external': True, 'name': network}}, 'volumes': volume_refs}
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
        resource_keys = ('NanoCpus','CpuQuota','CpuPeriod','Memory','PidsLimit','Privileged','PidMode','NetworkMode','IpcMode','CapAdd','CapDrop','SecurityOpt','Ulimits','ReadonlyRootfs','Tmpfs')
        for service in SERVICES:
            assert all(after[service]['HostConfig'].get(key) == before[service]['HostConfig'].get(key) for key in resource_keys), service
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
