from pathlib import Path
import importlib.util
import json
spec=importlib.util.spec_from_file_location('ci',Path('output/quality/ci-validation-20260927/github_ci.py'))
ci=importlib.util.module_from_spec(spec);spec.loader.exec_module(ci)
c=ci.session();c.trust_env=False
response=c.get('https://api.github.com/repos/'+ci.REPO+'/actions/runs/36746799170',timeout=30)
response.raise_for_status()
run=response.json();assert run['head_sha'].startswith('7403815')
path=Path(__file__).resolve().parent/'web-security/ci-run.json'
path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(run,indent=2),encoding='utf-8')
print(json.dumps({k:run.get(k) for k in ['id','head_sha','status','conclusion']}))
