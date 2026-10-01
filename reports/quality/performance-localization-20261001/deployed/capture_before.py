from pathlib import Path
import importlib.util
import json

stage = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('p', Path('output/acceptance/deployment-20260930/run_strict.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
c = p.authenticated_client()
c.proxy_mode = 'direct'
(stage / 'runtime-before.json').write_text(json.dumps(c.request('GET', '/api/v2/services'), indent=2), encoding='utf-8')
snapshot = p.snapshot('go')
(stage / 'go-before.json').write_text(json.dumps(snapshot, indent=2), encoding='utf-8')
print(json.dumps({'go_host_pid': snapshot['acceptance_process_identity']['host_pid'], 'fault_active': snapshot.get('fault_active')}))
