from pathlib import Path
import importlib.util
import json
import sys

sys.path.insert(0,str(Path.cwd()))
from scripts.verify_interview_demo import items_of
stage=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('provider',Path('output/acceptance/deployment-20260930/run_strict.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
c=p.authenticated_client();c.proxy_mode='direct'
ids=[r['diagnosis_id'] for r in json.loads((stage/'final-grade/localization-acceptance.json').read_text(encoding='utf-8'))['results']]
rows=items_of(c.request('GET','/api/v2/diagnoses'))
print(json.dumps({'listed':len(rows),'matching':[{k:v for k,v in r.items() if k in {'diagnosis_id','id','status','archived','source','created_at'}} for r in rows if (r.get('diagnosis_id') or r.get('id')) in ids]}))
for did in ids:
 r=c.request('GET','/api/v2/diagnoses/'+did)
 print(json.dumps({'diagnosis_id':did,'status':r.get('status'),'archived':r.get('archived')}))
