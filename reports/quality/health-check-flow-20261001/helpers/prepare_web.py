from pathlib import Path
import hashlib
import json
import subprocess

root = Path.cwd()
stage = Path(__file__).resolve().parent
prior = root / 'output/frontend-redesign-20261001/cloud-release'
source = stage / 'web-source'
assert not source.exists()
manifest = json.loads((prior / 'source-manifest.json').read_text(encoding='utf-8'))
files = {}
for name, sha in manifest['source_files'].items():
    raw = (prior / 'source' / name).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == sha, name
    target = source / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    files[name] = sha
changed = ['web/src/pages/AIDiagnosis.jsx', 'web/src/pages/AIDiagnosis.test.jsx',
    'web/src/components/ObservabilityOverview.jsx', 'web/src/components/ObservabilityOverview.test.jsx',
    'web/src/components/ManagedServicesPanel.jsx', 'web/src/components/ManagedServicesPanel.test.jsx',
    'web/src/components/HealthCheckActions.jsx', 'web/src/components/HealthCheckActions.test.jsx']
for name in changed:
    raw = (root / name).read_bytes()
    target = source / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    files[name] = hashlib.sha256(raw).hexdigest()
assert (source / 'web/package-lock.json').read_bytes() == (root / 'web/package-lock.json').read_bytes()
(stage/'web-source-manifest.json').write_text(json.dumps({
    'source_kind': 'previous frozen frontend plus health-check flow',
    'base_release': manifest['release_tag'], 'source_files': files, 'changed_files': changed,
}, indent=2), encoding='utf-8')
print(json.dumps({'source_files': len(files), 'base_release': manifest['release_tag']}))
