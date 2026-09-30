from pathlib import Path
import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
import zipfile

stage=Path(__file__).resolve().parent
dest=Path('reports/quality/performance-fix-20260930')


def copy(source, name):
    target=dest/name;target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():assert target.read_bytes()==source.read_bytes(), name
    else:shutil.copyfile(source,target)


for name in ['npm-audit-before.json','npm-audit-after.json','web-axios-serial.log',
             'web-axios-build.log','tunnel-default-targeted.xml','final-runtime-verification.json']:
    copy(stage/name,name)
copy(stage/'compat-ci-run.json','compat-ci-failed/ci-run.json')
copy(Path('output/quality/ci-validation-20260927/run-36745550176-jobs.json'),'compat-ci-failed/ci-jobs.json')
copy(Path('output/quality/ci-validation-20260927/job-109990709376.log'),'compat-ci-failed/web-job.log')
for name in ['manifest.json','platform-deployment.json','platform-deployment.log','deploy_runtime.py','activate.py','browser_report.mjs','browser-report.log']:
    copy(stage/'web-security'/name,'web-security/'+name)
for name in ['result.json','report.png','tree.png']:
    copy(stage/'web-security/browser-report'/name,'web-security/browser-report/'+name)
for name in ['archive_load_final.py','archive_multi_interrupted.py','preserve_multi_interruption.py',
             'finalize_docs.py','verify_final_runtime.py','prepare_web_security.py','run_security_browser.py',
             'archive_ci_security.py','archive_ci_transport.py','poll_web_security_ci.py',
             'poll_hour.py','check_public_evidence.py','archive_final_supplements.py']:
    copy(stage/name,'helpers/final/'+name)
copy(stage/'seal_evidence.py','verify_archive.py')

# Read only the primary test result, not synthetic XML fixtures nested in pytest tmp dirs.
results={}
with zipfile.ZipFile(dest/'security-ci/ci-python-quality.zip') as archive:
    names=[n for n in archive.namelist() if n=='python-all/junit.xml']
    assert len(names)==1
    xml=ET.fromstring(archive.read(names[0]))
    suites=list(xml.iter('testsuite'))
    totals={k:sum(int(s.get(k,0)) for s in suites) for k in ['tests','failures','errors','skipped']}
    totals['passed']=totals['tests']-totals['failures']-totals['errors']-totals['skipped']
    assert totals=={'tests':1259,'failures':0,'errors':0,'skipped':10,'passed':1249},totals
    results['primary_python']=totals
results['public_files']=len([p for p in dest.rglob('*') if p.is_file()])
print(json.dumps(results))
