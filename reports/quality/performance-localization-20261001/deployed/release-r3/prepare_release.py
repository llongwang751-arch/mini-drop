from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import tarfile

ROOT=Path.cwd().resolve()
stage=Path(__file__).resolve().parent
tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip()
assert not subprocess.check_output(['git','diff','HEAD','--','server','analyzer','scripts','web/src','web/public'],cwd=ROOT)
paths=subprocess.check_output(['git','ls-files','server','analyzer','scripts'],cwd=ROOT).decode().splitlines()
extras=['demo/go-hotspot/'+name for name in ('main.go','main_test.go','go.mod','Dockerfile')]+['docs/'+name for name in ('PROJECT_CONTEXT.md','RESTART_HANDOFF.md','AI_DIAGNOSIS.md','PERFORMANCE_DIAGNOSIS.md','FAULT_PLAZA_ACCEPTANCE.md')]
files,extra={},{}
source=stage/'deployment-source';source.mkdir(exist_ok=False)
for name in paths+extras:
    raw=subprocess.check_output(['git','show',head+':'+name],cwd=ROOT)
    target=source/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    (files if name in paths else extra)[name]=hashlib.sha256(raw).hexdigest()
build=[p for p in (ROOT/'web/dist').rglob('*') if p.is_file()]
for path in build:
    name=path.relative_to(ROOT).as_posix();raw=path.read_bytes()
    target=source/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    extra[name]=hashlib.sha256(raw).hexdigest()
manifest={'git_head':head,'release_tag':tag,'fault_lab_agent_id':'control-interview-demo-agent',
    'files':files,'deployment_files':extra,'unchanged_api_source':'f9b143ae3dfa4409ed621d34b5d2971e90c0a972'}
(stage/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
(stage/'release-tag.txt').write_text(tag)
with tarfile.open(stage/'runtime-bundle.tgz','w:gz') as archive:
    archive.add(stage/'manifest.json',arcname='manifest.json')
    archive.add(stage/'deploy_runtime.py',arcname='deploy_runtime.py')
    for path in source.rglob('*'):
        if path.is_file():archive.add(path,arcname='source/'+path.relative_to(source).as_posix())
print(json.dumps({'release':tag,'source':head,'runtime_files':len(files),'web_files':len(build)}))
