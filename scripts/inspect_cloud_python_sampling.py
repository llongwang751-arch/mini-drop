"""Bounded sampling-mode experiment on the isolated cloud Python demo only.

The lab truth is evaluator-only; this does not create VERIFIED agent evidence.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verify_interview_demo import Client, items_of
from analyzer.mini_drop_analyzer.pyspy_analyzer import analyze_speedscope


def inspect(name):
    return json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]


def main():
    assert ROOT.parent == Path('/opt/mini-drop-releases')
    out = ROOT / 'sampling-experiment'
    out.mkdir()
    api = inspect('mini-drop-control-apiserver-1')
    env = dict(v.split('=', 1) for v in api['Config']['Env'] if '=' in v)
    os.environ['SSL_CERT_FILE'] = next(m['Source'] for m in api['Mounts'] if m['Destination'] == '/certs') + '/ca.crt'
    client = Client('https://120.24.187.205', env['MINI_DROP_API_KEY'])
    path = '/api/v2/showcases/fault-plaza/source-hotspot'
    plaza = client.request('GET', '/api/v2/showcases/fault-plaza')
    assert not next(s for s in items_of(plaza['scenarios']) if s['scenario_id'] == 'source-hotspot')['active']
    result = {'kind': 'SAMPLER_SEMANTICS_EXPERIMENT', 'agent_root_cause_verified': False, 'modes': []}
    started = False
    try:
        started = True
        client.request('POST', path + '/start', {'duration_seconds': 90})
        pid = inspect('mini-drop-control-python-hotspot-1')['State']['Pid']
        agent = 'mini-drop-control-demo-agent-1'
        for mode, flags in [('default', []), ('gil', ['--gil'])]:
            remote = '/tmp/mini-drop-sampling-' + ROOT.name + '-' + mode + '.json'
            subprocess.run(['docker', 'exec', agent, 'py-spy', 'record', '--pid', str(pid),
                            '--rate', '99', '--duration', '8', '--format', 'speedscope',
                            '--nonblocking', '--output', remote] + flags, check=True, timeout=25)
            local = out / (mode + '.json')
            subprocess.run(['docker', 'cp', agent + ':' + remote, str(local)], check=True)
            data = json.loads(local.read_text())
            analysis = analyze_speedscope(data)
            result['modes'].append({'mode': mode, 'pid': pid, 'sample_count': analysis['sample_count'],
                                    'top': analysis['top'], 'threads': [p.get('name') for p in data['profiles']]})
    finally:
        if started:
            stopped = client.request('POST', path + '/stop')
            result['fault_stopped'] = not (stopped.get('scenario') or {}).get('active', True)
        (out / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
