"""Verify frozen HTTP business windows, actual probe lineage and raw downloads."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.audit_fault_plaza_failures import evaluate_recorded_lineage


def evaluate(directory):
    directory=Path(directory)
    load=lambda name:json.loads((directory/name).read_text(encoding='utf-8'))
    windows={phase:load(phase+'.json') for phase in ('baseline','before','after')}
    def p95(values):return sorted(values)[math.ceil(.95*len(values))-1]
    metrics={phase:{'requests':len(w['requests']),
        'p95_ms':p95([r['latency_ms'] for r in w['requests']]),
        'rerank_p95_ms':p95([r['stage_ms']['rerank'] for r in w['requests'] if r['success']]),
        'successes':sum(r['success'] is True for r in w['requests']),
        'quality_passed':sum(r['quality_passed'] is True for r in w['requests']),
        'candidate_counts':sorted(set(r.get('candidate_count') for r in w['requests']))}
        for phase,w in windows.items()}
    before,after=windows['before'],windows['after']
    records=load('diagnosis-records.json');manifest=load('source-manifest.json');downloads=load('downloads.json')
    identity=before['identity'];binding=records['diagnosis']['target'].get('process_binding') or {}
    target={'agent_id':records['diagnosis']['target']['agent_id'],'pid':identity['pid']}
    profiles=[e['envelope']['observation']['metadata'] for e in records['evidence']
        if e['envelope']['source']['tool_name']=='pyspy' and e['envelope']['observation']['metadata'].get('top_functions')]
    checks={
        'all_slots_recorded':all(m['requests']==360 for m in metrics.values()),
        'same_workload':all(before[k]==after[k] for k in ('request_count','arrival_rate','concurrency_limit','dataset_sha256','question_sha256')),
        'same_actual_candidates':metrics['before']['candidate_counts']==metrics['after']['candidate_counts'],
        'same_lifetime':all(w['identity']==identity for w in windows.values()),
        'trusted_target_lifetime':binding.get('pid')==identity['pid'] and binding.get('boot_id')==identity['boot_id'] and binding.get('process_start_ticks')==identity['start_ticks'],
        'source_bytes':all(hashlib.sha256((directory/'source'/name).read_bytes()).hexdigest()==digest for name,digest in manifest.items()),
        'changed_source_matches':before['source_sha256']==manifest['before.py'] and after['source_sha256']==manifest['after.py'] and manifest['before.py']!=manifest['after.py'],
        'success_and_quality_preserved':all(m['successes']==m['quality_passed']==360 for m in metrics.values()),
        'latency_improved':metrics['after']['p95_ms']<metrics['before']['p95_ms']*.5,
        'rerank_improved':metrics['after']['rerank_p95_ms']<metrics['before']['rerank_p95_ms']*.5,
        'request_context_linked':load('before-started.json')['slow_request']['trace_id'] in records['diagnosis']['query'],
        'diagnosis_closed':records['diagnosis']['status'] in {'COMPLETED','INSUFFICIENT_EVIDENCE'},
        'recorded_probe_lineage':evaluate_recorded_lineage(records,target)['recorded_chain_consistent'],
        'probe_completed_for_target':bool(records['tasks']) and all(t['status']=='DONE' for t in records['tasks']) and all(t['arguments']['pid']==identity['pid'] for t in records['tool-calls']),
        'actual_similarity_samples':any(m.get('sample_count',0)>=50 and any(f.get('name')=='find_longest_match' and f.get('samples',0)>0 and 'difflib.py' in f.get('file','') for f in m.get('top_functions',[])) for m in profiles),
        'raw_downloads':bool(downloads) and all(hashlib.sha256((directory/'artifacts'/d['file']).read_bytes()).hexdigest()==d['sha256'] for d in downloads),
        'remote_cleanup':load('remote-cleanup.json').get('original_process_exited') is True,
    }
    return {'schema':'mini-drop.same-load-business-fix.v1','passed':all(checks.values()),
        'checks':checks,'metrics':metrics,'download_sha_verified':len(downloads),
        'diagnosis_id':records['diagnosis']['diagnosis_id'],'source_manifest':manifest,
        'scope':'ISOLATED_REAL_SQLITE_FTS5_HTTP_BUSINESS; EXTRACTIVE_LOCAL; NOT_OFFICE_OR_LIVE_LLM',
        'workflow':'HUMAN_HYPOTHESIS_PLATFORM_PROBE_ENGINEERING_FIX_IDENTICAL_LOAD_RETEST',
        'causal_root_cause_verified_by_agent':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    args=parser.parse_args();result=evaluate(args.directory)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
