from pathlib import Path
import importlib.util
import json
import subprocess

stage=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('g',Path('output/quality/ci-validation-20260927/github_ci.py'))
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
c=g.session();c.trust_env=False
head=(stage/'source-head.txt').read_text().strip()
base='https://api.github.com/repos/'+g.REPO
r=c.get(base+'/actions/runs',params={'head_sha':head,'event':'pull_request','per_page':30},timeout=30);r.raise_for_status()
runs=r.json()['workflow_runs']
if not runs:
    print(json.dumps({'head':head,'status':'NOT_STARTED'}));raise SystemExit(0)
run=max(runs,key=lambda x:x['id'])
r=c.get(base+f"/actions/runs/{run['id']}/jobs",params={'per_page':100},timeout=30);r.raise_for_status()
jobs=r.json()
(stage/'ci-run.json').write_text(json.dumps(run,indent=2),encoding='utf-8')
(stage/'ci-jobs.json').write_text(json.dumps(jobs,indent=2),encoding='utf-8')
print(json.dumps({'head':head,'id':run['id'],'status':run['status'],'conclusion':run['conclusion'],
                  'jobs':[{k:x[k] for k in ['name','status','conclusion']} for x in jobs['jobs']]}))
