from pathlib import Path
import importlib.util
import json
import sys
import time

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
stage=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('provider', ROOT/'output/acceptance/deployment-20260930/run_strict.py')
provider=importlib.util.module_from_spec(spec);spec.loader.exec_module(provider)
from scripts.verify_interview_demo import items_of
client=provider.authenticated_client();client.proxy_mode='direct'
output=stage/'health-live';output.mkdir(exist_ok=True)
plaza=client.request('GET','/api/v2/showcases/fault-plaza')
assert not any(row.get('active') for row in items_of(plaza.get('scenarios')))
created_path=output/'created.json'
created=json.loads(created_path.read_text(encoding='utf-8')) if created_path.exists() else client.request(
    'POST','/api/v2/services/agi-office-backend/diagnoses',
    {'query':'检查当前进程状态与本次资源观测窗口','mode':'AUTONOMOUS','health_check':True})
did=created['diagnosis_id']
if not created_path.exists():created_path.write_text(json.dumps(created,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'event':'HEALTH_STARTED','diagnosis_id':did}),flush=True)
deadline=time.monotonic()+150
while time.monotonic()<deadline:
    detail=client.request('GET','/api/v2/diagnoses/'+did)
    if detail['status'] in {'COMPLETED','INSUFFICIENT_EVIDENCE','FAILED','CANCELLED'}:break
    time.sleep(2)
else:raise RuntimeError('Health session exceeded its bounded deadline')
records={'detail':detail}
for endpoint in ('evidence','reports','tool-calls','events'):
    records[endpoint]=items_of(client.request('GET','/api/v2/diagnoses/'+did+'/'+endpoint))
artifact_hashes=[]
for tool in records['tool-calls']:
    task=tool.get('task_id')
    if not task:continue
    artifacts=items_of(client.request('GET',f'/api/tasks/{task}/artifacts'))
    for artifact in artifacts:
        import hashlib
        raw=client.request_raw('GET',f"/api/tasks/{task}/artifacts/{artifact['artifact_type']}/download")
        digest=hashlib.sha256(raw).hexdigest()
        assert digest==artifact['sha256']
        artifact_hashes.append({'task_id':task,'artifact_id':artifact['id'],'sha256':digest})
records['downloaded_artifact_hashes']=artifact_hashes
(output/'records.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
(stage/'health-diagnosis-id.txt').write_text(did,encoding='utf-8')
assert detail['status'] in {'COMPLETED','INSUFFICIENT_EVIDENCE'}
assert len(records['tool-calls'])==1 and records['tool-calls'][0]['tool_name']=='collect_sys_metrics'
assert records['tool-calls'][0]['status']=='COMPLETED'
print(json.dumps({'event':'HEALTH_FINISHED','diagnosis_id':did,'status':detail['status'],
    'tool_count':len(records['tool-calls']),'download_sha_verified':len(artifact_hashes)}))
