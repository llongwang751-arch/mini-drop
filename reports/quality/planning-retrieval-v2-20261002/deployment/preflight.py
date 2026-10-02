"""Read-only final deployment gates; copy complete CI provenance, never deploy."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import ssl
import sys


ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
RELEASE = STAGE / 'final-release-r3'
API_SHA = 'ffb7bc11e5c7dbaa7165c717af90ecd726cd4cad6ebf1794f75602630128d2b3'
SERVICES = ('diagnosis-worker', 'analyzer', 'web')


def load_helper(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def provider():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return load_helper('planning_retrieval_v2_provider', ROOT / 'output/acceptance/deployment-20260930/run_strict.py')


def tls_client(remote_provider):
    client = remote_provider.authenticated_client()
    client.proxy_mode = 'direct'
    assert client._context.verify_mode == ssl.CERT_REQUIRED and client._context.check_hostname
    return client


BASELINE_PROGRAM = r'''
import hashlib,json,shutil,subprocess
from datetime import datetime,timezone
from pathlib import Path

def output(words):return subprocess.check_output(words,text=True).strip()
def binary(service,path):return output(['docker','exec','mini-drop-control-'+service+'-1','sha256sum',path]).split()[0]
objects=json.loads(output(['docker','inspect',*output(['docker','ps','-q']).split()]))
assert len(objects)==13 and all(o['State']['Running'] and o['State'].get('Health',{}).get('Status','healthy')=='healthy' for o in objects)
containers={}
for o in objects:
 env=dict(v.split('=',1) for v in o['Config']['Env'] if '=' in v)
 containers[o['Name']]={'id':o['Id'],'pid':o['State']['Pid'],'image':o['Image'],
  'env_sha256':hashlib.sha256(json.dumps(env,sort_keys=True).encode()).hexdigest(),
  'mounts':o['Mounts'],'health':o['State'].get('Health',{}).get('Status','not_configured'),
  'running':o['State']['Running'], 'started_at':o['State']['StartedAt']}
cpp={s:binary(s,'/usr/local/bin/cpp-hotspot') for s in ('cpp-hotspot','diagnosis-worker','analyzer')}
assert len(set(cpp.values()))==1
native=binary('demo-agent','/usr/local/bin/mini-drop-native-agent')
api=binary('apiserver','/usr/local/bin/mini-drop-apiserver')
toolchain={s:output(['docker','exec','mini-drop-control-'+s+'-1','python','-c','import shutil; print(shutil.which("addr2line"))']) for s in ('diagnosis-worker','analyzer')}
assert all(v=='/usr/bin/addr2line' for v in toolchain.values())
agent_code=''' + '"""' + r'''
import json
from sqlalchemy import select
from server.app.database import new_session
from server.app.models import AgentModel,ProcessCandidateSnapshotModel,ProcessCandidateModel
with new_session() as s:
 a=s.get(AgentModel,'control-interview-demo-agent')
 snapshot=s.execute(select(ProcessCandidateSnapshotModel).where(ProcessCandidateSnapshotModel.agent_id=='control-interview-demo-agent').order_by(ProcessCandidateSnapshotModel.received_at.desc()).limit(1)).scalar_one_or_none()
 pids=[] if snapshot is None else list(s.scalars(select(ProcessCandidateModel.pid).where(ProcessCandidateModel.snapshot_id==snapshot.id)))
 print(json.dumps({'agent_id':'control-interview-demo-agent','heartbeat':None if a is None else a.last_heartbeat_at.isoformat(),'snapshot':None if snapshot is None else snapshot.received_at.isoformat(),'complete':False if snapshot is None else snapshot.complete,'pids':pids}))
''' + '"""' + r'''
receipt=json.loads(output(['docker','exec','mini-drop-control-diagnosis-worker-1','python','-c',agent_code]))
pids=[containers['/mini-drop-control-'+lab+'-hotspot-1']['pid'] for lab in ('python','java','cpp')]
assert receipt['complete'] is True and all(pid in receipt['pids'] for pid in pids)
now=datetime.now(timezone.utc)
started=datetime.fromisoformat(containers['/mini-drop-control-demo-agent-1']['started_at'].replace('Z','+00:00'))
for field in ('heartbeat','snapshot'):
 observed=datetime.fromisoformat(receipt[field])
 if observed.tzinfo is None:observed=observed.replace(tzinfo=timezone.utc)
 assert observed>=started and -5<=(now-observed).total_seconds()<=120
office=output(['systemctl','show','agi-office-backend.service','--property=MainPID,NRestarts,ActiveState'])
assert 'ActiveState=active' in office
print(json.dumps({'release':str(Path('/opt/mini-drop-current').resolve()),'containers':containers,
 'office_state':office,'office_release':str(Path('/opt/agi-office/current').resolve()),'free_bytes':shutil.disk_usage('/').free,
 'native_binary_sha256':native,'cpp_binary_sha256':cpp,'api_binary_sha256':api,'toolchain':toolchain,
 'agent_receipt':receipt,'current_demo_pids':pids,'observed_at':now.isoformat()}))
