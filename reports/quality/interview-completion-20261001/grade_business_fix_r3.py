from pathlib import Path
import hashlib
import importlib.util
import json
import math
import subprocess

ROOT=Path(__file__).resolve().parents[3];STAGE=Path(__file__).resolve().parent;out=STAGE/'business-fix-r3'
def p95(values):return sorted(values)[math.ceil(.95*len(values))-1]
windows={label:json.loads((out/(label+'.json')).read_text(encoding='utf-8')) for label in ('baseline','before','after')}
metrics={label:{'requests':len(w['requests']),'p95_ms':p95([r['latency_ms'] for r in w['requests']]),
    'rerank_p95_ms':p95([r['stage_ms']['rerank'] for r in w['requests'] if r['success']]),
    'successes':sum(r['success'] for r in w['requests']),'quality_passed':sum(r['quality_passed'] for r in w['requests']),
    'dispatch_p95_ms':p95([r['dispatch_lag_ms'] for r in w['requests']]),
    'candidate_counts':sorted(set(r.get('candidate_count') for r in w['requests']))} for label,w in windows.items()}
before,after=windows['before'],windows['after']
records=json.loads((out/'diagnosis-records.json').read_text(encoding='utf-8'))
binding=records['diagnosis']['target'].get('process_binding') or {}
manifest=json.loads((out/'source-manifest.json').read_text(encoding='utf-8'))
downloads=json.loads((out/'downloads.json').read_text(encoding='utf-8'))
checks={'all_slots_recorded':all(m['requests']==360 for m in metrics.values()),
    'same_workload':all(before[k]==after[k] for k in ('request_count','arrival_rate','concurrency_limit','dataset_sha256','question_sha256')),
    'same_actual_candidates':metrics['before']['candidate_counts']==metrics['after']['candidate_counts'],
    'same_lifetime':all(w['identity']==before['identity'] for w in windows.values()),
    'target_lifetime_matches':binding.get('pid')==before['identity']['pid'] and binding.get('boot_id')==before['identity']['boot_id']
        and binding.get('process_start_ticks')==before['identity']['start_ticks'],
    'changed_source_matches':before['source_sha256']==manifest['before.py'] and after['source_sha256']==manifest['after.py']
        and manifest['before.py']!=manifest['after.py'],
    'success_and_quality_preserved':all(m['successes']==m['quality_passed']==360 for m in metrics.values()),
    'user_latency_improved':metrics['after']['p95_ms']<metrics['before']['p95_ms']*.5,
    'rerank_improved':metrics['after']['rerank_p95_ms']<metrics['before']['rerank_p95_ms']*.5,
    'slow_request_linked':json.loads((out/'before-started.json').read_text(encoding='utf-8'))['slow_request']['trace_id'] in records['diagnosis']['query'],
    'diagnosis_closed':records['diagnosis']['status'] in {'COMPLETED','INSUFFICIENT_EVIDENCE'},
    'raw_downloads':bool(downloads) and all(hashlib.sha256((out/'artifacts'/d['file']).read_bytes()).hexdigest()==d['sha256'] for d in downloads)}
ssh=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','ubuntu@106.52.176.128']
pid=before['identity']['pid'];remote=subprocess.run(ssh+['python3','-'],input='from pathlib import Path\nimport json\nprint(json.dumps({"original_process_exited":not Path("/proc/'+str(pid)+'").exists()}))\n',
    text=True,capture_output=True,encoding='utf-8',timeout=25,check=True)
cleanup=json.loads(remote.stdout);checks['remote_cleanup']=cleanup['original_process_exited']
import sys
sys.path.insert(0,str(ROOT))
from scripts.audit_fault_plaza_failures import evaluate_recorded_lineage
lineage=evaluate_recorded_lineage(records,{'agent_id':'tencent-cvm-worker-1','pid':pid})
checks['recorded_probe_lineage']=lineage['recorded_chain_consistent']
profiles=[e['envelope']['observation']['metadata'] for e in records['evidence'] if e['envelope']['source']['tool_name']=='pyspy' and e['envelope']['observation']['metadata'].get('top_functions')]
checks['actual_similarity_samples']=any(m.get('sample_count',0)>=50 and any(f.get('name')=='find_longest_match' and f.get('samples',0)>0 and 'difflib.py' in f.get('file','') for f in m.get('top_functions',[])) for m in profiles)
checks['same_phase_probe']=bool(records['tasks']) and all(t['status']=='DONE' for t in records['tasks']) and all(t['arguments']['pid']==pid for t in records['tool-calls'])
result={'schema':'mini-drop.same-load-business-fix.v1','passed':all(checks.values()),'checks':checks,'metrics':metrics,
    'source_manifest':manifest,'diagnosis_id':records['diagnosis']['diagnosis_id'],
    'reports':[{'id':r['report_id'],'conclusion':r['conclusion'],'verification_status':r['verification'].get('status')} for r in records['reports']],
    'download_sha_verified':len(downloads),'scope':'ISOLATED_REAL_SQLITE_FTS5_HTTP_BUSINESS; EXTRACTIVE_LOCAL; NOT_OFFICE_OR_LIVE_LLM',
    'change':'same 96 returned candidates and same 4RPS/360 requests; frozen uncached code -> bounded lexical score cache',
    'causal_root_cause_verified_by_agent':False}
(out/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
