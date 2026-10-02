from pathlib import Path
import importlib.util
import json
import sys

sys.path.insert(0,str(Path.cwd()))
from scripts.verify_interview_demo import items_of
stage=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('provider',Path('output/acceptance/deployment-20260930/run_strict.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
client=p.authenticated_client();client.proxy_mode='direct'
rows=items_of(client.request('GET','/api/v2/diagnoses'))
selected=[]
for row in rows:
    if str(row.get('created_at',''))[:19]<'2026-10-01 09:58:00':continue
    did=row.get('diagnosis_id') or row.get('id')
    if not did:continue
    reports=items_of(client.request('GET',f'/api/v2/diagnoses/{did}/reports'))
    tools=items_of(client.request('GET',f'/api/v2/diagnoses/{did}/tool-calls'))
    selected.append({'diagnosis_id':did,'status':row.get('status'),'created_at':row.get('created_at'),
        'tools':[{'tool':t.get('tool_name'),'status':t.get('status')} for t in tools],
        'reports':[{'status':(r.get('verification') or {}).get('status'),
                    'localization':(r.get('verification') or {}).get('bottleneck_localization'),
                    'observation':(r.get('verification') or {}).get('observation_verification')} for r in reports]})
print(json.dumps(selected,ensure_ascii=False))
