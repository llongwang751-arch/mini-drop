from pathlib import Path
import hashlib
import importlib.util
import json
import os
import zipfile

root = Path.cwd()
stage = Path(__file__).resolve().parent
target = root / 'reports/quality/performance-localization-20261001'
target.mkdir(exist_ok=False)
spec = importlib.util.spec_from_file_location('provider', root / 'output/acceptance/deployment-20260930/run_strict.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
key = p.authenticated_client()._key.encode()
manifest = []

def copy(source, name):
    raw = source.read_bytes()
    assert key not in raw, source.name
    if source.suffix == '.zip':
        with zipfile.ZipFile(source) as archive:
            assert all(key not in archive.read(n) for n in archive.namelist() if not n.endswith('/'))
    destination = target / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    manifest.append({'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})

for name in ('performance-localization-20261001', 'performance-localization-buildfix-20261001',
             'performance-localization-livefix-20261001', 'performance-localization-iofix-20261001',
             'performance-localization-iofix-r2-20261001'):
    quality = root / 'output/quality' / name
    for source in quality.rglob('*'):
        if source.is_file() and 'tmp' not in source.relative_to(quality).parts:
            copy(source, 'local/' + name + '/' + source.relative_to(quality).as_posix())
old = root / 'output/acceptance/performance-diagnosis-20261001'
for name in ('observation-persistence-after.xml', 'observation-persistence-after-r3.xml', 'web-localization.xml'):
    copy(old / name, 'regression/' + name)
excluded={'deployment-source','__pycache__','tmp','web-refutation-sandbox','node_modules'}
for parent, directories, filenames in os.walk(stage):
    directories[:]=[name for name in directories if name not in excluded]
    for name in filenames:
        source=Path(parent)/name
        if source.suffix=='.pyc' or source.name=='runtime-bundle.tgz':continue
        relative=source.relative_to(stage)
        copy(source,'deployed/'+relative.as_posix())
(target / 'manifest.json').write_text(json.dumps({'schema': 'mini-drop.delivery-evidence.v1',
    'scope': 'Local tests and three fresh observed paths; old 21 causal scores unchanged.',
    'files': manifest}, indent=2), encoding='utf-8')
print(json.dumps({'archived_files': len(manifest), 'target': str(target)}))
