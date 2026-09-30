from pathlib import Path
import importlib.util
import json

stage=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('p',Path('output/acceptance/deployment-20260930/run_strict.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
backend=json.loads((stage/'cache/manifest.json').read_text(encoding='utf-8'))
web=json.loads((stage/'web-security/manifest.json').read_text(encoding='utf-8'))
office=json.loads((stage/'cache-empty/office-deployment.json').read_text(encoding='utf-8'))
code="""import json,subprocess,hashlib
from pathlib import Path
backend=%r
web=%r
office=%r
result={}
for service in ['diagnosis-worker','analyzer']:
    name='mini-drop-control-'+service+'-1'
    raw=subprocess.check_output(['docker','exec',name,'sha256sum',*['/app/'+n for n in backend]]).decode().splitlines()
    actual={line.split()[1].removeprefix('/app/'):line.split()[0] for line in raw}
    assert actual==backend
    obj=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
    result[service]={'files_verified':len(actual),'image':obj['Image'],'health':obj['State']['Health']['Status']}
raw=subprocess.check_output(['docker','exec','mini-drop-control-web-1','sha256sum',*['/usr/share/nginx/html/'+n.removeprefix('web/dist/') for n in web]]).decode().splitlines()
actual={'web/dist/'+line.split()[1].removeprefix('/usr/share/nginx/html/'):line.split()[0] for line in raw};assert actual==web
api=subprocess.check_output(['docker','exec','mini-drop-control-apiserver-1','sha256sum','/usr/local/bin/mini-drop-apiserver']).decode().split()[0]
assert api=='ffb7bc11e5c7dbaa7165c717af90ecd726cd4cad6ebf1794f75602630128d2b3'
assert str(Path('/opt/agi-office/current').resolve())==office['release']
for n,sha in office['integration_sha256'].items():assert hashlib.sha256((Path('/opt/agi-office/current')/n).read_bytes()).hexdigest()==sha
state=subprocess.check_output(['systemctl','show','agi-office-backend.service','--property=MainPID,NRestarts,ActiveState']).decode()
containers=json.loads(subprocess.check_output(['docker','inspect',*subprocess.check_output(['docker','ps','-q']).decode().split()]))
assert len(containers)==13
assert all(x['State']['Status']=='running' and x['State'].get('Health',{}).get('Status','healthy')=='healthy' for x in containers)
print(json.dumps({'services':result,'web_files_verified':len(actual),'api_binary_sha256':api,'office':office['release'],'office_state':state,'current_release':str(Path('/opt/mini-drop-current').resolve()),'healthy_containers':len(containers)}))
"""%(backend['files'],{n:sha for n,sha in web['files'].items() if n.startswith('web/dist/')},office)
result=json.loads(p.remote(code))
assert result['current_release']=='/opt/mini-drop-releases/'+web['release_tag']
c=p.authenticated_client();c.proxy_mode='direct'
health=c.request('GET','/api/healthz');assert all(x=='healthy' for x in health['dependencies'].values())
from scripts.verify_interview_demo import items_of
faults=items_of(c.request('GET','/api/v2/showcases/fault-plaza')['scenarios']);assert len(faults)==21 and not any(x.get('active') for x in faults)
service=next(x for x in c.request('GET','/api/v2/services')['items'] if x['id']=='agi-office-backend')
requests=service['business_requests'];assert requests['status']=='AVAILABLE' and requests.get('invalid_records',0)==0
result.update({'health':health,'active_faults':0,'invalid_records':requests.get('invalid_records',0),
               'worker_analyzer_source':backend['git_head'],'web_source':web['git_head'],
               'api_source':'f9b143ae3dfa4409ed621d34b5d2971e90c0a972','office_source':'f37f44e0f43ea58720da3638658a82c65430c595'})
(stage/'final-runtime-verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
