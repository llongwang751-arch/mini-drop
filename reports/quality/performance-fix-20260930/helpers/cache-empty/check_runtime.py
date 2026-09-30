import importlib.util,json
from pathlib import Path
stage=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('p',Path('output/acceptance/deployment-20260930/run_strict.py'));p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
manifest=json.loads(Path('output/acceptance/performance-fix-20260930/cache/manifest.json').read_text());office=json.loads((stage/'office-deployment.json').read_text())
web={n.removeprefix('web/dist/'):sha for n,sha in manifest['deployment_files'].items() if n.startswith('web/dist/')}
code="""import json,subprocess,hashlib
from pathlib import Path
web=%r
office=%r
raw=subprocess.check_output(['docker','exec','mini-drop-control-web-1','sha256sum',*[('/usr/share/nginx/html/'+n) for n in web]]).decode().splitlines()
actual={line.split()[1].removeprefix('/usr/share/nginx/html/'):line.split()[0] for line in raw};assert actual==web
api=subprocess.check_output(['docker','exec','mini-drop-control-apiserver-1','sha256sum','/usr/local/bin/mini-drop-apiserver']).decode().split()[0]
assert api=='ffb7bc11e5c7dbaa7165c717af90ecd726cd4cad6ebf1794f75602630128d2b3'
assert str(Path('/opt/agi-office/current').resolve())==office['release']
for n,sha in office['integration_sha256'].items():assert hashlib.sha256((Path('/opt/agi-office/current')/n).read_bytes()).hexdigest()==sha
state=subprocess.check_output(['systemctl','show','agi-office-backend.service','--property=MainPID,NRestarts,ActiveState']).decode()
print(json.dumps({'web_files_verified':len(actual),'api_binary_sha256':api,'office':office['release'],'office_state':state,'platform_release':str(Path('/opt/mini-drop-current').resolve())}))
"""%(web,office)
result=json.loads(p.remote(code));assert result['platform_release']=='/opt/mini-drop-releases/20260930T144209Z'
c=p.authenticated_client();c.proxy_mode='direct'
health=c.request('GET','/api/healthz');assert all(x=='healthy' for x in health['dependencies'].values())
from scripts.verify_interview_demo import items_of
faults=items_of(c.request('GET','/api/v2/showcases/fault-plaza')['scenarios']);assert len(faults)==21 and not any(x.get('active') for x in faults)
service=next(x for x in c.request('GET','/api/v2/services')['items'] if x['id']=='agi-office-backend')
requests=service['business_requests'];assert requests['status']=='AVAILABLE' and requests.get('invalid_records',0)==0
expected=[]
for index in (1,2):
 row=json.loads((stage/f'office-three-phase-cache-{index}.json').read_text());expected.extend(r['request_id'] for r in row['requests']);expected.append(row['preparation']['request_id'])
ids={r['request_id'] for r in requests['items']};assert set(expected)<=ids
result.update({'health':health,'active_faults':0,'requests_ingested':len(expected),'invalid_records':requests.get('invalid_records',0)})
(stage/'runtime-verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
