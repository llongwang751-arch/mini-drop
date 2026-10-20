#!/usr/bin/env python3
"""复用生产 RunnerRegistry 实现，采集合成业务进程的真实插桩 profile。

这里验证采集链路与格式，不把玩具程序的指标解释为生产业务性能。
"""
import hashlib
import json
import os
import pathlib
import subprocess
import tempfile
import time

ROOT=pathlib.Path('/src')
OUTPUT=ROOT/'core/evidence/native-collected'
OUTPUT.mkdir(parents=True,exist_ok=True)
evidence=[]
for kind,library in [('GPERF_CPU','libprofiler.so'),('GPERF_HEAP','libtcmalloc.so'),('JEPROF_HEAP','libjemalloc.so')]:
    with tempfile.TemporaryDirectory(prefix='drop-native-control-') as control:
        os.chmod(control,0o700)
        environment=dict(os.environ,NATIVE_CONTROL_ROOT=control,LD_PRELOAD=library,LD_LIBRARY_PATH='/src/build',MALLOC_CONF='prof:true,prof_active:false,lg_prof_sample:0')
        target=subprocess.Popen([str(ROOT/'build/drop_native_workload')],env=environment,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            line=target.stdout.readline().strip()
            if not line.isdigit():raise RuntimeError('Instrumented toy failed to initialize')
            pid=int(line)
            ticks=int(pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19])
            request={'kind':kind,'parameters':{'pid':pid,'duration_seconds':2,'frequency_hz':99,'target':{'type':'process','pid':pid,'start_time_ticks':ticks,'expected_uid':os.getuid(),'check_uid':True}}}
            directory=OUTPUT/kind.lower()
            directory.mkdir(exist_ok=True)
            request_path=directory/'request.json'
            request_path.write_text(json.dumps(request))
            probe_env=dict(os.environ,NATIVE_CONTROL_ROOT=control,ALLOW_HOST_PROCESSES='true',ALLOWED_TARGET_UIDS=str(os.getuid()))
            result=subprocess.run([str(ROOT/'build/drop_runner_probe'),str(ROOT/'contracts/task-kinds.json'),str(request_path),str(directory)],env=probe_env,capture_output=True,text=True,timeout=40)
            if result.returncode:raise RuntimeError(result.stderr)
            manifest=json.loads(result.stdout)
            manifest['fixture_source']='real instrumented toy through RunnerRegistry; not production data'
            (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
            evidence.append({key:manifest[key] for key in ['kind','format','size','sha256','fixture_source']})
        finally:
            target.terminate()
            try:target.communicate(timeout=5)
            except subprocess.TimeoutExpired:target.kill();target.communicate()
(OUTPUT/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
print(json.dumps(evidence,indent=2))
