#!/usr/bin/env python3
"""Real RunnerRegistry collection against independent toy business PIDs.

Run in full-tools, no network or host PID namespace; explicitly grant fixture
SYS_PTRACE/PERFMON and allow their syscalls. Nothing accesses production PIDs.
"""
import hashlib,json,os,pathlib,subprocess,time
ROOT=pathlib.Path('/src')
OUTPUT=ROOT/'core/evidence/language-collected'
OUTPUT.mkdir(parents=True,exist_ok=True)
PERF=next(pathlib.Path('/usr/lib/linux-tools').glob('*/perf')).resolve()
environment=dict(os.environ,ALLOW_HOST_PROCESSES='true',ALLOWED_TARGET_UIDS=str(os.getuid()),PERF_EXECUTABLE=str(PERF),PERF_SHA256=hashlib.sha256(PERF.read_bytes()).hexdigest())
subprocess.run(['/usr/bin/go','build','-o','/tmp/drop-go-fixture',str(ROOT/'core/fixtures/go_workload.go')],check=True,env=dict(os.environ,GOCACHE='/tmp/drop-go-cache'),timeout=120)
subprocess.run(['/usr/bin/javac','-d','/tmp',str(ROOT/'core/fixtures/JavaWorkload.java')],check=True,timeout=30)
targets={
 'go':subprocess.Popen(['/tmp/drop-go-fixture'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL),
 'java':subprocess.Popen(['/usr/bin/java','-Xmx48m','-Xms16m','-cp','/tmp','JavaWorkload'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL),
 'python':subprocess.Popen(['/opt/drop-tools/venv/bin/python',str(ROOT/'core/fixtures/python_workload.py')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL),
 'native':subprocess.Popen([str(ROOT/'build/drop_workload')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)}
results=[]
try:
 time.sleep(2)
 cases=[('GO_CPU','go',{'port':6060}),('GO_HEAP','go',{'port':6060,'sample_type':'inuse_space'}),('JAVA_CPU','java',{'event':'cpu'}),('JAVA_HEAP','java',{'live':True}),('PYTHON_CPU','python',{}),('PYTHON_MEMORY','python',{}),('PROC_CPU','native',{}),('PERF_CPU','native',{}),('RESOURCE_SNAPSHOT','native',{}),('SCRIPT','native',{'template_id':'process_status'})]
 for kind,target,options in cases:
  process=targets[target]
  pid=process.pid
  ticks=int(pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19])
  directory=OUTPUT/kind.lower();directory.mkdir(exist_ok=True)
  request={'kind':kind,'parameters':{'pid':pid,'duration_seconds':2,'frequency_hz':10 if kind in ('PROC_CPU','RESOURCE_SNAPSHOT') else 99,'target':{'type':'process','pid':pid,'start_time_ticks':ticks,'expected_uid':os.getuid(),'check_uid':True},'options':options}}
  request_path=directory/'request.json';request_path.write_text(json.dumps(request))
  result=subprocess.run([str(ROOT/'build/drop_runner_probe'),str(ROOT/'contracts/task-kinds.json'),str(request_path),str(directory)],env=environment,capture_output=True,text=True,timeout=75)
  if result.returncode:
   report={'kind':kind,'success':False,'error':result.stderr[-3000:]}
   log=directory/'runner.log'
   if log.exists():report['tool_diagnostic']=log.read_text(errors='replace')[-3000:]
  else:
   report=json.loads(result.stdout);report['success']=True;report['fixture_source']='real toy business PID through production RunnerRegistry; not production data'
  (directory/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');results.append(report)
  print(json.dumps({k:report[k] for k in ('kind','success','size','error') if k in report}),flush=True)
finally:
 for target in targets.values():
  target.terminate()
 for target in targets.values():
  try:target.wait(timeout=5)
  except subprocess.TimeoutExpired:target.kill();target.wait()
(OUTPUT/'evidence.json').write_text(json.dumps(results,indent=2)+'\n')
if not all(item['success'] for item in results):raise SystemExit(1)
