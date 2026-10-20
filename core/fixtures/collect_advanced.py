#!/usr/bin/env python3
"""在当前 PID 命名空间内，对合成业务进程执行真实且有界的 BOLT/BPF 采集。

产物用于受控采集验收，不代表生产业务的性能或采样环境。
"""
import hashlib,json,os,pathlib,platform,re,resource,subprocess,tempfile,time
ROOT=pathlib.Path('/src')
run_id=os.environ.get('DROP_CORE_EVIDENCE_RUN_ID','')
if run_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',run_id):raise RuntimeError('Invalid fixture run ID')
OUTPUT=ROOT/'core/evidence'/('advanced-collected'+('-'+run_id if run_id else ''));OUTPUT.mkdir(parents=True,exist_ok=True)
if any(OUTPUT.iterdir()):raise RuntimeError('Advanced fixture requires a new evidence directory to prevent stale diagnostics')
def optional_text(name):
 try:return pathlib.Path(name).read_text().strip()[:4096]
 except OSError:return None
status={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line}
proof={'kernel':platform.release(),'architecture':platform.machine(),'effective_capabilities_hex':status.get('CapEff'),'bounding_capabilities_hex':status.get('CapBnd'),'seccomp':status.get('Seccomp'),'no_new_privs':status.get('NoNewPrivs'),'memlock':resource.getrlimit(resource.RLIMIT_MEMLOCK),'perf_event_paranoid':optional_text('/proc/sys/kernel/perf_event_paranoid'),'kptr_restrict':optional_text('/proc/sys/kernel/kptr_restrict'),'lockdown':optional_text('/sys/kernel/security/lockdown'),'kernel_btf_available':pathlib.Path('/sys/kernel/btf/vmlinux').is_file(),'tracefs_readable':pathlib.Path('/sys/kernel/tracing/events').is_dir(),'memory_max':optional_text('/sys/fs/cgroup/memory.max'),'cpu_max':optional_text('/sys/fs/cgroup/cpu.max'),'fixture_policy':'isolated PID namespace; effective capability mask recorded above; no privileged/hostPID/sysctl changes'}
(OUTPUT/'environment.json').write_text(json.dumps(proof,indent=2)+'\n')
PERF=next(pathlib.Path('/usr/lib/linux-tools').glob('*/perf')).resolve()
environment=dict(os.environ,ALLOW_HOST_PROCESSES='true',ALLOWED_TARGET_UIDS=str(os.getuid()),PERF_EXECUTABLE=str(PERF),PERF_SHA256=hashlib.sha256(PERF.read_bytes()).hexdigest())
subprocess.run(['/usr/bin/g++','-std=c++20','-O1','-fno-builtin-malloc','-fno-builtin-free','-g','-fno-omit-frame-pointer',str(ROOT/'core/fixtures/io_workload.cpp'),'-o','/tmp/drop-io-fixture'],check=True)
results=[]
with tempfile.TemporaryDirectory(prefix='drop-native-advanced-') as control:
 os.chmod(control,0o700)
 native=subprocess.Popen([str(ROOT/'build/drop_native_workload')],env=dict(os.environ,NATIVE_CONTROL_ROOT=control,LD_LIBRARY_PATH='/src/build'),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
 # 编译临时文件可使用 tmpfs；真实块 I/O 证据则必须写入显式挂载的夹具文件系统，
 # 不能用纯内存 /tmp 的行为冒充磁盘 I/O。
 io=subprocess.Popen(['/tmp/drop-io-fixture'],env=dict(os.environ,DROP_IO_FIXTURE_DIRECTORY=str(OUTPUT)),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
 try:
  if not native.stdout.readline().strip().isdigit() or not io.stdout.readline().strip().isdigit():raise RuntimeError('Toy initialization failed')
  cases=[('BOLT_PROFILE',native,{}),('IO_TRACE',io,{'category':'syscalls'}),('IO_TRACE',io,{'category':'vfs'}),('IO_TRACE',io,{'category':'block'}),('MEMORY_TRACE',io,{'category':'pagefault'}),('MEMORY_TRACE',io,{'category':'alloc'}),('MEMORY_TRACE',io,{'category':'mmap'})]
  for kind,target,options in cases:
   directory=OUTPUT/(kind.lower()+'_'+options.get('category','default'));directory.mkdir(exist_ok=True)
   pid=target.pid;ticks=int(pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19])
   if kind!='BOLT_PROFILE':options.update(max_events=10000,stack_depth=16)
   request={'kind':kind,'parameters':{'pid':pid,'duration_seconds':2,'frequency_hz':99,'target':{'type':'process','pid':pid,'start_time_ticks':ticks,'expected_uid':os.getuid(),'check_uid':True},'options':options}}
   request_path=directory/'request.json';request_path.write_text(json.dumps(request))
   result=subprocess.run([str(ROOT/'build/drop_runner_probe'),str(ROOT/'contracts/task-kinds.json'),str(request_path),str(directory)],env=environment,capture_output=True,text=True,timeout=90)
   if result.returncode:
    report={'kind':kind,'category':options.get('category'),'success':False,'error':result.stderr[-3000:]}
    for name in ['runner.log','trace.raw','bolt.log']:
     log=directory/name
     if log.exists():report[name]=log.read_text(errors='replace')[-5000:]
   else:
    report=json.loads(result.stdout);report['success']=True;report['category']=options.get('category');report['fixture_source']='actual kernel capture of isolated toy PID, not production data'
   (directory/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');results.append(report);print(json.dumps({k:report[k] for k in ['kind','category','success','size','error'] if k in report}),flush=True)
 finally:
  for child in [native,io]:child.terminate()
  for child in [native,io]:
   try:child.communicate(timeout=5)
   except subprocess.TimeoutExpired:child.kill();child.communicate()
(OUTPUT/'evidence.json').write_text(json.dumps(results,indent=2)+'\n')
if not all(row['success'] for row in results):raise SystemExit(1)
