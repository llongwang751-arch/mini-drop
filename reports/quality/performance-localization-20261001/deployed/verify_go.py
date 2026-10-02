from pathlib import Path
import importlib.util
import json

stage = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('p', Path('output/acceptance/deployment-20260930/run_strict.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
deployment = json.loads((stage / 'release-r5/platform-deployment.json').read_text())
manifest = json.loads((stage / 'release-r5/manifest.json').read_text())
sources = {n: digest for n, digest in manifest['deployment_files'].items() if n.startswith('demo/go-hotspot/')}
result = json.loads(p.remote('''import json, subprocess, hashlib
from pathlib import Path
sources=%r
release=Path(%r)
for name, digest in sources.items():
    assert hashlib.sha256((release/name).read_bytes()).hexdigest()==digest
obj=json.loads(subprocess.check_output(['docker','inspect','mini-drop-control-go-hotspot-1']))[0]
assert obj['Image']==%r and obj['State']['Health']['Status']=='healthy'
sha=subprocess.check_output(['docker','exec','mini-drop-control-go-hotspot-1','sha256sum','/usr/local/bin/go-hotspot']).decode().split()[0]
print(json.dumps({'image':obj['Image'],'binary_sha256':sha,'source_files_verified':len(sources),'health':'healthy'}))
''' % (sources, deployment['release'], deployment['images']['go-hotspot']['new'])))
snapshot = p.snapshot('go')
assert isinstance(snapshot['io_operation_duration_ms_total'], (int, float))
assert snapshot['io_operation_duration_ms_total'] >= 0
before = json.loads((stage / 'go-before.json').read_text())
assert snapshot['acceptance_process_identity']['container_id'] != before['acceptance_process_identity']['container_id']
result.update(source=manifest['git_head'], snapshot=snapshot)
(stage / 'go-runtime-verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in result.items() if k != 'snapshot'}))
