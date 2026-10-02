from pathlib import Path
import hashlib
import io
import json
import subprocess
import tarfile

stage = Path(__file__).resolve().parent
old = stage.parent / 'performance-localization-20261001'
release = stage / 'release'
release.mkdir(exist_ok=False)
head = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip()
(release / 'source-head.txt').write_text(head)
source = release / 'deployment-source'
source.mkdir()
raw = subprocess.check_output(['git', 'archive', '--format=tar', head, 'web'])
with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
    for member in archive.getmembers():
        assert (source / member.name).resolve().is_relative_to(source)
    archive.extractall(source, filter='data')
names = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', head, 'web']).decode().splitlines()
blobs = subprocess.check_output(['git', 'cat-file', '--batch'], input=''.join(head+':'+n+'\n' for n in names).encode())
offset = 0
files = []
restored = []
for name in names:
    end = blobs.index(b'\n', offset)
    size = int(blobs[offset:end].split()[2])
    blob = blobs[end+1:end+1+size]
    offset = end+size+2
    path = source / name
    actual = path.read_bytes()
    if actual != blob:
        assert actual.replace(b'\r\n', b'\n') == blob.replace(b'\r\n', b'\n'), name
        path.write_bytes(blob)
        restored.append(name)
    assert path.read_bytes() == blob
    files.append({'path': name, 'sha256': hashlib.sha256(blob).hexdigest()})
(release/'source-verification.json').write_text(json.dumps({'source_head':head,'archive_sha256':hashlib.sha256(raw).hexdigest(),'files':files,'eol_only_restored':restored},indent=2))
for name in ('activate_platform.py','ci_status.py','deploy_runtime.py','prepare_release.py'):
    content=(old/'release-r8'/name).read_text(encoding='utf-8')
    if name=='prepare_release.py':
        content=content.replace('==56','==57').replace("'web_files':56","'web_files':57")
        content=content.replace("'FAULT_PLAZA_ACCEPTANCE.md'", "'FAULT_PLAZA_ACCEPTANCE.md','DIAGNOSIS_ACCEPTANCE.md'")
    (release/name).write_text(content,encoding='utf-8')
content=(old/'build_lifecycle_web.py').read_text(encoding='utf-8')
content=content.replace("'release-r8'","'release'").replace('294','299').replace('web-lifecycle-clean','web-engineering-clean')
content=content.replace("'src/hooks/useSSE.test.jsx'", "'src/hooks/useSSE.test.jsx','src/components/EngineeringDiagnosisSummary.jsx','src/components/EngineeringDiagnosisSummary.test.jsx','src/components/EvalPanel.jsx','src/components/FaultPlazaPanel.jsx','src/components/FaultPlazaPanel.test.jsx','public/report-assets/engineering-diagnosis/index.json'")
(stage/'build_web.py').write_text(content,encoding='utf-8')
for name in ('collect_ci_lifecycle.py','collect_ci_logs_lifecycle.py'):
    content=(old/name).read_text(encoding='utf-8').replace('release-r8','release').replace('ci-artifacts-lifecycle','ci-artifacts').replace('ci-logs-lifecycle','ci-logs')
    (stage/name.replace('_lifecycle','')).write_text(content,encoding='utf-8')
print(json.dumps({'source':head,'files':len(files),'services':['web']}))
