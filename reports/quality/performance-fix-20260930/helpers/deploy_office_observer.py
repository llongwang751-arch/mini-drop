"""Overlay one reviewed observer in a new release, with health-based rollback."""
import importlib.util
import json
from pathlib import Path
import hashlib

stage = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('provider',Path('output/acceptance/deployment-20260930/run_strict.py'))
provider = importlib.util.module_from_spec(spec);spec.loader.exec_module(provider)
client = provider.authenticated_client();client.proxy_mode='direct'
from scripts.verify_interview_demo import items_of
faults = items_of(client.request('GET','/api/v2/showcases/fault-plaza')['scenarios'])
assert not any(row.get('active') for row in faults)
cases = items_of(client.request('GET','/api/v2/diagnoses?limit=100'))
for row in cases:
    if row['status'] in {'COMPLETED','FAILED','CANCELLED','INSUFFICIENT_EVIDENCE'}:
        continue
    assert row['status']=='NEEDS_CLARIFICATION', 'Active diagnosis prevents office restart'
    calls=items_of(client.request('GET',f"/api/v2/diagnoses/{row['diagnosis_id']}/tool-calls"))
    assert all(call['status'] in {'COMPLETED','FAILED','CANCELLED','REJECTED'} for call in calls), 'Active collection prevents office restart'
manifest=json.loads((stage/'manifest.json').read_text())
name='integrations/agi_saber/request_observations.py'
raw=(stage/'deployment-source'/name).read_bytes()
sha=hashlib.sha256(raw).hexdigest()
assert sha==manifest['deployment_files'][name]
tag='observer-'+manifest['release_tag']
code='''from pathlib import Path
import json,subprocess,shutil,os,time,hashlib,urllib.request
tag=%r
source=%r
expected=%r
head=%r
old=Path('/opt/agi-office/current').resolve()
assert old==Path('/opt/agi-office/releases/native-20260924-e')
target=old.parent/tag
assert not target.exists()
before={n:hashlib.sha256((old/n).read_bytes()).hexdigest() for n in ('internal/rag/hybrid.py','internal/infra/infra.py','internal/repo/ragchunk.py')}
shutil.copytree(old,target,symlinks=True)
(target/'request_observations.py').write_bytes(source)
assert hashlib.sha256((target/'request_observations.py').read_bytes()).hexdigest()==expected
subprocess.run(['/opt/agi-office/venvs/native-20260924/bin/python','-m','py_compile',str(target/'request_observations.py')],check=True)
def switch(path):
    link=Path('/opt/agi-office')/('.switch-'+tag+'-'+str(time.time_ns()))
    link.symlink_to(path);os.replace(link,'/opt/agi-office/current')
def ready():
    for i in range(40):
        if subprocess.call(['systemctl','is-active','--quiet','agi-office-backend.service'])==0:
            try:
                with urllib.request.urlopen('http://172.17.0.1:18090/api/status',timeout=3) as r:
                    if r.status==200:return
            except urllib.error.HTTPError as e:
                if e.code==401:return
            except (OSError,TimeoutError):pass
        time.sleep(2)
    raise RuntimeError('office health failed')
try:
    switch(target);subprocess.run(['systemctl','restart','agi-office-backend.service'],check=True);ready()
except BaseException:
    switch(old);subprocess.run(['systemctl','restart','agi-office-backend.service'],check=True);ready();raise
assert before=={n:hashlib.sha256((target/n).read_bytes()).hexdigest() for n in before}
result={'release':str(target),'previous_release':str(old),'source_head':head,'observer_sha256':expected,'status':'HEALTHY','rollback':'switch current to previous_release and restart agi-office-backend.service','business_sources_unchanged':before}
(target/'observer-deployment.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
'''%(tag,raw,sha,manifest['git_head'])
result=json.loads(provider.remote(code))
(stage/'office-deployment.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
