from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import tarfile

root = Path.cwd()
stage = Path(__file__).resolve().parent
release = stage/'release'
release.mkdir(exist_ok=True)
head = subprocess.check_output(['git','rev-parse','HEAD']).decode().strip()
(release/'source-head.txt').write_text(head)
prior = root/'output/acceptance/performance-localization-20261001/release-r8'
for name in ('ci_status.py','activate_platform.py'):
    (release/name).write_bytes((prior/name).read_bytes())
deploy = (prior/'deploy_runtime.py').read_text(encoding='utf-8')
deploy = deploy.replace("SERVICES = ('web',)", "SERVICES = ('diagnosis-worker', 'analyzer', 'web')")
deploy = deploy.replace('COPY scripts/ /app/scripts/\\n', 'COPY scripts/ /app/scripts/\\nCOPY contracts/ /app/contracts/\\n')
(release/'deploy_runtime.py').write_text(deploy, encoding='utf-8')
assert not (release/'manifest.json').exists()
test = json.loads((stage/'web-frozen-tests.json').read_text(encoding='utf-8'))
assert test['success'] and test['numPassedTests'] == 308
assert 'web bundle check passed' in (stage/'web-frozen-build.log').read_text(encoding='utf-8')
tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
source = release/'source'
source.mkdir(exist_ok=False)
files = {}
paths = subprocess.check_output(['git','ls-files','server','analyzer','scripts','contracts']).decode().splitlines()
for name in paths:
    raw = subprocess.check_output(['git','show', head+':'+name])
    dst = source/name;dst.parent.mkdir(parents=True, exist_ok=True);dst.write_bytes(raw)
    files[name] = hashlib.sha256(raw).hexdigest()
extra = {}
for path in (stage/'web-source/web/dist').rglob('*'):
    if path.is_file():
        name = 'web/dist/'+path.relative_to(stage/'web-source/web/dist').as_posix()
        raw = path.read_bytes(); dst = source/name;dst.parent.mkdir(parents=True, exist_ok=True);dst.write_bytes(raw)
        extra[name] = hashlib.sha256(raw).hexdigest()
for name in ('PROJECT_CONTEXT.md','RESTART_HANDOFF.md','AI_DIAGNOSIS.md','PERFORMANCE_DIAGNOSIS.md'):
    key = 'docs/'+name; raw = subprocess.check_output(['git','show',head+':'+key])
    dst = source/key;dst.parent.mkdir(parents=True, exist_ok=True);dst.write_bytes(raw)
    extra[key] = hashlib.sha256(raw).hexdigest()
web = json.loads((stage/'web-source-manifest.json').read_text(encoding='utf-8'))
manifest = {'git_head':head, 'release_tag':tag, 'fault_lab_agent_id':'control-interview-demo-agent',
    'files':files, 'deployment_files':extra, 'web_source':web, 'web_tests_passed':308}
(release/'manifest.json').write_text(json.dumps(manifest,indent=2), encoding='utf-8')
(release/'release-tag.txt').write_text(tag)
with tarfile.open(release/'runtime-bundle.tgz','w:gz') as archive:
    archive.add(release/'manifest.json', arcname='manifest.json')
    archive.add(release/'deploy_runtime.py', arcname='deploy_runtime.py')
    for name in [*files,*extra]: archive.add(source/name, arcname='source/'+name)
print(json.dumps({'head':head, 'release':tag, 'backend_files':len(files), 'web_files':len(extra)-4}))
