from pathlib import Path
import importlib.util
import json
import os
import subprocess

stage=Path(__file__).resolve().parent
source=(stage/'cache-empty/browser_report.mjs').read_text(encoding='utf-8')
source=source.replace('cache-empty/browser-report','web-security/browser-report')
helper=stage/'web-security/browser_report.mjs';helper.write_text(source,encoding='utf-8')
spec=importlib.util.spec_from_file_location('p',Path('output/acceptance/deployment-20260930/run_strict.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
c=p.authenticated_client()
did=json.loads((stage/'cache-empty/browser-diagnosis-final.json').read_text(encoding='utf-8'))['case']['diagnosis_id']
env=dict(os.environ,MINI_DROP_API_KEY=c._key,MINI_DROP_BROWSER_BASE_URL=c.base,
         MINI_DROP_ACCEPTANCE_CHROME='C:/Program Files/Google/Chrome/Application/chrome.exe')
r=subprocess.run(['node',str(helper),did],env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
(stage/'web-security/browser-report.log').write_text(r.stdout+r.stderr,encoding='utf-8')
print(json.dumps({'exit':r.returncode,'stdout_bytes':len(r.stdout),'stderr_bytes':len(r.stderr)}));print(r.stdout)
assert r.returncode==0
