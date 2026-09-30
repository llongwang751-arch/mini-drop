import importlib.util,json,subprocess
from pathlib import Path
stage=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('ci',Path('output/quality/ci-validation-20260927/github_ci.py'))
ci=importlib.util.module_from_spec(spec);spec.loader.exec_module(ci)
c=ci.session();c.trust_env=False
head=subprocess.check_output(['git','rev-parse','HEAD']).decode().strip()
r=c.get('https://api.github.com/repos/'+ci.REPO+'/actions/runs',params={'event':'pull_request','per_page':8},timeout=30);r.raise_for_status()
for run in r.json()['workflow_runs']:
 if run['head_sha']==head:
  (stage/'ci-run.json').write_text(json.dumps(run,indent=2),encoding='utf-8')
  print(json.dumps({k:run.get(k) for k in ['id','head_sha','status','conclusion']}))
  break
else: print('Run not yet listed')
