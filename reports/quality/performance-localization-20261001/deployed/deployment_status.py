import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('p', Path('output/acceptance/deployment-20260930/run_strict.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
print(p.remote('''import json,subprocess
x=json.loads(subprocess.check_output(['docker','inspect','mini-drop-control-go-hotspot-1','mini-drop-control-web-1','mini-drop-control-analyzer-1','mini-drop-control-diagnosis-worker-1']))
print(json.dumps([{'service':r['Name'],'image':r['Config']['Image'],'state':r['State']['Status'],'health':r['State'].get('Health',{}).get('Status')} for r in x]))
'''))
