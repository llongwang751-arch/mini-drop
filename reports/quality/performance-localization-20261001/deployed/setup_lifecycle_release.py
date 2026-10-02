from pathlib import Path
import hashlib
import io
import json
import subprocess
import tarfile

stage = Path(__file__).resolve().parent
release = stage / 'release-r8'
release.mkdir(exist_ok=False)
head = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip()
(release / 'source-head.txt').write_text(head)
source = release / 'deployment-source'
source.mkdir()
raw = subprocess.check_output(['git', '-c', 'core.autocrlf=false', 'archive', '--format=tar', head, 'web'])
with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
    for member in archive.getmembers(): assert (source / member.name).resolve().is_relative_to(source)
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
(release / 'source-verification.json').write_text(json.dumps({'source_head': head, 'archive_sha256': hashlib.sha256(raw).hexdigest(), 'files': files, 'eol_only_restored': restored}, indent=2))
for name in ('activate_platform.py', 'ci_status.py', 'deploy_runtime.py', 'prepare_release.py'):
    (release/name).write_bytes((stage/'release-r7'/name).read_bytes())
text = (stage/'build_route_web.py').read_text(encoding='utf-8').replace('release-r7', 'release-r8').replace('291', '294').replace('web-route-clean-', 'web-lifecycle-clean-')
(stage/'build_lifecycle_web.py').write_text(text, encoding='utf-8')
for name in ('collect_ci_route.py', 'collect_ci_logs_route.py'):
    text = (stage/name).read_text(encoding='utf-8').replace('release-r7','release-r8').replace('ci-artifacts-route','ci-artifacts-lifecycle').replace('ci-logs-route','ci-logs-lifecycle')
    (stage/name.replace('_route', '_lifecycle')).write_text(text, encoding='utf-8')
print(json.dumps({'source': head, 'source_files':len(files), 'services':['web']}))
