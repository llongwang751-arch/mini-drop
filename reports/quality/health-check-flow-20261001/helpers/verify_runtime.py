from pathlib import Path
import importlib.util
import json
import hashlib

stage=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('p',Path('output/acceptance/deployment-20260930/run_strict.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
m=json.loads((stage/'release/manifest.json').read_text(encoding='utf-8'))
before=json.loads((stage/'runtime-before.json').read_text(encoding='utf-8'))
web={n:sha for n,sha in m['deployment_files'].items() if n.startswith('web/dist/')}
code='''import json,subprocess
from pathlib import Path
files=%r
web=%r
before=%r
services={}
for service in ('diagnosis-worker','analyzer'):
    name='mini-drop-control-'+service+'-1'
    raw=subprocess.check_output(['docker','exec',name,'sha256sum',*['/app/'+n for n in files]]).decode().splitlines()
    actual={line.split()[1].removeprefix('/app/'):line.split()[0] for line in raw}
    assert actual==files
    obj=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
    services[service]={'files_verified':len(actual),'image':obj['Image'],'health':obj['State']['Health']['Status']}
raw=subprocess.check_output(['docker','exec','mini-drop-control-web-1','sha256sum',*['/usr/share/nginx/html/'+n.removeprefix('web/dist/') for n in web]]).decode().splitlines()
actual={'web/dist/'+line.split()[1].removeprefix('/usr/share/nginx/html/'):line.split()[0] for line in raw}
assert actual==web
objects=json.loads(subprocess.check_output(['docker','inspect',*subprocess.check_output(['docker','ps','-q']).decode().split()]))
assert len(objects)==13 and all(x['State'].get('Health',{}).get('Status','healthy')=='healthy' for x in objects)
changed={'/mini-drop-control-'+s+'-1' for s in ('diagnosis-worker','analyzer','web')}
for obj in objects:
    if obj['Name'] not in changed:assert obj['Id']==before['containers'][obj['Name']]['id'],obj['Name']
office_state=subprocess.check_output(['systemctl','show','agi-office-backend.service','--property=MainPID,NRestarts,ActiveState']).decode()
assert office_state==before['office_state']
assert str(Path('/opt/agi-office/current').resolve())==before['office_release']
api=subprocess.check_output(['docker','exec','mini-drop-control-apiserver-1','sha256sum','/usr/local/bin/mini-drop-apiserver']).decode().split()[0]
assert api=='ffb7bc11e5c7dbaa7165c717af90ecd726cd4cad6ebf1794f75602630128d2b3'
print(json.dumps({'services':services,'web_files_verified':len(web),'healthy_containers':13,'untouched_containers':10,
 'api_binary_sha256':api,'office_state':office_state,'office_release':before['office_release'],
 'platform_release':str(Path('/opt/mini-drop-current').resolve())}))
'''%(m['files'],web,before['runtime'])
result=json.loads(p.remote(code))
assert result['platform_release']=='/opt/mini-drop-releases/'+m['release_tag']
c=p.authenticated_client();c.proxy_mode='direct'
for name,sha in web.items():
    raw=c.request_raw('GET','/'+name.removeprefix('web/dist/'))
    assert hashlib.sha256(raw).hexdigest()==sha,name
from scripts.verify_interview_demo import items_of
health=c.request('GET','/api/healthz');assert all(v=='healthy' for v in health['dependencies'].values())
faults=items_of(c.request('GET','/api/v2/showcases/fault-plaza')['scenarios'])
assert len(faults)==21 and not any(x.get('active') for x in faults)
assert sum(x.get('latest_acceptance',{}).get('root_cause_accepted') is True for x in faults)==0
service=next(x for x in c.request('GET','/api/v2/services')['items'] if x['id']=='agi-office-backend')
assert service['business_requests'].get('invalid_records',0)==0
result.update({'source_head':m['git_head'],'web_source_kind':m['web_source']['source_kind'],
    'public_web_sha_verified':len(web),'inactive_faults':21,'legacy_strict_root_passes':0,
    'health':health,'invalid_business_records':0})
(stage/'final-runtime-verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
