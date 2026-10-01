"""Serial live engineering trials, with immutable records and independent cleanup.

Call run_campaign with an authenticated Client and a read-only lab snapshot
provider. Oracle snapshots stay outside diagnosis Evidence. No causal grading,
fixed round requirement or historic record rewriting is performed here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from urllib.parse import quote

from scripts.audit_fault_plaza_failures import evaluate_recorded_lineage
from scripts.evaluate_engineering_diagnosis import evaluate_engineering_case
from scripts.run_fault_plaza_closure_campaign import _find_demo_process, _write_json_atomic
from scripts.run_fault_plaza_strict_acceptance import collect_records, evaluate_intervention, measure, now
from scripts.verify_interview_demo import _get_plaza, _start_fault, _stop_fault, items_of

TERMINAL={'COMPLETED','INSUFFICIENT_EVIDENCE','FAILED','CANCELLED'}


def download_artifacts(client,records,directory):
    """Verify downloadable bytes independently of the persisted metadata."""
    verified=[]
    for task in records['tasks']:
        for artifact in task.get('artifacts',[]):
            task_id=task['task_id'];kind=artifact['artifact_type']
            if not all(isinstance(v,str) and v and '/' not in v and '\\' not in v for v in (task_id,kind)):
                raise ValueError('invalid artifact filename')
            raw=client.request_raw('GET','/api/tasks/'+quote(task_id,safe='')+'/artifacts/'+quote(kind,safe='')+'/download')
            digest=hashlib.sha256(raw).hexdigest()
            if digest!=artifact['sha256']:raise ValueError('downloaded artifact SHA mismatch')
            filename=task_id+'-'+kind+'.raw'
            with (directory/filename).open('xb') as stream:stream.write(raw)
            verified.append({'task_id':task_id,'artifact_id':artifact['id'],
                'artifact_type':kind,'file':filename,'sha256':digest})
    return verified


def run_case(client,scenario,provider,path,contract,*,agent_id,timeout_seconds=180):
    sid=scenario['scenario_id'];lab=scenario.get('lab_key') or 'python'
    row={'scenario_id':sid,'title':scenario['title'],'started_at':now(),
        'windows':{},'cleanup_verified':False,'session_drained':False,
        'acceptance_profile':'engineering-diagnosis.v1','fresh_live_run':True}
    started=False;did=None
    try:
        if not scenario.get('available') or scenario.get('active'):
            raise RuntimeError('scenario unavailable or already active')
        if any(s.get('active') for s in items_of(_get_plaza(client)['scenarios'])):
            raise RuntimeError('another fault is active; refusing overlap')
        row['windows']['baseline']=measure(provider,lab,4)
        started=True
        request=_start_fault(client,sid,300)['diagnosis_request']
        time.sleep(3);row['windows']['fault']=measure(provider,lab,4)
        expected=_find_demo_process(client,agent_id=agent_id,lab_key=lab)
        row['target']={'agent_id':agent_id,'pid':expected['pid'],'comm':expected['comm']}
        created=client.request('POST','/api/v2/diagnoses',{
            'query':request['query'],'auto_scope':True,'mode':'AUTONOMOUS',
            'target':request.get('target') or {},
            'budget':{'max_duration_seconds':timeout_seconds,'max_tool_calls':6,
                'min_diagnosis_rounds':1,'max_diagnosis_rounds':2,'max_hosts':1}})
        did=created['diagnosis_id'];row['diagnosis_id']=did
        _write_json_atomic(path,row)
        deadline=time.monotonic()+timeout_seconds+25
        while time.monotonic()<deadline:
            state=client.request('GET','/api/v2/diagnoses/'+did)
            if state['status'] in TERMINAL:break
            target=state.get('target') or {}
            if target.get('process_binding') and (target.get('agent_id')!=agent_id or target.get('pid')!=expected['pid']):
                raise RuntimeError('diagnosis selected a different target')
            time.sleep(2)
        else:raise TimeoutError('bounded diagnosis did not finish')
    except Exception as exc:
        row['error']=str(exc);row['error_type']=type(exc).__name__
    finally:
        if started:
            try:
                row['fault_before_stop']={'observed_at':now(),'snapshot':provider(lab)}
            except Exception as exc:row['pre_stop_observation_error']=str(exc)
            try:
                _stop_fault(client,sid);time.sleep(3)
                row['windows']['recovery']=measure(provider,lab,4)
                row['cleanup_verified']=not next(s for s in items_of(_get_plaza(client)['scenarios']) if s['scenario_id']==sid)['active']
            except Exception as exc:row['cleanup_error']=str(exc)
        if did:
            try:
                state=client.request('GET','/api/v2/diagnoses/'+did)
                if state['status'] not in TERMINAL:
                    client.request('POST','/api/v2/diagnoses/'+did+'/cancel',{'reason':'isolated engineering trial deadline or scope failure'})
                row['records']=collect_records(client,did)
                row['session_drained']=row['records']['diagnosis']['status'] in TERMINAL
                directory=path.parent/(sid+'-artifacts');directory.mkdir(exist_ok=False)
                row['downloads']=download_artifacts(client,row['records'],directory)
            except Exception as exc:row['records_error']=str(exc)
    row['intervention']=evaluate_intervention(sid,row['windows'])
    if 'records' in row:
        row['lineage_evaluation']=evaluate_recorded_lineage(row['records'],row.get('target'))
        row['engineering_evaluation']=evaluate_engineering_case(row,contract)
        # Actual byte download is mandatory for this new campaign.
        if not row.get('downloads') or row.get('records_error'):
            row['engineering_evaluation']['diagnosis_accepted']=False
            row['engineering_evaluation']['localization_accepted']=False
    row['finished_at']=now();_write_json_atomic(path,row)
    return row


def run_campaign(client,provider,output,*,scenario_ids,contracts,provenance,agent_id='control-interview-demo-agent'):
    output=Path(output);directory=output.parent/(output.stem+'-cases')
    if output.exists() or directory.exists():raise FileExistsError('choose a fresh campaign destination')
    scenarios=items_of(_get_plaza(client)['scenarios'])
    if any(s.get('active') for s in scenarios):raise RuntimeError('pre-existing fault; no trial started')
    selected=[s for s in scenarios if s['scenario_id'] in scenario_ids]
    if len(selected)!=len(scenario_ids) or len(set(scenario_ids))!=len(scenario_ids):raise ValueError('missing or duplicate scenario')
    selected.sort(key=lambda s:scenario_ids.index(s['scenario_id']))
    directory.mkdir(parents=True)
    report={'schema':'mini-drop.engineering-live-campaign.v1','started_at':now(),
        'run_status':'RUNNING','selected_count':len(selected),'results':[],
        'provenance':provenance,'causal_root_cause_verified':False,'same_load_fix_verified':False}
    _write_json_atomic(output,report)
    for scenario in selected:
        sid=scenario['scenario_id'];print(json.dumps({'event':'START','scenario_id':sid}),flush=True)
        row=run_case(client,scenario,provider,directory/(sid+'.json'),contracts[sid],agent_id=agent_id)
        evaluation=row.get('engineering_evaluation') or {}
        report['results'].append({'scenario_id':sid,'diagnosis_id':row.get('diagnosis_id'),
            'diagnosis_accepted':evaluation.get('diagnosis_accepted',False),
            'outcome':evaluation.get('outcome','INSUFFICIENT_EVIDENCE'),
            'cleanup_verified':row['cleanup_verified'],'session_drained':row['session_drained'],
            'case_path':str(directory/(sid+'.json'))})
        report['completed_count']=len(report['results']);report['diagnosis_accepted']=sum(r['diagnosis_accepted'] for r in report['results'])
        _write_json_atomic(output,report)
        print(json.dumps({'event':'FINISH',**report['results'][-1]},ensure_ascii=False),flush=True)
        if not row['cleanup_verified'] or not row['session_drained']:
            report['run_status']='STOPPED_UNSAFE_TO_CONTINUE';break
    else:report['run_status']='COMPLETED'
    report['finished_at']=now();_write_json_atomic(output,report)
    return report