'''


def runtime_baseline(remote_provider):
    return json.loads(remote_provider.remote(BASELINE_PROGRAM))


def verify_public_baseline(client, require_retired=False):
    health = client.request('GET', '/api/healthz')
    assert health['dependencies'] and all(v == 'healthy' for v in health['dependencies'].values())
    faults = client.request('GET', '/api/v2/showcases/fault-plaza')['scenarios']
    assert len(faults) == 21 and not any(row.get('active') for row in faults)
    retired_fields = {'latest_acceptance', 'acceptance_level', 'root_cause_accepted', 'passed', 'fix_verified'}
    if require_retired:
        assert all(not retired_fields.intersection(row) for row in faults), 'retired causal grades remain in public fault API'
    office = next(row for row in client.request('GET', '/api/v2/services')['items'] if row['id'] == 'agi-office-backend')
    assert office['business_requests'].get('invalid_records', 0) == 0
    return {'health': health, 'inactive_faults': 21, 'retired_score_fields_checked': require_retired,
            'invalid_business_records': 0, 'tls_ca_verified': True, 'tls_hostname_verified': True}


def main():
    destination = STAGE / 'final-runtime-predeployment.json'
    assert not destination.exists(), 'predeployment baseline already exists; preserve it'
    manifest = json.loads((RELEASE / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['services'] == list(SERVICES)
    inventory = manifest['backend_inventory']
    assert inventory['file_count'] == len(manifest['files'])
    assert inventory['paths'] == sorted(manifest['files'])
    assert hashlib.sha256(('\n'.join(inventory['paths']) + '\n').encode()).hexdigest() == inventory['paths_sha256']
    assert manifest['engineering_summary'] == {'accepted': 21, 'localized': 6, 'refuted': 8, 'current_campaign': 7, 'historical': 14}
    directory = STAGE / 'ci' / manifest['git_head']
    run = json.loads((directory / 'run.json').read_text(encoding='utf-8'))
    jobs_doc = json.loads((directory / 'jobs.json').read_text(encoding='utf-8'))
    jobs = jobs_doc['jobs']
    assert run['name'] == 'CI' and run['status'] == 'completed' and run['conclusion'] == 'success'
    assert run['head_sha'] == manifest['git_head']
    assert jobs_doc['total_count'] == len(jobs) == 14 and len({j['id'] for j in jobs}) == 14
    assert all(j['status'] == 'completed' and j['conclusion'] == 'success'
               and j['head_sha'] == manifest['git_head'] and j['run_id'] == run['id'] for j in jobs)
    remote_provider = provider()
    baseline = runtime_baseline(remote_provider)
    assert baseline['free_bytes'] > 1428112200, "insufficient measured overlay capacity"
    assert baseline['api_binary_sha256'] == API_SHA
    assert baseline['native_binary_sha256'] == manifest['retained_native_binary_sha256']
    assert all('/mini-drop-control-' + service + '-1' in baseline['containers'] for service in SERVICES)
    baseline['public'] = verify_public_baseline(tls_client(remote_provider), require_retired=True)
    for name, target in (('run.json', 'ci-run.json'), ('jobs.json', 'ci-jobs.json')):
        assert not (RELEASE / target).exists(), 'CI receipt already exists'
        shutil.copy2(directory / name, RELEASE / target)
    manifest['ci_evidence'] = {'head_sha': run['head_sha'], 'run_id': run['id'], 'jobs_succeeded': 14,
        'run_sha256': hashlib.sha256((RELEASE / 'ci-run.json').read_bytes()).hexdigest(),
        'jobs_sha256': hashlib.sha256((RELEASE / 'ci-jobs.json').read_bytes()).hexdigest()}
    (RELEASE / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    prepare = load_helper('planning_retrieval_v2_prepare', STAGE / 'prepare_release.py')
    baseline.update(source_head=manifest['git_head'], release_tag=manifest['release_tag'],
                    ci_run=run['id'], ci_jobs_succeeded=14, changed_services=list(SERVICES),
                    bundle_sha256=prepare.write_bundle(), recorded_at=datetime.now(timezone.utc).isoformat())
    destination.write_text(json.dumps(baseline, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'ready': True, 'head': manifest['git_head'], 'ci_run': run['id'], 'ci_jobs_succeeded': 14,
                      'healthy_containers': 13, 'inactive_faults': 21, 'services': list(SERVICES),
                      'bundle_sha256': baseline['bundle_sha256']}))


if __name__ == '__main__':
    main()
