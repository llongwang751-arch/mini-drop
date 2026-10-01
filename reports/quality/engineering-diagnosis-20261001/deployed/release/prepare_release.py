from pathlib import Path
from datetime import datetime,timezone
import hashlib
import json
import subprocess
import tarfile

root=Path.cwd();stage=Path(__file__).resolve().parent
head=(stage/'source-head.txt').read_text().strip()
source=stage/'deployment-source'
tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
assert not (stage/'manifest.json').exists()
files={}
for path in (source/'web/dist').rglob('*'):
 if path.is_file():files[path.relative_to(source).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
assert len(files)==57
for name in ('PROJECT_CONTEXT.md','RESTART_HANDOFF.md','AI_DIAGNOSIS.md','PERFORMANCE_DIAGNOSIS.md','FAULT_PLAZA_ACCEPTANCE.md','DIAGNOSIS_ACCEPTANCE.md'):
 path=source/'docs'/name;path.parent.mkdir(exist_ok=True)
 raw=subprocess.check_output(['git','show',head+':docs/'+name]);path.write_bytes(raw)
 files['docs/'+name]=hashlib.sha256(raw).hexdigest()
manifest={'git_head':head,'release_tag':tag,'fault_lab_agent_id':'control-interview-demo-agent',
 'files':{},'deployment_files':files,'build_source':'immutable git archive',
 'component_sources':{'web':head,'diagnosis-worker':'c59aac566cedef6a239eb1d640ee8fb796524ae3',
                     'analyzer':'c59aac566cedef6a239eb1d640ee8fb796524ae3','go-hotspot':'c59aac566cedef6a239eb1d640ee8fb796524ae3'},
 'npm_lock_sha256':hashlib.sha256((source/'web/package-lock.json').read_bytes()).hexdigest()}
(stage/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
(stage/'release-tag.txt').write_text(tag)
with tarfile.open(stage/'runtime-bundle.tgz','w:gz') as archive:
 archive.add(stage/'manifest.json',arcname='manifest.json')
 archive.add(stage/'deploy_runtime.py',arcname='deploy_runtime.py')
 for name in files:archive.add(source/name,arcname='source/'+name)
print(json.dumps({'release':tag,'source':head,'web_files':57,'changed_services':['web']}))
