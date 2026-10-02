from pathlib import Path
import hashlib
import importlib.util
import json
import sys
from urllib.parse import quote

sys.path.insert(0,str(Path.cwd()))
from scripts.verify_interview_demo import items_of
stage=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('provider',Path('output/acceptance/deployment-20260930/run_strict.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
c=p.authenticated_client();c.proxy_mode='direct'
records=json.loads((stage/'health-live/records.json').read_text(encoding='utf-8'))
out=stage/'health-live/raw';out.mkdir(exist_ok=False)
rows=[]
for tool in records['tool-calls']:
 tid=tool.get('task_id')
 if not tid:continue
 for artifact in items_of(c.request('GET',f'/api/tasks/{tid}/artifacts')):
  kind=artifact['artifact_type']
  path=(out/(tid+'-'+kind+'.raw')).resolve();assert path.is_relative_to(out.resolve())
  raw=c.request_raw('GET','/api/tasks/'+quote(tid,safe='')+'/artifacts/'+quote(kind,safe='')+'/download')
  digest=hashlib.sha256(raw).hexdigest();assert digest==artifact['sha256']
  path.write_bytes(raw);rows.append({'file':path.name,'sha256':digest,'bytes':len(raw)})
assert len(rows)==2
(stage/'health-live/raw-download-manifest.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
print(json.dumps({'downloaded_sha_verified':len(rows)}))
