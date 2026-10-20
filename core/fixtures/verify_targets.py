#!/usr/bin/env python3
"""Run on the Ubuntu build host; only self-created toy targets are sampled."""
import hashlib,json,os,pathlib,subprocess,time
ROOT=pathlib.Path(__file__).resolve().parents[2]
OUTPUT=ROOT/'core/evidence/target-collected';OUTPUT.mkdir(parents=True,exist_ok=True)
PROBE=ROOT/'build/drop_runner_probe';CATALOG=ROOT/'contracts/task-kinds.json'
def command(args):return subprocess.check_output(list(map(str,args)),text=True).strip()
def identity(pid):
 ticks=int(pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19])
 return {'start_time_ticks':ticks,'expected_uid':10001,'check_uid':True}
def collect(name,kind,target,environment,success=True):
 directory=OUTPUT/name;directory.mkdir(exist_ok=True)
 request={'kind':kind,'parameters':{'target':target,'duration_seconds':1,'frequency_hz':10 if kind=='PROC_CPU' else 2}}
 request_file=directory/'request.json';request_file.write_text(json.dumps(request))
 result=subprocess.run([str(PROBE),str(CATALOG),str(request_file),str(directory)],env=environment,text=True,capture_output=True,timeout=30)
 if (result.returncode==0)!=success:raise RuntimeError(f'{name} unexpected result: {result.stderr}')
 report=json.loads(result.stdout if success else result.stderr)
 if success and kind=='RESOURCE_SNAPSHOT':
  raw=json.loads((directory/report['name']).read_text())
  if not raw.get('samples'):raise RuntimeError('Resource samples missing')
  if 'namespace' not in raw.get('network_scope',''):raise RuntimeError('Network scope must disclose namespace totals')
  if raw.get('dmesg',{}).get('status') not in ('ok','disabled','unavailable','permission_denied'):raise RuntimeError('Kernel log result lacks explicit policy/permission status')
  if not all(key in raw.get('collector_overhead',{}) for key in ('user_cpu_seconds','system_cpu_seconds','children_user_cpu_seconds','children_system_cpu_seconds')):raise RuntimeError('Collector interval overhead missing')
  last=raw['samples'][-1]
  if not all(key in last for key in ('collector_cpu_ticks','collector_rss_bytes','network_rx_bytes','network_tx_bytes')):raise RuntimeError('Network or collector process counters missing')
  expected=('context_switches','cpu_total_ticks') if target['type']=='host' else ('voluntary_context_switches','involuntary_context_switches','cpu_ticks')
  if not all(key in last for key in expected):raise RuntimeError('Target CPU/context switch counters missing')
  report['resource_fields_verified']=True
 report.update(case=name,expected_success=success,fixture_source='self-created toy only; actual host/container target validation')
 (directory/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
 return report
environment=dict(os.environ,ALLOW_HOST_PROCESSES='true',ALLOWED_TARGET_UIDS='10001',ALLOW_HOST_RESOURCES='true',DOCKER_EXECUTABLE='/usr/bin/docker',DOCKER_SHA256=hashlib.sha256(pathlib.Path('/usr/bin/docker').read_bytes()).hexdigest())
containers=[];host=None;reports=[]
try:
 host=subprocess.Popen(['/usr/bin/setpriv','--reuid=10001','--regid=10001','--clear-groups',str(ROOT/'build/drop_workload')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 time.sleep(.2)
 reports.append(collect('host_process','PROC_CPU',{'type':'process','pid':host.pid,**identity(host.pid)},environment))
 reports.append(collect('host_resources','RESOURCE_SNAPSHOT',{'type':'host'},environment))
 for index in range(2):
  name=f'drop-core-target-fixture-{os.getpid()}-{index}'
  cid=command(['docker','run','-d','--name',name,'--network','none','--user','10001:10001','--memory','64m','--cpus','.15','--entrypoint','/bin/bash','drop-core-full-tools-base:v2','-c','/usr/local/bin/drop_workload & wait'])
  containers.append(cid)
 cid=containers[0]
 environment.update(ALLOW_CONTAINERS='true',ALLOWED_CONTAINER_IDS=cid)
 rows=command(['docker','top',cid,'-eo','pid,comm']).splitlines()[1:]
 host_pid=next(int(row.split()[0]) for row in rows if row.split()[-1]=='drop_workload')
 status=pathlib.Path(f'/proc/{host_pid}/status').read_text().splitlines()
 inner=int(next(line for line in status if line.startswith('NSpid:')).split()[-1])
 target={'type':'container','runtime':'docker','container_id':cid,'container_pid':inner,**identity(host_pid)}
 report=collect('container_inner_pid','PROC_CPU',target,environment)
 if report['metadata']['target_pid']!=host_pid:raise RuntimeError('Runtime target did not resolve the actual inner process')
 if report['metadata']['target_namespace_pid']!=inner:raise RuntimeError('Runtime target did not preserve the active namespace PID')
 reports.append(report)
 reports.append(collect('container_resource','RESOURCE_SNAPSHOT',target,environment))
 reports.append(collect('container_not_whitelisted','PROC_CPU',dict(target,container_id=containers[1]),environment,False))
 reports.append(collect('container_wrong_uid','PROC_CPU',dict(target,expected_uid=0),environment,False))
 reports.append(collect('container_stale_pid','PROC_CPU',dict(target,start_time_ticks=target['start_time_ticks']+1),environment,False))
finally:
 if host:
  host.terminate()
  try:host.wait(timeout=5)
  except subprocess.TimeoutExpired:host.kill();host.wait()
 for container in containers:subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
(OUTPUT/'evidence.json').write_text(json.dumps(reports,indent=2)+'\n')
print(json.dumps([{'case':r['case'],'expected_success':r['expected_success'],'size':r.get('size')} for r in reports],indent=2))
