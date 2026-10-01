from pathlib import Path
import hashlib
import importlib.util
import json
import os
import zipfile

root=Path.cwd()
stage=Path(__file__).resolve().parent
target=root/'reports/quality/engineering-diagnosis-20261001'
target.mkdir(exist_ok=False)
spec=importlib.util.spec_from_file_location('provider',root/'output/acceptance/deployment-20260930/run_strict.py')
provider=importlib.util.module_from_spec(spec);spec.loader.exec_module(provider)
key=provider.authenticated_client()._key.encode()
manifest=[]
def copy(source,name):
    raw=source.read_bytes()
    assert key not in raw,source.name
    if source.suffix=='.zip':
        with zipfile.ZipFile(source) as archive:
            assert all(key not in archive.read(n) for n in archive.namelist() if not n.endswith('/'))
    destination=target/name
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_bytes(raw)
    manifest.append({'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
quality=root/'output/quality/engineering-diagnosis-20261001'
for parent,directories,names in os.walk(quality):
    directories[:]=[name for name in directories if name not in {'tmp','__pycache__'}]
    for name in names:
        source=Path(parent)/name
        copy(source,'local/'+source.relative_to(quality).as_posix())
excluded={'web','guide-source','deployment-source','__pycache__','tmp','node_modules'}
for parent,directories,names in os.walk(stage):
    directories[:]=[name for name in directories if name not in excluded]
    for name in names:
        source=Path(parent)/name
        if source.suffix in {'.pyc','.tmp'} or source.name.startswith('before-') or source.name=='runtime-bundle.tgz':
            continue
        copy(source,'deployed/'+source.relative_to(stage).as_posix())
(target/'manifest.json').write_text(json.dumps({'schema':'mini-drop.delivery-evidence.v1',
    'scope':'Default engineering acceptance; regrading three frozen live records, not a fresh 21-case campaign. Historical strict scores and pinned evidence unchanged.',
    'files':manifest},indent=2),encoding='utf-8')
print(json.dumps({'archived_files':len(manifest),'target':str(target)}))
