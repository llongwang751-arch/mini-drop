"""Live bounded checks on the isolated Go demo, retaining every failed trial."""
from pathlib import Path
import hashlib
import importlib.util
import json
import sys
import time
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
STAGE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('provider',ROOT/'output/acceptance/deployment-20260930/run_strict.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
from scripts.verify_interview_demo import items_of, _start_fault, _stop_fault
from scripts.run_fault_plaza_closure_campaign import _find_demo_process
from scripts.run_fault_plaza_strict_acceptance import collect_records
c=p.authenticated_client();c.proxy_mode='direct'
TERMINAL={'COMPLETED','INSUFFICIENT_EVIDENCE','FAILED','CANCELLED'}

def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def check(name,expected,interrupt=False):
    out=STAGE/name;out.mkdir(exist_ok=False)
    proc=_find_demo_process(c,agent_id='control-interview-demo-agent',lab_key='go')
    payload={'query':'检查 Go go-hotspot 当前状态','health_check':True,'auto_scope':True,
        'mode':'AUTONOMOUS','target':{'agent_id':'control-interview-demo-agent','pid':proc['pid']},
        'budget':{'max_duration_seconds':120,'max_tool_calls':1,'max_diagnosis_rounds':1}}
    created=c.request('POST','/api/v2/diagnoses',payload);save(out/'created.json',created)
    did=created['diagnosis_id'];print(json.dumps({'event':'HEALTH_STARTED','name':name,'id':did}),flush=True)
    deadline=time.monotonic()+145;cancelled=False
    while time.monotonic()<deadline:
        state=c.request('GET','/api/v2/diagnoses/'+did)
        if interrupt and not cancelled:
            calls=items_of(c.request('GET','/api/v2/diagnoses/'+did+'/tool-calls'))
            for call in calls:
                if call.get('task_id') and call['status'] in {'TASK_CREATED','RUNNING'}:
                    response=c.request('POST','/api/tasks/'+quote(call['task_id'],safe='')+'/cancel',{'reason':'isolated acceptance: interrupt only this check'})
                    save(out/'task-cancel.json',response);cancelled=True;break
        if state['status'] in TERMINAL:break
        time.sleep(1)
    records=collect_records(c,did);save(out/'records.json',records)
    downloads=[]
    for task in records['tasks']:
        for artifact in task.get('artifacts',[]):
            raw=c.request_raw('GET','/api/tasks/'+quote(task['task_id'],safe='')+'/artifacts/'+quote(artifact['artifact_type'],safe='')+'/download')
            sha=hashlib.sha256(raw).hexdigest();assert sha==artifact['sha256']
            filename=task['task_id']+'-'+artifact['artifact_type']+'.raw'
            (out/filename).write_bytes(raw);downloads.append({'file':filename,'sha256':sha})
    save(out/'downloads.json',downloads)
    events=[e for e in records['events'] if e['event_type']=='health_check.completed']
    assert len(events)==1, {'status':records['diagnosis']['status'],'events':len(events)}
    result=events[0]['payload'];assert result['code']==expected,result
    assert not records['reports'] and result['causal_root_cause_verified'] is False
    assert records['diagnosis']['target']['pid']==proc['pid']
    assert records['diagnosis']['status']==('INSUFFICIENT_EVIDENCE' if interrupt else 'COMPLETED')
    if interrupt:assert cancelled
    save(out/'verified.json',{'result':result,'download_sha_verified':len(downloads),'interrupted':cancelled})
    print(json.dumps({'event':'HEALTH_VERIFIED','name':name,'code':result['code'],'downloads':len(downloads)}),flush=True)
    return records

if __name__=='__main__':
    check('go-normal','NORMAL_OBSERVED')
    started=False
    try:
        started=True;start=_start_fault(c,'go-cpu-hotspot',180);save(STAGE/'health-fault-start.json',start)
        time.sleep(3);check('go-anomaly','ANOMALY_OBSERVED')
    finally:
        if started:_stop_fault(c,'go-cpu-hotspot')
    time.sleep(3);check('go-recovery','NORMAL_OBSERVED')
    check('go-interrupted','INSUFFICIENT_OBSERVABILITY',interrupt=True)
    check('go-resampled','NORMAL_OBSERVED')
