"""Run on the existing Control host. Preserve live credentials and rollback config.

Usage: python3 scripts/release_sre_cloud.py prepare|build|activate|rollback|finish
prepare accepts only the provider settings JSON on stdin, never logs it.
The release directory must be a new child of /opt/mini-drop-releases.
"""
from pathlib import Path
import copy
import json
import os
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
assert ROOT.parent == Path('/opt/mini-drop-releases')
TAG = ROOT.name
PRIVATE = ROOT / 'private'
SERVICES = ['diagnosis-worker', 'analyzer', 'web']

def run(args, **kw):
    return subprocess.run(args, check=True, **kw)

def inspect(service):
    return json.loads(subprocess.check_output(['docker', 'inspect', 'mini-drop-control-' + service + '-1']))[0]

def save(path, obj):
    if path.name.endswith('.compose.json'):
        def literal(value):
            if isinstance(value, str): return value.replace('$', '$$')
            if isinstance(value, list): return [literal(v) for v in value]
            if isinstance(value, dict): return {k: literal(v) for k,v in value.items()}
            return value
        obj = literal(obj)
    path.write_text(json.dumps(obj, indent=2), encoding='utf-8')
    path.chmod(0o600)

def compose(file, *args):
    return run(['docker', 'compose', '-p', 'mini-drop-control', '-f', str(PRIVATE / file), *args])

def healthy(service):
    for _ in range(45):
        obj = inspect(service)
        if obj['State'].get('Health', {}).get('Status') == 'healthy':
            return
        time.sleep(2)
    raise RuntimeError('Service not healthy: ' + service)

def prepare():
    PRIVATE.mkdir(mode=0o700)
    old = inspect('diagnosis-worker')
    labels = old['Config']['Labels']
    args = ['docker', 'compose', '-p', 'mini-drop-control']
    for f in (labels.get('com.docker.compose.project.environment_file') or '').split(','):
        if f:
            args += ['--env-file', f]
    for f in labels['com.docker.compose.project.config_files'].split(','):
        args += ['-f', f]
    base = json.loads(subprocess.check_output(args + ['config', '--format', 'json']))
    selected = {s: copy.deepcopy(base['services'][s]) for s in SERVICES}
    summary = {'tag': TAG, 'previous_release': os.path.realpath('/opt/mini-drop-current'), 'images': {}, 'rollback_images': {}}
    for service, spec in selected.items():
        obj = inspect(service)
        rollback = f'mini-drop-sre-rollback:{service}-{TAG}'
        run(['docker', 'tag', obj['Image'], rollback])
        summary['rollback_images'][service] = rollback
        summary['images'][service] = obj['Config']['Image']
        spec['image'] = rollback
        spec['environment'] = dict(item.split('=', 1) for item in obj['Config']['Env'] if '=' in item)
        for key in ['build', 'depends_on', 'profiles', 'pull_policy']:
            spec.pop(key, None)
        spec['pull_policy'] = 'never'
    network = next(iter(old['NetworkSettings']['Networks']))
    rollback_config = {'services': selected, 'networks': {'default': {'external': True, 'name': network}}}
    save(PRIVATE / 'rollback.compose.json', rollback_config)
    new = copy.deepcopy(rollback_config)
    python_image = f'mini-drop-python-worker:{TAG}'
    new['services']['diagnosis-worker']['image'] = python_image
    new['services']['analyzer']['image'] = python_image
    new['services']['web']['image'] = f'mini-drop-web:{TAG}'
    for v in new['services']['diagnosis-worker'].get('volumes', []):
        if v.get('target') == '/workspace-source':
            v['source'] = str(ROOT)
    secret = json.load(sys.stdin)
    allowed = {'MINI_DROP_AI_API_KEY', 'SILICONFLOW_API_KEY', 'MINI_DROP_AI_MODEL', 'MINI_DROP_AI_BASE_URL', 'MINI_DROP_AI_PROVIDER'}
    assert set(secret) <= allowed and secret.get('SILICONFLOW_API_KEY')
    env = new['services']['diagnosis-worker']['environment']
    env.update(secret)
    env.update(MINI_DROP_AI_ENABLED='full', MINI_DROP_NLP_ENABLED='true', MINI_DROP_RCA_ENABLED='true', MINI_DROP_SUMMARIZE_ENABLED='true',
               MINI_DROP_RETRIEVAL_MODE='hybrid', MINI_DROP_CHROMA_HOST='chroma', MINI_DROP_CHROMA_PORT='8000',
               MINI_DROP_EMBEDDING_MODEL='Qwen/Qwen3-Embedding-4B', MINI_DROP_EMBEDDING_DIMENSIONS='1024',
               MINI_DROP_RERANK_MODEL='Qwen/Qwen3-Reranker-4B', MINI_DROP_AGENT_MODEL_TIMEOUT_SEC='90', MINI_DROP_SILICONFLOW_ENABLE_THINKING='false')
    new['services']['chroma'] = {'image': python_image, 'pull_policy': 'never',
        'command': ['chroma', 'run', '--host', '0.0.0.0', '--port', '8000', '--path', '/var/lib/mini-drop-runtime/chroma'],
        'restart': 'unless-stopped', 'volumes': ['knowledge_chroma:/var/lib/mini-drop-runtime'],
        'healthcheck': {'test': ['CMD', 'python', '-c', "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v2/heartbeat',timeout=3)"], 'interval': '10s', 'timeout': '5s', 'retries': 12}}
    new['volumes'] = {'knowledge_chroma': {'name': 'mini-drop-control_knowledge_chroma'}}
    save(PRIVATE / 'runtime.compose.json', new)
    save(ROOT / 'release-summary.json', summary)
    print(json.dumps({'prepared': TAG, 'previous_release': summary['previous_release'], 'changed_services': SERVICES, 'new_service': 'chroma'}))

def build():
    summary = json.loads((ROOT / 'release-summary.json').read_text())
    run(['docker', 'build', '--build-arg', 'BASE_IMAGE='+summary['rollback_images']['diagnosis-worker'], '-f', str(ROOT/'deploy/dockerfiles/sre-release-python.Dockerfile'), '-t', f'mini-drop-python-worker:{TAG}', str(ROOT)])
    run(['docker', 'build', '--build-arg', 'WEB_RUNTIME_IMAGE='+summary['rollback_images']['web'], '-f', str(ROOT/'deploy/dockerfiles/web-prebuilt.Dockerfile'), '-t', f'mini-drop-web:{TAG}', str(ROOT/'web/dist')])

def activate():
    compose('runtime.compose.json', 'up', '-d', '--no-deps', 'chroma')
    healthy('chroma')
    compose('runtime.compose.json', 'run', '--rm', '--no-deps', 'diagnosis-worker', 'python', 'scripts/build_knowledge_index.py')
    for service in SERVICES:
        compose('runtime.compose.json', 'up', '-d', '--no-deps', service)
        healthy(service)
    print(json.dumps({'activated': TAG, 'status': 'HEALTHY'}))

def rollback():
    for service in SERVICES:
        compose('rollback.compose.json', 'up', '-d', '--no-deps', service)
        healthy(service)
    summary = json.loads((ROOT / 'release-summary.json').read_text())
    switch_current(summary['previous_release'])

def switch_current(target):
    current = Path('/opt/mini-drop-current')
    assert current.is_symlink()
    staging = Path('/opt/mini-drop-current-' + TAG)
    staging.symlink_to(target)
    staging.replace(current)

if __name__ == '__main__':
    {'prepare': prepare, 'build': build, 'activate': activate, 'rollback': rollback, 'finish': lambda: switch_current(ROOT)}[sys.argv[1]]()
