from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import tarfile

stage=Path(__file__).resolve().parent/'web-security'
stage.mkdir(exist_ok=True)
head=subprocess.check_output(['git','rev-parse','HEAD']).decode().strip()
assert head.startswith('7403815')
assert not subprocess.check_output(['git','diff','HEAD','--','web/package.json','web/package-lock.json','web/src']).strip()
tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
source=stage/'deployment-source'
assert not source.exists()
files={}
for path in sorted(Path('web/dist').rglob('*')):
    if path.is_file():
        raw=path.read_bytes();target=source/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        files[path.as_posix()]=hashlib.sha256(raw).hexdigest()
for name in ['web/package.json','web/package-lock.json','docs/PROJECT_CONTEXT.md','docs/SERVICE_INTEGRATION.md']:
    raw=subprocess.check_output(['git','show',head+':'+name]);target=source/name
    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    files[name]=hashlib.sha256(raw).hexdigest()
manifest={'git_head':head,'release_tag':tag,'files':files,'changed_services':['web'],
          'unchanged_worker_analyzer_source':'dc50c47692bd0052b59b5d0d6c3c679fff9d8960',
          'unchanged_api_source':'f9b143ae3dfa4409ed621d34b5d2971e90c0a972',
          'unchanged_office_source':'f37f44e0f43ea58720da3638658a82c65430c595'}
(stage/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
(stage/'release-tag.txt').write_text(tag,encoding='utf-8')
helper=Path('output/acceptance/deployment-20260930/deploy_web.py').read_bytes()
(stage/'deploy_runtime.py').write_bytes(helper)
with tarfile.open(stage/'runtime-bundle.tgz','w:gz') as archive:
    archive.add(stage/'manifest.json',arcname='manifest.json')
    archive.add(stage/'deploy_runtime.py',arcname='deploy_runtime.py')
    for p in sorted(source.rglob('*')):
        if p.is_file():archive.add(p,arcname='source/'+p.relative_to(source).as_posix())
print(json.dumps({'source':head,'tag':tag,'web_files':sum(n.startswith('web/dist/') for n in files)}))
