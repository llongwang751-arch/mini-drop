import os,json,subprocess,importlib.util
from pathlib import Path
stage=Path(__file__).resolve().parent
p=Path('output/acceptance/deployment-20260930/run_strict.py');sp=importlib.util.spec_from_file_location('p',p);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m)
c=m.authenticated_client()
source=Path('output/cloud-sre-exercise-20260923T141511Z/verify_business.py').read_text(encoding='utf-8')
source=source[:source.index('rows = []')]+"print(json.dumps({'token':token}))"
office_token=json.loads(m.remote(source))['token']

env=dict(os.environ,MINI_DROP_API_KEY=c._key,OFFICE_ACCEPTANCE_TOKEN=office_token,MINI_DROP_BROWSER_BASE_URL=c.base,
         MINI_DROP_ACCEPTANCE_CHROME='C:/Program Files/Google/Chrome/Application/chrome.exe')
r=subprocess.run(['node',str(stage/'browser_exercise.mjs')],env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
(stage/'browser-exercise.log').write_text(r.stdout+r.stderr,encoding='utf-8')
print(json.dumps({"exit":r.returncode,"stdout_bytes":len(r.stdout),"stderr_bytes":len(r.stderr)}));print(r.stdout)
assert r.returncode==0
