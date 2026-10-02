"""Run actual local HTTP business requests; preserve every measurement and failure."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import threading
import time
from urllib.request import Request,urlopen
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from demo.rag_service.app import DATASET_SHA,QUESTIONS,KnowledgeService,Settings,serve
from server.app.drop_insight.business_acceptance import (
    Workload,RequestOutcome,MeasurementWindow,AcceptancePolicy,canonical_hash,compare_business_windows,
)

CASES={
    'RAG-01': {'title':'知识库重排工作量异常','stage':'rerank','background_import':False,
        'baseline':Settings(),'fault':Settings(rerank_candidates=384),'after':Settings(),
        'change':'将实际参与重排的候选上限由 384 调整为 4；请求集与检索质量标准不变。'},
    'RAG-02': {'title':'文档导入占用查询队列','stage':'queue','background_import':True,
        'baseline':Settings(),'fault':Settings(shared_ingest_queue=True),'after':Settings(shared_ingest_queue=False),
        'change':'把同样数量的文档导入任务移入独立执行队列，查询流量和导入批次均保持不变。'},
    'RAG-03': {'title':'生成依赖延迟与超时降级','stage':'generation','background_import':False,
        # The healthy baseline carries the same dependency constant as the
        # post-change timeout; otherwise the 1.3x recovery gate depends on
        # absolute machine speed and flips between DEGRADED_AVAILABLE/REJECTED.
        'baseline':Settings(dependency_latency_ms=10),
        'fault':Settings(dependency_latency_ms=120),
        'after':Settings(dependency_latency_ms=120,dependency_timeout_ms=10,allow_extractive_fallback=True),
        'change':'生成依赖正常固定耗时约 10ms，故障时慢至 120ms；处置后依赖仍然慢，保持 10ms 超时并返回本地摘录备用结果。只允许判为降级，不称完整恢复。'},
}


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.pending')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    os.replace(temp,path)


def measure(settings,workload,revision,background_import):
    service=KnowledgeService(settings);http=serve(service,port=0)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    endpoint=f'http://127.0.0.1:{http.server_port}/query'
    pool=ThreadPoolExecutor(max_workers=workload.concurrency_limit)
    stop=threading.Event();imports=[];import_thread=None
    def call(i):
        question,expected=QUESTIONS[i%len(QUESTIONS)]
        start=time.perf_counter()
        try:
            request=Request(endpoint,data=json.dumps({'question':question}).encode(),headers={'Content-Type':'application/json'},method='POST')
            with urlopen(request,timeout=30) as response:result=json.load(response)
            return RequestOutcome(request_id=f'q-{i}',latency_ms=(time.perf_counter()-start)*1000,
                success=result['success'],quality_passed=result['success'] and expected in result['citations'],
                degraded=result['degraded'],stage_ms=result['stage_ms'],trace_id=result['trace_id'])
        except Exception:
            return RequestOutcome(request_id=f'q-{i}',latency_ms=(time.perf_counter()-start)*1000,
                success=False,quality_passed=False,trace_id=uuid4().hex)
    try:
        for i in range(workload.warmup_requests):call(i)
        start=time.perf_counter();max_lag=0;futures=[]
        def import_work():
            # Fixed offered work; do not slow the producer because queries slow.
            for i in range(9):
                if stop.wait(max(0,start+i*.5-time.perf_counter())):break
                imports.append(service.ingest(i))
        if background_import:
            import_thread=threading.Thread(target=import_work);import_thread.start()
        dispatch=[]
        for i in range(workload.request_count):
            due=start+i/workload.arrival_rate
            time.sleep(max(0,due-time.perf_counter()))
            def dispatched(i=i,due=due):
                lag=max(0,(time.perf_counter()-due)*1000)
                result=call(i)
                # Client-side queueing is part of user latency, not hidden.
                result.latency_ms+=lag
                return result,lag
            futures.append(pool.submit(dispatched))
        pairs=[f.result(timeout=35) for f in futures]
        elapsed=time.perf_counter()-start
        if import_thread:import_thread.join(timeout=10)
        for task in imports:task.result(timeout=30)
        window=MeasurementWindow(window_id=uuid4().hex,workload=workload,revision=revision,
            config_sha256=canonical_hash(asdict(settings)),generator='EXTRACTIVE_LOCAL',
            elapsed_seconds=elapsed,max_dispatch_lag_ms=max(lag for _,lag in pairs),requests=[r for r,_ in pairs])
        return window,{'offered_import_batches':len(imports),'completed_import_documents':service.ingested_documents}
    finally:
        stop.set()
        if import_thread:import_thread.join(timeout=10)
        pool.shutdown(wait=True);http.shutdown();http.server_close();thread.join(timeout=5);service.close()


def aggregate_repeats(rows):
    """Aggregate independent repeat runs; every run is recorded, never a best-of selection."""
    if not rows:raise ValueError('no repeat rows to aggregate')
    def spread(values):
        ordered=sorted(values)
        return {'min':ordered[0],'mean':sum(ordered)/len(ordered),'max':ordered[-1],
            'stdev':statistics.stdev(ordered) if len(ordered)>1 else 0.0,'samples':len(ordered)}
    scenarios={}
    for sid in sorted({row['scenario_id'] for row in rows}):
        runs=sorted((row for row in rows if row['scenario_id']==sid),key=lambda row:row['run'])
        outcomes=[row['outcome'] for row in runs]
        scenarios[sid]={'runs':len(runs),'run_indexes':[row['run'] for row in runs],
            'outcomes':outcomes,'outcome_stable':len(set(outcomes))==1,
            'p95_ms':{label:spread([row['p95_ms'][label] for row in runs])
                      for label in ('baseline','fault','after')}}
    return {'schema':'mini-drop.business-repeat-summary.v1',
        'selection':'ALL_RUNS_RECORDED; NOT_BEST_OF','scenarios':scenarios,
        'all_outcome_stable':all(item['outcome_stable'] for item in scenarios.values())}


def run(output,case_ids=None,repeat=1):
    if not isinstance(repeat,int) or not 1<=repeat<=10:raise ValueError('repeat must be 1..10')
    if output.exists():raise ValueError('refusing to overwrite campaign evidence')
    policy=AcceptancePolicy()
    try:revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    except subprocess.SubprocessError:revision='unknown'
    source_files=['demo/rag_service/app.py','server/app/drop_insight/business_acceptance.py',
                  'scripts/run_business_acceptance.py','contracts/business_test_plan.json']
    sources={name:canonical_hash((ROOT/name).read_text(encoding='utf-8')) for name in source_files if (ROOT/name).exists()}
    revision=revision[:12]+'+'+canonical_hash(sources)[:12]
    report={'schema':'mini-drop.business-campaign.v1','run_id':uuid4().hex,'status':'RUNNING',
        'started_at':datetime.now(timezone.utc).isoformat(),'source_hashes':sources,'revision':revision,
        'policy':policy.model_dump(),'scope':'LOCAL_HTTP_EXTRACTIVE_FIXTURE; NO_LIVE_AI_DIAGNOSIS; NO_PRODUCTION_CLAIM',
        'repeat_runs':repeat,'results':[]}
    write_json(output,report)
    resource=canonical_hash({'platform':platform.platform(),'cpu_count':os.cpu_count()})
    repeat_rows=[]
    for run_index in range(1,repeat+1):
      for sid in (case_ids or CASES):
        case=CASES[sid];windows=[];ingestion=[]
        workload=Workload(dataset_sha256=DATASET_SHA,request_set_sha256=canonical_hash(QUESTIONS),
            arrival_rate=6,request_count=30,concurrency_limit=8,seed=0,warmup_requests=4,
            environment='local-http-fixture',service='knowledge-api',resources_sha256=resource)
        for label,settings in [('baseline',case.get('baseline',Settings())),('fault',case['fault']),('after',case['after'])]:
            print(json.dumps({'case':sid,'run':run_index,'window':label,'event':'START'}),flush=True)
            w,ingest=measure(settings,workload,revision,case['background_import']);windows.append(w);ingestion.append(ingest)
        comparison=compare_business_windows(*windows,policy,case['change'])
        if case['background_import'] and (len({x['completed_import_documents'] for x in ingestion})!=1 or ingestion[0]['completed_import_documents']==0):
            comparison['outcome']='INCOMPARABLE';comparison['reasons'].append('IMPORT_WORK_CHANGED')
        row={'scenario_id':sid,'title':case['title'],'comparison':comparison,'windows':[w.model_dump() for w in windows],
             'ingestion':ingestion,'expected_affected_stage':case['stage'],'ai_root_cause_verified':False}
        row['report_sha256']=canonical_hash(row)
        # Repeated runs keep every per-run case file; a later run never overwrites earlier evidence.
        suffix='' if repeat==1 else f'-r{run_index:02d}'
        case_path=output.parent/(output.stem+'-cases')/(sid+suffix+'.json');write_json(case_path,row)
        entry={'scenario_id':sid,'title':case['title'],'outcome':comparison['outcome'],
            'case_file':case_path.relative_to(output.parent).as_posix(),'report_sha256':row['report_sha256']}
        if repeat>1:
            entry['run']=run_index
            repeat_rows.append({'scenario_id':sid,'run':run_index,'outcome':comparison['outcome'],
                'p95_ms':{label:comparison['summaries'][label]['p95_ms'] for label in ('baseline','fault','after')}})
        report['results'].append(entry)
        write_json(output,report)
        print(json.dumps({'case':sid,'run':run_index,'event':'FINISH','outcome':comparison['outcome']}),flush=True)
    if repeat>1:report['repeat_summary']=aggregate_repeats(repeat_rows)
    report['status']='COMPLETED';report['finished_at']=datetime.now(timezone.utc).isoformat()
    report['report_sha256']=canonical_hash(report);write_json(output,report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case',choices=list(CASES),action='append')
    parser.add_argument('--repeat',type=int,default=1,help='Independent campaign repetitions; all runs are recorded (1..10)')
    parser.add_argument('--require-outcomes',action='store_true');args=parser.parse_args()
    result=run(args.output,args.case,args.repeat)
    if args.require_outcomes:
        plan=json.loads((ROOT/'contracts/business_test_plan.json').read_text(encoding='utf-8'))
        expected={c['id']:c['required_outcome'] for c in plan['cases']}
        if any(row['outcome']!=expected[row['scenario_id']] for row in result['results']):
            raise SystemExit('Business acceptance did not meet the versioned plan; evidence preserved')
    if result.get('repeat_summary'):
        print(json.dumps(result['repeat_summary'],ensure_ascii=False),flush=True)
