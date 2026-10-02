from pathlib import Path
from datetime import datetime,timezone
import hashlib
import json
import shutil
import subprocess
import tarfile
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[3];STAGE=Path(__file__).resolve().parent
release=STAGE/'release';release.mkdir(exist_ok=True);assert not (release/"manifest.json").exists()
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
(release/'source-head.txt').write_text(head)
test=ET.parse(STAGE/'final-web.xml').getroot()
suites=list(test.iter('testsuite'));passed=sum(int(s.get('tests',0)) for s in suites)
assert passed==309 and not any(int(s.get('failures',0)) or int(s.get('errors',0)) for s in suites)
assert 'web bundle check passed' in (STAGE/'final-web-2.log').read_text(encoding='utf-8')
clean=ROOT/'.tmp-interview-r2'
paths=subprocess.check_output(['git','ls-tree','-r','--name-only',head,'web'],cwd=ROOT,text=True).splitlines()
web_source={}
for name in paths:
    raw=subprocess.check_output(['git','show',head+':'+name],cwd=ROOT)
    assert (clean/name).read_bytes()==raw,name
    web_source[name]=hashlib.sha256(raw).hexdigest()
prior=ROOT/'output/acceptance/health-check-flow-20261001/release'
for name in ('deploy_runtime.py','activate_platform.py'):(release/name).write_bytes((prior/name).read_bytes())
tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');source=release/'source';source.mkdir()
files={};extra={}
paths=subprocess.check_output(['git','ls-tree','-r','--name-only',head,'server','analyzer','scripts','contracts'],cwd=ROOT,text=True).splitlines()
for name in paths:
    raw=subprocess.check_output(['git','show',head+':'+name],cwd=ROOT);target=source/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw);files[name]=hashlib.sha256(raw).hexdigest()
for file in sorted((clean/'web/dist').rglob('*')):
    if file.is_file():
        name='web/dist/'+file.relative_to(clean/'web/dist').as_posix();raw=file.read_bytes();target=source/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw);extra[name]=hashlib.sha256(raw).hexdigest()
for name in ('PROJECT_CONTEXT','RESTART_HANDOFF','AI_DIAGNOSIS','DIAGNOSIS_ACCEPTANCE','PERFORMANCE_DIAGNOSIS','ENGINEERING_DELIVERY','INTERVIEW_DEMO_GUIDE'):
    path='docs/'+name+'.md';raw=subprocess.check_output(['git','show',head+':'+path],cwd=ROOT);target=source/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw);extra[path]=hashlib.sha256(raw).hexdigest()
manifest={'git_head':head,'release_tag':tag,'fault_lab_agent_id':'control-interview-demo-agent','files':files,'deployment_files':extra,
    'web_source':{'source_kind':'EXACT_GIT_HEAD','git_head':head,'source_files':web_source},'web_tests_passed':passed}
(release/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8');(release/'release-tag.txt').write_text(tag)
with tarfile.open(release/'runtime-bundle.tgz','w:gz') as archive:
    archive.add(release/'manifest.json',arcname='manifest.json');archive.add(release/'deploy_runtime.py',arcname='deploy_runtime.py')
    for name in [*files,*extra]:archive.add(source/name,arcname='source/'+name)
print(json.dumps({'head':head,'release':tag,'backend_files':len(files),'web_files':len([p for p in extra if p.startswith('web/dist/')]),'web_tests':passed}))
