from pathlib import Path
import importlib.util
import json
import os
import subprocess
ROOT=Path.cwd()
stage=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('provider',ROOT/'output/acceptance/deployment-20260930/run_strict.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
client=p.authenticated_client()
env=dict(os.environ,MINI_DROP_API_KEY=client._key,MINI_DROP_BROWSER_BASE_URL=client.base,
    MINI_DROP_ACCEPTANCE_CHROME='C:/Program Files/Google/Chrome/Application/chrome.exe',
    MINI_DROP_HEALTH_DIAGNOSIS_ID=(stage/'health-diagnosis-id.txt').read_text().strip())
r=subprocess.run(['node',str(stage/'browser_performance.mjs')],env=env,capture_output=True,text=True,encoding='utf-8',timeout=150)
(stage/'browser.log').write_text(r.stdout+r.stderr,encoding='utf-8')
print(r.stdout)
assert r.returncode==0,'Browser validation failed; see retained log'
