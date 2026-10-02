from pathlib import Path
import importlib.util
import json

stage = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('p', Path('output/acceptance/deployment-20260930/run_strict.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
source = Path('scripts/verify_go_io_window.py').read_text(encoding='utf-8')
image = json.loads((stage / 'go-runtime-verification.json').read_text())['image']
code = '''from pathlib import Path
import json,subprocess
source=%r
image=%r
root=Path('/root/mini-drop-go-io-capacity-before-20261001')
assert not root.exists();root.mkdir(mode=0o700)
(root/'verify.py').write_text(source)
r=subprocess.run(['python3',str(root/'verify.py'),'--image',image,'--output',str(root/'evidence')],text=True,capture_output=True,timeout=75)
result=json.loads((root/'evidence/report.json').read_text())
assert r.returncode==1 and not result['passed'] and result['cleanup_verified']
print(json.dumps({'exit_code':r.returncode,'report':result,'log':r.stdout+r.stderr}))
''' % (source, image)
result = json.loads(p.remote(code))
(stage / 'io-capacity-before.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps({'expected_failure': True, 'operations': result['report']['observations'][-1]['io_operations'],
                  'failures': result['report']['observations'][-1]['io_failures'], 'cleanup': result['report']['cleanup_verified']}))
