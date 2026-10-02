from pathlib import Path
import hashlib
import io
import json
import subprocess
import tarfile

root=Path.cwd();stage=Path(__file__).resolve().parent
release=stage/'release-r6';release.mkdir(exist_ok=False)
head=subprocess.check_output(['git','rev-parse','HEAD']).decode().strip()
source=release/'deployment-source';source.mkdir()
raw=subprocess.check_output(['git','archive','--format=tar',head,'web'])
with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
 for member in archive.getmembers():
  assert (source/member.name).resolve().is_relative_to(source.resolve())
 archive.extractall(source,filter='data')
for name in ('activate_platform.py','ci_status.py'):
 (release/name).write_bytes((stage/'release-r5'/name).read_bytes())
deploy=(stage/'release-r5/deploy_runtime.py').read_text(encoding='utf-8')
old="SERVICES = ('diagnosis-worker', 'analyzer', 'web', 'go-hotspot')"
assert old in deploy
(release/'deploy_runtime.py').write_text(deploy.replace(old,"SERVICES = ('web',)",1),encoding='utf-8')
(release/'source-head.txt').write_text(head)
print(json.dumps({'source_head':head,'services':['web'],'source':'immutable git archive','web_lock_sha256':hashlib.sha256((source/'web/package-lock.json').read_bytes()).hexdigest()}))
