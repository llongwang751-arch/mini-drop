import importlib.util
import json
from pathlib import Path

stage=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('provider',Path('output/acceptance/deployment-20260930/run_strict.py'))
provider=importlib.util.module_from_spec(spec);spec.loader.exec_module(provider)
source=Path('output/cloud-sre-exercise-20260923T141511Z/verify_business.py').read_text(encoding='utf-8')
source=source[:source.index('rows = []')]+'''
warm_status,warm_answer=post('/api/chat',{'message':QUESTION,'use_rag':True},token,'')
warm_id=warm_answer['trace_id'].replace('-','').lower()
warm_record=next(r for r in json.loads(OBSERVATIONS.read_text())['records'] if r['request_id']==warm_id)
warmup={**{k:warm_record.get(k) for k in ('request_id','duration_ms','stage_ms','retrieval_detail_ms','pid','version')},'http_status':warm_status,'answer_matches':warm_status==200 and not warm_answer.get('error') and '三个工作日' in warm_answer.get('answer','')}
rows=[]
for phase in ('baseline','fault','recovery'):
    status,answer=post('/api/chat',{'message':QUESTION,'use_rag':True},token,phase)
    request_id=answer['trace_id'].replace('-','').lower()
    records=json.loads(OBSERVATIONS.read_text())['records']
    row=next(r for r in records if r['request_id']==request_id)
    rows.append({**{k:row.get(k) for k in ('request_id','exercise_phase','injected_delay_ms','duration_ms','stage_ms','retrieval_detail_ms','retrieval_mode','result','pid','version')},'http_status':status,'answer_matches':status==200 and not answer.get('error') and '三个工作日' in answer.get('answer','')})
checks={'preparation_answer':warmup['answer_matches'],'answers':all(r['answer_matches'] for r in rows),'completed':all(r['result']=='COMPLETED' for r in rows),
        'injection':[r['injected_delay_ms'] for r in rows]==[0,2500,0],
        'same_process_version':len({(r['pid'],r['version']) for r in rows})==1,
        'semantic':all(r['retrieval_mode'] in {'semantic','hybrid'} and r['retrieval_detail_ms'].get('vector_search_ms',0)>0 for r in rows),
        'fault_vs_baseline':rows[1]['stage_ms']['retrieval_ms']>rows[0]['stage_ms']['retrieval_ms']+2000,
        'fault_vs_recovery':rows[1]['stage_ms']['retrieval_ms']>rows[2]['stage_ms']['retrieval_ms']+2000,
        'measured_sleep':rows[1]['retrieval_detail_ms'].get('fault_delay_ms',0)>=2499}
print(json.dumps({'passed':all(checks.values()),'checks':checks,'requests':rows,'preparation':warmup}))
'''
results=[]
# Fixed two chains, with one visible preparation each; keep every outcome.
for index in range(2):
    result=json.loads(provider.remote(source))
    target=stage/f'office-three-phase-cache-{index+1}.json'
    assert not target.exists()
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    results.append(result)
    print(json.dumps(result,ensure_ascii=False),flush=True)
raise SystemExit(0 if all(r['passed'] for r in results) else 1)
