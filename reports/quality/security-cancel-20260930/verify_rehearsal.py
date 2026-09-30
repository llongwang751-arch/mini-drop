"""Fresh normal business -> request binding -> diagnosis -> immutable artifacts."""
import importlib.util,json,time,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];STAGE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('provider',ROOT/'output/acceptance/deployment-20260930/run_strict.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
from scripts.verify_interview_demo import items_of
c=m.authenticated_client();c.proxy_mode='direct'
req=lambda method,path,body=None:c.request(method,path,body,timeout=40)
source=(ROOT/'output/cloud-sre-exercise-20260923T141511Z/verify_business.py').read_text(encoding='utf-8')
source=source[:source.index('rows = []')]+'''
status,answer=post('/api/chat',{'message':QUESTION,'use_rag':True},token,'baseline')
assert status==200 and not answer.get('error')
assert '三个工作日' in answer.get('answer','')
request_id=answer['trace_id'].replace('-','').lower()
envelope=json.loads(OBSERVATIONS.read_text())
row=next(r for r in envelope['records'] if r['request_id']==request_id)
assert row['result']=='COMPLETED' and row['injected_delay_ms']==0
print(json.dumps({'result':'PASS_NORMAL_REQUEST','request':{k:row[k] for k in ('request_id','duration_ms','stage_ms','result','pid','version')},
                  'answer_matches_known_synthetic_document':True}))
'''
business=json.loads(m.remote(source))
(STAGE/'fresh-normal-business.json').write_text(json.dumps(business,ensure_ascii=False,indent=2),encoding='utf-8')
request_id=business['request']['request_id']
created=req('POST','/api/v2/services/agi-office-backend/diagnoses',{'query':'彩排：检查本次知识库问答请求的阶段与当前进程状态',
             'request_id':request_id,'health_check':True,'mode':'AUTONOMOUS'})
did=created['diagnosis_id']
(STAGE/'rehearsal-created.json').write_text(json.dumps(created,ensure_ascii=False,indent=2),encoding='utf-8')
try:
    deadline=time.monotonic()+160
    while time.monotonic()<deadline:
        detail=req('GET',f'/api/v2/diagnoses/{did}')
        if detail['status'] in {'COMPLETED','INSUFFICIENT_EVIDENCE','FAILED','CANCELLED'}:break
        time.sleep(2)
    calls=items_of(req('GET',f'/api/v2/diagnoses/{did}/tool-calls'))
    reports=items_of(req('GET',f'/api/v2/diagnoses/{did}/reports'))
    assert detail['status'] in {'COMPLETED','INSUFFICIENT_EVIDENCE'}
    assert calls and all(v['status']=='COMPLETED' for v in calls)
    assert reports
    evidence=items_of(req('GET',f'/api/v2/diagnoses/{did}/evidence'))
    artifacts=[]
    for call in calls:
        artifacts.extend({**a,'task_id':call['task_id']} for a in items_of(req('GET',f"/api/tasks/{call['task_id']}/artifacts")))
    downloads=[]
    for a in artifacts:
        if a.get('integrity_status')!='VERIFIED':continue
        raw=c.request_raw('GET',f"/api/tasks/{a['task_id']}/artifacts/{a['artifact_type']}/download")
        assert hashlib.sha256(raw).hexdigest()==a['sha256']
        downloads.append({'task_id':a['task_id'],'artifact_type':a['artifact_type'],'bytes':len(raw),'sha256':a['sha256']})
    assert downloads
    result={'passed_normal_chain':True,'request_id':request_id,'diagnosis':detail,'tool_calls':calls,
            'reports':reports,'evidence':evidence,'verified_downloads':downloads,
            'three_phase_latency_comparison_passed':False,'causal_root_cause_not_claimed':True}
    (STAGE/'interview-rehearsal.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'diagnosis_id':did,'normal_chain':'PASS','status':detail['status'],'verified_downloads':len(downloads)}))
finally:
    current=req('GET',f'/api/v2/diagnoses/{did}')
    if current['status'] not in {'COMPLETED','INSUFFICIENT_EVIDENCE','FAILED','CANCELLED'}:
        req('POST',f'/api/v2/diagnoses/{did}/cancel',{'reason':'彩排退出时取消本次诊断'})
