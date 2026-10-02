"""Read back this run's remote evidence after local disappearance; preserve RUNNING unchanged."""
from pathlib import Path
import hashlib
import json
import shlex
import subprocess

stage=Path(__file__).resolve().parent
source=stage/'after-multi-hour'
report=json.loads((source/'report.json').read_text(encoding='utf-8'))
assert report['status']=='RUNNING'
assert report['run_id']=='a27e3d4ff1124f2db9eefefc667ead07'
remote_root='/home/ubuntu/mini-drop-perf-multi-hour-20260930a'
code="""import os,json,hashlib
from pathlib import Path
root=Path(%r)
pid=%r
proc=Path('/proc')/str(pid)
print(json.dumps({'pid':pid,'pid_exists':proc.exists(),'completed':json.loads((root/'completed.json').read_text()) if (root/'completed.json').exists() else None,'resource_lines':sum(1 for _ in (root/'resources.jsonl').open()),'resource_sha256':hashlib.sha256((root/'resources.jsonl').read_bytes()).hexdigest()}))
"""%(remote_root,report['remote']['pid'])
args=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','ubuntu@106.52.176.128']
r=subprocess.run(args+['python3 -c '+shlex.quote(code)],capture_output=True,check=True,timeout=60)
remote=json.loads(r.stdout)
assert not remote['pid_exists'], 'owned process still present; do not claim cleanup'
raw=subprocess.run(args+['cat '+shlex.quote(remote_root+'/resources.jsonl')],capture_output=True,check=True,timeout=60).stdout
assert hashlib.sha256(raw).hexdigest()==remote['resource_sha256']
source.joinpath('interrupted-remote-resources.jsonl').write_bytes(raw)
source.joinpath('interrupted-remote-completed.json').write_text(json.dumps(remote,indent=2),encoding='utf-8')
rows=[json.loads(x) for x in (source/'soak.jsonl').read_text(encoding='utf-8').splitlines() if x.endswith('}')]
result={'status':'INTERRUPTED','original_report_status':'RUNNING','run_id':report['run_id'],
 'soak_recorded_requests':len(rows),'last_scheduled_soak_offset_seconds':max(r['scheduled_offset_seconds'] for r in rows),
 'failed_requests':sum(not r['success'] or not r['quality_passed'] for r in rows),
 'local_process_missing':True,'owned_remote_pid_exited':True,
 'cause':'Local process disappeared; exact termination cause is not established',
 'complete_hour':False,'remote_readback_sha256':hashlib.sha256(raw).hexdigest()}
target=source/'interruption.json'
assert not target.exists()
target.write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
