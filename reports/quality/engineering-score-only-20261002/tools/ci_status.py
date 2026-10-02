from pathlib import Path
import importlib.util
import json
import subprocess

ROOT=Path(__file__).resolve().parents[3];STAGE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('github',ROOT/'output/quality/ci-validation-20260927/github_ci.py')
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
s=g.session();s.trust_env=False
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
response=s.get(f'https://api.github.com/repos/{g.REPO}/actions/runs',params={'head_sha':head,'per_page':20},timeout=35)
response.raise_for_status();runs=response.json()['workflow_runs']
out=STAGE/'ci'/head;out.mkdir(parents=True,exist_ok=True)
if not runs:print(json.dumps({'head':head,'runs':0}));raise SystemExit(0)
run=runs[0];(out/'run.json').write_text(json.dumps(run,indent=2),encoding='utf-8')
response=s.get(run['jobs_url'],timeout=35);response.raise_for_status();jobs=response.json()
(out/'jobs.json').write_text(json.dumps(jobs,indent=2),encoding='utf-8')
print(json.dumps({'head':head,'run_id':run['id'],'status':run['status'],'conclusion':run['conclusion'],
    'jobs':[{'id':j['id'],'name':j['name'],'status':j['status'],'conclusion':j['conclusion']} for j in jobs['jobs']]},ensure_ascii=False))
