from pathlib import Path
from datetime import datetime, timezone
import subprocess
import tarfile
import hashlib
import json

stage = Path(__file__).resolve().parent
tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
head = subprocess.check_output(['git','rev-parse','HEAD']).decode().strip()
helper = Path('reports/quality/security-cancel-20260930/deploy_runtime.py').read_text(encoding='utf-8')
helper = helper.replace("SERVICES = ('diagnosis-worker', 'analyzer', 'apiserver', 'web')", "SERVICES = ('diagnosis-worker', 'analyzer', 'web')")
(stage/'deploy_runtime.py').write_text(helper,encoding='utf-8')
paths = subprocess.check_output(['git','ls-files','server','analyzer','scripts']).decode().splitlines()
docs = ['docs/'+name for name in ('PROJECT_CONTEXT.md','RESTART_HANDOFF.md','DISTRIBUTED_LOAD.md','FULL_CHAIN_ACCEPTANCE.md')]
extras = docs + ['integrations/agi_saber/request_observations.py']
build = [p for p in Path('web/dist').rglob('*') if p.is_file()]
files, extra = {}, {}
source = stage/'deployment-source'
assert not source.exists()
for name in paths+extras:
    raw = subprocess.check_output(['git','show',head+':'+name])
    target = source/name
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(raw)
    (files if name in paths else extra)[name] = hashlib.sha256(raw).hexdigest()
for name in build:
    raw = name.read_bytes()
    target = source/name
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(raw)
    extra[name.as_posix()] = hashlib.sha256(raw).hexdigest()
manifest = {'git_head':head,'release_tag':tag,'fault_lab_agent_id':'control-interview-demo-agent','files':files,'deployment_files':extra,'unchanged_api_source':'f9b143ae3dfa4409ed621d34b5d2971e90c0a972'}
(stage/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
(stage/'release-tag.txt').write_text(tag)
with tarfile.open(stage/'runtime-bundle.tgz','w:gz') as t:
    t.add(stage/'manifest.json',arcname='manifest.json')
    t.add(stage/'deploy_runtime.py',arcname='deploy_runtime.py')
    for p in source.rglob('*'):
        if p.is_file():t.add(p,arcname='source/'+p.relative_to(source).as_posix())
print(json.dumps({'tag':tag,'source_head':head,'runtime_files':len(files),'web_files':len(build),'api_unchanged':True}))
