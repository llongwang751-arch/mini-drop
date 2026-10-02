"""Live cancellation and fresh business rehearsal; credentials remain in memory."""
import importlib.util, json, os, subprocess, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('provider', ROOT/'output/acceptance/deployment-20260930/run_strict.py')
provider=importlib.util.module_from_spec(spec);spec.loader.exec_module(provider)
from scripts.verify_interview_demo import items_of
client=provider.authenticated_client();client.proxy_mode='direct'

def save(name,value):
    (STAGE/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def req(method,path,body=None):
    return client.request(method,path,body,timeout=40)

owned=[]
try:
    first=req('POST','/api/v2/diagnoses',{'query':'验收：演示停止尚未开始采集的诊断', 'mode':'ASSISTED','auto_scope':False})
    owned.append(first['diagnosis_id'])
    env=dict(os.environ,MINI_DROP_API_KEY=client._key,MINI_DROP_BROWSER_BASE_URL=client.base)
    result=subprocess.run(['node',str(STAGE/'browser_cancel.mjs'),first['diagnosis_id']],env=env,cwd=ROOT,
                          capture_output=True,text=True,encoding='utf-8',timeout=70)
    (STAGE/'browser-cancel.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    assert result.returncode == 0, 'Browser cancellation failed'
    repeated=req('POST',f"/api/v2/diagnoses/{first['diagnosis_id']}/cancel",{'expected_version':first['version'],'reason':'重复取消请求'})
    events=req('GET',f"/api/v2/diagnoses/{first['diagnosis_id']}/events")
    assert repeated['status']=='CANCELLED'
    assert len([e for e in items_of(events) if e['event_type']=='diagnosis.cancelled'])==1
    save('cancel-browser-api.json',{'created':first,'repeated':repeated,'events':events})

    second=req('POST','/api/v2/services/agi-office-backend/diagnoses',
               {'query':'验收：采样运行中主动停止，保留已有记录', 'mode':'ASSISTED','health_check':True})
    diagnosis_id=second['diagnosis_id'];owned.append(diagnosis_id)
    target=second['target'];binding=target.get('process_binding') or {}
    agent_id=target.get('agent_id') or binding['agent_id'];pid=target.get('pid') or binding['pid']
    call=req('POST',f'/api/v2/diagnoses/{diagnosis_id}/tool-calls',
             {'tool_name':'collect_sys_metrics','arguments':{'agent_id':agent_id,'pid':pid,'duration_seconds':60}})
    if call['status']=='PENDING_APPROVAL':
        call=req('POST',f"/api/v2/diagnoses/{diagnosis_id}/tool-calls/{call['tool_call_id']}/decision",{'approved':True,'reason':'受控取消验收'})
    task_id=call['task_id']
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        task=req('GET',f'/api/tasks/{task_id}')
        if task['status']=='RUNNING':break
        assert task['status']=='PENDING',task['status']
        time.sleep(.4)
    assert task['status']=='RUNNING','Agent did not claim test task'
    current=req('GET',f'/api/v2/diagnoses/{diagnosis_id}')
    start=time.monotonic()
    cancelled=req('POST',f'/api/v2/diagnoses/{diagnosis_id}/cancel',{'reason':'运行中取消验收','expected_version':current['version']})
    assert cancelled['status']=='CANCELLED'
    # This Agent buffers stdout, so task_finished may not be visible yet.
    # A new task can only be claimed after the previous collector thread was
    # joined and worker_running was cleared. Prove that within 25 s, well
    # before the cancelled task's requested 60 s observation would end.
    follower=req('POST','/api/v2/services/agi-office-backend/diagnoses',
                 {'query':'验收：取消后 Agent 接受短采集', 'mode':'ASSISTED','health_check':True})
    owned.append(follower['diagnosis_id'])
    follower_target=follower['target']
    next_call=req('POST',f"/api/v2/diagnoses/{follower['diagnosis_id']}/tool-calls",
             {'tool_name':'collect_sys_metrics','arguments':{'agent_id':follower_target['agent_id'],
               'pid':follower_target['pid'],'duration_seconds':5}})
    next_task_id=next_call['task_id']
    while time.monotonic()-start<25:
        next_task=req('GET',f'/api/tasks/{next_task_id}')
        if next_task['status'] in {'RUNNING','UPLOADING','ANALYZING','DONE'}:break
        time.sleep(.4)
    assert next_task['status'] in {'RUNNING','UPLOADING','ANALYZING','DONE'},'Agent did not become available after cancellation'
    elapsed=time.monotonic()-start
    deadline=time.monotonic()+40
    while time.monotonic()<deadline:
        next_task=req('GET',f'/api/tasks/{next_task_id}')
        if next_task['status'] in {'DONE','FAILED','CANCELLED'}:break
        time.sleep(.5)
    assert next_task['status']=='DONE','Follow-up collection failed'
    agent_events={'method':'next_task_claim_requires_prior_collector_join', 'follow_up_task':next_task,
                  'cancel_to_next_claim_seconds':elapsed, 'original_duration_seconds':60}
    time.sleep(6)
    final=req('GET',f'/api/v2/diagnoses/{diagnosis_id}')
    tasks=req('GET',f'/api/tasks/{task_id}')
    assert final['status']=='CANCELLED' and tasks['status']=='CANCELLED'
    assert items_of(req('GET',f'/api/v2/diagnoses/{diagnosis_id}/reports'))==[]
    save('cancel-running-task.json',{'diagnosis_id':diagnosis_id,'task_id':task_id,'requested_duration_seconds':60,
         'observed_exit_seconds':elapsed,'agent_events':agent_events,'diagnosis':final,'task':tasks,
         'tool_calls':req('GET',f'/api/v2/diagnoses/{diagnosis_id}/tool-calls'),'passed':True})

    # Existing synthetic office document, fresh baseline/fault/recovery requests.
    business=json.loads(provider.remote((ROOT/'output/cloud-sre-exercise-20260923T141511Z/verify_business.py').read_text(encoding='utf-8')))
    save('fresh-business-three-phases.json',business)
    baseline=business['requests'][0]
    created=req('POST','/api/v2/services/agi-office-backend/diagnoses',
                {'query':'彩排：检查这次知识库问答请求的阶段与当前进程状态',
                 'request_id':baseline['request_id'],'health_check':True,'mode':'AUTONOMOUS'})
    diagnosis_id=created['diagnosis_id'];owned.append(diagnosis_id)
    save('rehearsal-created.json',created)
    deadline=time.monotonic()+160
    while time.monotonic()<deadline:
        detail=req('GET',f'/api/v2/diagnoses/{diagnosis_id}')
        if detail['status'] in {'COMPLETED','INSUFFICIENT_EVIDENCE','FAILED','CANCELLED'}:break
        time.sleep(2)
    calls=items_of(req('GET',f'/api/v2/diagnoses/{diagnosis_id}/tool-calls'))
    reports=items_of(req('GET',f'/api/v2/diagnoses/{diagnosis_id}/reports'))
    assert detail['status'] in {'COMPLETED','INSUFFICIENT_EVIDENCE'}
    assert calls and all(c['status']=='COMPLETED' for c in calls)
    assert reports,'No report produced'
    evidence=items_of(req('GET',f'/api/v2/diagnoses/{diagnosis_id}/evidence'))
    artifacts=[]
    for c in calls:
        artifacts.extend({**a, 'task_id':c['task_id']} for a in items_of(req('GET',f"/api/tasks/{c['task_id']}/artifacts")))
    verified=[]
    import hashlib
    for a in artifacts:
        if a.get('integrity_status')!='VERIFIED':continue
        raw=client.request_raw('GET',f"/api/tasks/{a['task_id']}/artifacts/{a['artifact_type']}/download")
        assert hashlib.sha256(raw).hexdigest()==a['sha256']
        verified.append({'artifact_type':a['artifact_type'],'task_id':a['task_id'],'bytes':len(raw),'sha256':a['sha256']})
    assert verified,'No verified artifacts downloaded'
    save('interview-rehearsal.json',{'passed':True,'request_id':baseline['request_id'],'diagnosis':detail,
       'tool_calls':calls,'reports':reports,'evidence':evidence,'verified_downloads':verified,
       'causal_root_cause_not_claimed':True})
    print(json.dumps({'cancellation':'PASS','physical_exit_seconds':elapsed,'business':'PASS',
                      'diagnosis_id':diagnosis_id,'status':detail['status'],'verified_artifacts':len(verified)}))
finally:
    for diagnosis_id in owned:
        current=req('GET',f'/api/v2/diagnoses/{diagnosis_id}')
        if current['status'] not in {'COMPLETED','INSUFFICIENT_EVIDENCE','FAILED','CANCELLED'}:
            req('POST',f'/api/v2/diagnoses/{diagnosis_id}/cancel',{'reason':'验收退出时取消本次测试诊断'})
