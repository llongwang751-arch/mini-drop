from pathlib import Path
import hashlib
import importlib.util
import json
import queue
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT));STAGE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('provider',ROOT/'output/acceptance/deployment-20260930/run_strict.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
from scripts.run_fault_plaza_strict_acceptance import collect_records
from scripts.run_engineering_diagnosis_acceptance import download_artifacts
target='ubuntu@106.52.176.128';remote='/home/ubuntu/mini-drop-business-fix-20261001T152600Z'
ssh=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15',target]
subprocess.run(ssh+['mkdir',remote],check=True,timeout=25)
sources={'before.py':ROOT/'output/acceptance/performance-fix-20260930/before-source/demo/rag_service/app.py',
    'after.py':ROOT/'demo/rag_service/app.py','fixture.py':ROOT/'scripts/same_load_rag_fix_fixture.py'}
for name,path in sources.items():
    subprocess.run(['scp','-q','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',str(path),target+':'+remote+'/'+name],check=True,timeout=45)
out=STAGE/'business-fix';out.mkdir(exist_ok=False)
(out/'source-manifest.json').write_text(json.dumps({name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in sources.items()},indent=2),encoding='utf-8')
proc=subprocess.Popen(ssh+['python3','-u',remote+'/fixture.py','--before',remote+'/before.py','--after',remote+'/after.py'],
    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
q=queue.Queue()
def reader():
    for line in proc.stdout:q.put(json.loads(line))
threading.Thread(target=reader,daemon=True).start()
c=p.authenticated_client();c.proxy_mode='direct';did=None
try:
    ready=q.get(timeout=30);assert ready['event']=='READY'
    (out/'ready.json').write_text(json.dumps(ready,indent=2),encoding='utf-8');pid=ready['identity']['pid']
    for label in ('baseline','before','after'):
        proc.stdin.write(json.dumps({'phase':label})+'\n');proc.stdin.flush()
        started=q.get(timeout=35);assert started['event']=='WINDOW_STARTED'
        (out/(label+'-started.json')).write_text(json.dumps(started,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'event':'BUSINESS_WINDOW_STARTED','phase':label,'pid':pid}),flush=True)
        if label=='before':
            trace=started['slow_request']['trace_id']
            diagnosis=c.request('POST','/api/v2/diagnoses',{'query':'Python rag-fix-case 知识检索查询耗时升高，重排阶段慢，请检查进程CPU和调用路径；实际慢请求 trace_id='+trace,
                'auto_scope':True,'mode':'AUTONOMOUS','target':{'agent_id':'tencent-cvm-worker-1','pid':pid,'trace_id':trace},
                'budget':{'max_duration_seconds':100,'max_tool_calls':3,'min_diagnosis_rounds':1,'max_diagnosis_rounds':1}})
            did=diagnosis['diagnosis_id'];(out/'diagnosis-created.json').write_text(json.dumps(diagnosis,ensure_ascii=False,indent=2),encoding='utf-8')
        finished=q.get(timeout=140);assert finished['event']=='WINDOW_COMPLETED'
        (out/(label+'.json')).write_text(json.dumps(finished['result'],ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'event':'BUSINESS_WINDOW_COMPLETED','phase':label,'requests':len(finished['result']['requests'])}),flush=True)
        if label=='before':
            deadline=time.monotonic()+35
            while time.monotonic()<deadline:
                state=c.request('GET','/api/v2/diagnoses/'+did)
                if state['status'] in {'COMPLETED','INSUFFICIENT_EVIDENCE','FAILED','CANCELLED'}:break
                time.sleep(2)
            records=collect_records(c,did)
            (out/'diagnosis-records.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
            artifacts=out/'artifacts';artifacts.mkdir()
            downloads=download_artifacts(c,records,artifacts)
            (out/'downloads.json').write_text(json.dumps(downloads,indent=2),encoding='utf-8')
finally:
    if proc.poll() is None:
        proc.stdin.close()
        try:proc.wait(timeout=15)
        except subprocess.TimeoutExpired:proc.terminate();proc.wait(timeout=10)
    (out/'fixture-exit.json').write_text(json.dumps({'exit_code':proc.returncode,'local_ssh_stopped':proc.poll() is not None}),encoding='utf-8')
    if did:
        state=c.request('GET','/api/v2/diagnoses/'+did)
        if state['status'] not in {'COMPLETED','INSUFFICIENT_EVIDENCE','FAILED','CANCELLED'}:
            c.request('POST','/api/v2/diagnoses/'+did+'/cancel',{'reason':'isolated business comparison ended'})
print('Business windows preserved; run independent comparison',flush=True)
