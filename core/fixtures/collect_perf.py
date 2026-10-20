#!/usr/bin/env python3
"""Actual V2 Runner perf_script plus native perf.data offline-import fixtures."""
import hashlib
import json
import os
import pathlib
import subprocess
import time

ROOT = pathlib.Path('/src')
OUTPUT = ROOT / 'core/evidence/perf-collected'
OUTPUT.mkdir(parents=True, exist_ok=True)
PERF = next(pathlib.Path('/usr/lib/linux-tools').glob('*/perf')).resolve()
environment = dict(os.environ, ALLOW_HOST_PROCESSES='true', ALLOWED_TARGET_UIDS=str(os.getuid()), PERF_EXECUTABLE=str(PERF), PERF_SHA256=hashlib.sha256(PERF.read_bytes()).hexdigest())
subprocess.run(['/usr/bin/g++','-std=c++20','-Wall','-Wextra','-Wpedantic','-Werror','-O1','-g','-rdynamic','-fno-omit-frame-pointer','-pthread',str(ROOT/'core/fixtures/perf_workload.cpp'),'-o','/tmp/drop-perf-fixture'],check=True,timeout=30)
target = subprocess.Popen(['/tmp/drop-perf-fixture'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
reports=[]
try:
    time.sleep(.2)
    ticks=int(pathlib.Path(f'/proc/{target.pid}/stat').read_text().rsplit(')',1)[1].split()[19])
    directory=OUTPUT/'perf_script'
    directory.mkdir(exist_ok=True)
    request={'kind':'PERF_CPU','parameters':{'pid':target.pid,'duration_seconds':2,'frequency_hz':99,'target':{'type':'process','pid':target.pid,'start_time_ticks':ticks,'expected_uid':os.getuid(),'check_uid':True},'options':{}}}
    request_path=directory/'request.json';request_path.write_text(json.dumps(request))
    result=subprocess.run([str(ROOT/'build/drop_runner_probe'),str(ROOT/'contracts/task-kinds.json'),str(request_path),str(directory)],env=environment,capture_output=True,text=True,timeout=40)
    if result.returncode:raise RuntimeError('Actual Runner perf_script collection failed: '+result.stderr[-500:])
    report=json.loads(result.stdout)
    report.update(success=True,fixture_source='actual isolated toy PID through production RunnerRegistry; V2 thread/time/DSO perf script')
    (directory/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');reports.append(report)
    directory=OUTPUT/'perf_data';directory.mkdir(exist_ok=True)
    native=directory/'profile.perf.data'
    result=subprocess.run([str(PERF),'record','-F','99','-g','--clockid','mono','-p',str(target.pid),'-o',str(native),'--','sleep','2'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,timeout=20)
    if result.returncode or not native.is_file():raise RuntimeError('Actual native perf.data fixture recording failed')
    raw=native.read_bytes()
    if raw[:8] not in (b'PERFILE2',b'2ELIFREP'):raise RuntimeError('Native perf fixture magic invalid')
    report={'kind':'PERF_CPU','name':native.name,'format':'perf_data','content_type':'application/octet-stream','size':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'success':True,'metadata':{'clock':'CLOCK_MONOTONIC'},'fixture_source':'actual isolated toy perf record native file for offline Analyzer import, not synthetic data'}
    (directory/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');reports.append(report)
finally:
    target.terminate()
    try:target.wait(timeout=5)
    except subprocess.TimeoutExpired:target.kill();target.wait()
(OUTPUT/'evidence.json').write_text(json.dumps(reports,indent=2)+'\n')
print(json.dumps([{'format':r['format'],'size':r['size'],'success':True} for r in reports],indent=2))
