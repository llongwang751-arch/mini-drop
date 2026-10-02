from pathlib import Path
import hashlib
import importlib.util
import json
import sys
import time
from urllib.parse import quote

root=Path.cwd();sys.path.insert(0,str(root))
stage=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('p',root/'output/acceptance/deployment-20260930/run_strict.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
from scripts.verify_interview_demo import items_of
c=p.authenticated_client();c.proxy_mode='direct'
before=json.loads((stage/'runtime-before.json').read_text(encoding='utf-8'))
assert not any(x.get('active') for x in items_of(c.request('GET','/api/v2/showcases/fault-plaza')['scenarios']))

def run_check(name,parent=None,*,existing_id=None):
    output=stage/name;output.mkdir(exist_ok=True)
    created_path=output/('selected-existing-case.json' if existing_id else 'created.json')
    payload={'query':'检查当前状态','mode':'AUTONOMOUS','health_check':True}
    if parent:payload['follow_up_diagnosis_id']=parent['detail']['diagnosis_id']
    created=json.loads(created_path.read_text(encoding='utf-8')) if created_path.exists() else (
        c.request('GET','/api/v2/diagnoses/'+existing_id) if existing_id else c.request(
            'POST','/api/v2/services/agi-office-backend/diagnoses',payload))
    if not created_path.exists():created_path.write_text(json.dumps(created,ensure_ascii=False,indent=2),encoding='utf-8')
    did=created['diagnosis_id']
    print(json.dumps({'event':'CHECK_STARTED','diagnosis_id':did}),flush=True)
    deadline=time.monotonic()+160
    while time.monotonic()<deadline:
        detail=c.request('GET','/api/v2/diagnoses/'+did)
        if detail['status'] in {'COMPLETED','INSUFFICIENT_EVIDENCE','FAILED','CANCELLED'}:break
        time.sleep(2)
    else:raise RuntimeError('Check did not finish within its bounded deadline')
    records={'detail':detail}
    for endpoint in ('evidence','reports','tool-calls','events','hypotheses'):
        records[endpoint]=items_of(c.request('GET','/api/v2/diagnoses/'+did+'/'+endpoint))
    records_path=output/'records.json'
    records_path.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    result_events=[e for e in records['events'] if e['event_type']=='health_check.completed']
    assert len(result_events)==1 and not records['reports']
    result=result_events[0].get('payload',result_events[0].get('payload_json'))
    assert result['diagnosis_id']==did and result['schema']=='mini-drop.health-check.v1'
    assert result['causal_root_cause_verified'] is False
    assert result['code']=='NORMAL_OBSERVED', result
    assert detail['status']=='COMPLETED'
    assert len(records['tool-calls'])==1 and records['tool-calls'][0]['tool_name']=='collect_sys_metrics'
    tool=records['tool-calls'][0];assert tool['status']=='COMPLETED'
    assert all(h['status']!='SUPPORTED' for h in records['hypotheses'])
    hashes=[]
    artifacts=items_of(c.request('GET','/api/tasks/'+quote(tool['task_id'],safe='')+'/artifacts'))
    for artifact in artifacts:
        raw=c.request_raw('GET','/api/tasks/'+quote(tool['task_id'],safe='')+'/artifacts/'+quote(artifact['artifact_type'],safe='')+'/download')
        sha=hashlib.sha256(raw).hexdigest();assert sha==artifact['sha256']
        name=artifact['artifact_type'];assert '/' not in name and '\\' not in name
        (output/(name+'.raw')).write_bytes(raw)
        hashes.append({'artifact_id':artifact['id'],'artifact_type':name,'sha256':sha})
    assert len(hashes)>=2 and result['evidence_refs']
    for ref in result['evidence_refs']:
        ev=next(e for e in records['evidence'] if e['evidence_id']==ref)
        assert ev['envelope']['source']['artifact_sha256'] in {h['sha256'] for h in hashes}
    records['downloaded_artifact_hashes']=hashes;records['check_result']=result
    records_path.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    if parent:
        assert did!=parent['detail']['diagnosis_id']
        created_event=next(e for e in records['events'] if e['event_type']=='diagnosis.created')
        assert created_event['payload']['follow_up_diagnosis_id']==parent['detail']['diagnosis_id']
        current_binding=detail['target']['process_binding'];previous_binding=parent['detail']['target']['process_binding']
        assert current_binding['process_snapshot_id']!=previous_binding['process_snapshot_id']
        assert current_binding['snapshot_received_at']>previous_binding['snapshot_received_at']
        previous=c.request('GET','/api/v2/diagnoses/'+parent['detail']['diagnosis_id'])
        assert previous['status']=='COMPLETED' and previous['version']==parent['detail']['version']
    print(json.dumps({'event':'CHECK_FINISHED','diagnosis_id':did,'status':detail['status'],
        'code':result['code'],'download_sha_verified':len(hashes)}),flush=True)
    return records

if __name__=='__main__':
    first=run_check('health-live')
    (stage/'health-diagnosis-id.txt').write_text(first['detail']['diagnosis_id'])
    second=run_check('resample-live',first)
    (stage/'resample-diagnosis-id.txt').write_text(second['detail']['diagnosis_id'])
    (stage/'live-check-summary.json').write_text(json.dumps({
        'healthy_checks':2,'normal_checks':2,'root_reports':0,
        'first':first['detail']['diagnosis_id'],'resample':second['detail']['diagnosis_id'],
        'raw_downloads_sha_verified':len(first['downloaded_artifact_hashes'])+len(second['downloaded_artifact_hashes'])
    },indent=2),encoding='utf-8')
