from pathlib import Path
import importlib.util
import json
import os
import subprocess

ROOT=Path(__file__).resolve().parents[3];STAGE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('provider',ROOT/'output/acceptance/deployment-20260930/run_strict.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p);client=p.authenticated_client()
checks=[]
for folder,name,label in [('go-normal','normal','检查结果：正常'),('go-anomaly','anomaly','检查结果：异常'),
    ('go-recovery','recovery','检查结果：正常'),('go-interrupted','unknown','检查结果：无法判断'),('go-resampled','resampled','检查结果：正常')]:
    records=json.loads((STAGE/folder/'records.json').read_text(encoding='utf-8'))
    checks.append({'name':name,'label':label,'diagnosis_id':records['diagnosis']['diagnosis_id']})
acceptance=json.loads((ROOT/'output/acceptance/performance-localization-20261001/final-grade/localization-acceptance.json').read_text(encoding='utf-8'))
business=json.loads((STAGE/'business-fix-r3/validated-comparison.json').read_text(encoding='utf-8'))
env=dict(os.environ,MINI_DROP_API_KEY=client._key,MINI_DROP_BROWSER_BASE_URL=client.base,
    MINI_DROP_ACCEPTANCE_CHROME='C:/Program Files/Google/Chrome/Application/chrome.exe',
    MINI_DROP_HEALTH_CASES=json.dumps(checks),MINI_DROP_BUSINESS_CASE=business['diagnosis_id'],
    MINI_DROP_LOCALIZATION_DIAGNOSES=json.dumps(acceptance['results']),MINI_DROP_BROWSER_OUTPUT=str(STAGE/'browser'))
result=subprocess.run(['node',str(STAGE/'browser_engineering.mjs')],env=env,capture_output=True,text=True,encoding='utf-8',timeout=180)
(STAGE/'browser.log').write_text(result.stdout+result.stderr,encoding='utf-8')
print(result.stdout)
assert result.returncode==0,'Browser failed; original log/screens retained'
