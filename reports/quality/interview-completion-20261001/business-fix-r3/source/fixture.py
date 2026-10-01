"""Disposable real HTTP business fixture, controlled only through stdin.

    python same_load_rag_fix_fixture.py --before frozen.py --after fixed.py

Each JSON line requests one baseline/before/after window. Before and after use
identical corpus, question, arrival slots and candidate count. The change is
the existing bounded lexical-score cache, not withdrawal of offered load.
This is a local extractive business sample, not the original office or an LLM.
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
import ctypes
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time
from urllib.request import Request,urlopen


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return module


def emit(value):print(json.dumps(value,ensure_ascii=False),flush=True)


def identity():
    fields=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()
    return {'pid':os.getpid(),'start_ticks':int(fields[19]),
        'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def measure(module,label,path,*,requests=360,rate=4):
    settings=module.Settings(rerank_candidates=4 if label=='baseline' else 384,
        **({'cache_rerank':True} if label=='after' else {}))
    service=module.KnowledgeService(settings);server=module.serve(service,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    endpoint=f'http://127.0.0.1:{server.server_port}/query'
    rows=[];question='annual leave policy'
    def call(index,due):
        start=time.monotonic();lag=max(0,start-due)
        try:
            payload=json.dumps({'question':question}).encode()
            with urlopen(Request(endpoint,data=payload,headers={'Content-Type':'application/json'},method='POST'),timeout=30) as response:
                result=json.load(response)
            return {'slot':index,'success':result['success'],'quality_passed':result['success'] and result['citations']==['leave']
                and 'Manager approval is required' in result.get('answer',''),'trace_id':result['trace_id'],
                'candidate_count':result['candidate_count'],'stage_ms':result['stage_ms'],
                'latency_ms':(time.monotonic()-due)*1000,'dispatch_lag_ms':lag*1000,'response':result}
        except Exception as exc:return {'slot':index,'success':False,'quality_passed':False,'latency_ms':(time.monotonic()-due)*1000,
            'dispatch_lag_ms':lag*1000,'error_type':type(exc).__name__}
    source=hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        warmup=[call(-i-1,time.monotonic()) for i in range(4)]
        emit({'event':'WINDOW_STARTED','phase':label,'identity':identity(),'source_sha256':source,
            'settings':asdict(settings),'request_count':requests,'arrival_rate':rate,
            'question_sha256':hashlib.sha256(question.encode()).hexdigest(),'dataset_sha256':module.DATASET_SHA,
            'slow_request':warmup[-1]})
        start=time.monotonic()
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures=[]
            for index in range(requests):
                due=start+index/rate;time.sleep(max(0,due-time.monotonic()))
                futures.append(pool.submit(call,index,due))
            rows=[f.result(timeout=35) for f in futures]
        result={'phase':label,'identity':identity(),'source_sha256':source,'settings':asdict(settings),
            'request_count':requests,'arrival_rate':rate,'concurrency_limit':8,'warmup':warmup,'requests':rows,
            'dataset_sha256':module.DATASET_SHA,'question_sha256':hashlib.sha256(question.encode()).hexdigest(),
            'elapsed_seconds':time.monotonic()-start}
        emit({'event':'WINDOW_COMPLETED','result':result})
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5);service.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before',type=Path,required=True);parser.add_argument('--after',type=Path,required=True)
    args=parser.parse_args()
    # Only this new fixture's comm changes; the service is never registered persistently.
    ctypes.CDLL(None).prctl(15,b'rag-fix-case',0,0,0)
    before=load('rag_before',args.before);after=load('rag_after',args.after)
    threading.Timer(600,lambda:os._exit(74)).start()
    emit({'event':'READY','identity':identity(),'scope':'ISOLATED_EXTRACTIVE_HTTP_BUSINESS; NO_LIVE_LLM'})
    try:
        for line in sys.stdin:
            command=json.loads(line)
            if command['phase']=='stop':break
            label=command['phase']
            if label not in {'baseline','before','after'}:raise ValueError('unknown phase')
            measure(after if label=='after' else before,label,args.after if label=='after' else args.before)
    finally:os._exit(0)


if __name__=='__main__':main()
