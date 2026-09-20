"""Read-only browser verification; credentials stay in parent/child memory."""
import os
from pathlib import Path
import subprocess
import sys

code = '''import json,subprocess
x=json.loads(subprocess.check_output(['docker','inspect','mini-drop-control-apiserver-1']))[0]
e=dict(v.split('=',1) for v in x['Config']['Env'] if '=' in v)
print(e['MINI_DROP_API_KEY'])
'''
if __name__ == '__main__':
    key = subprocess.check_output(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8', 'root@120.24.187.205', 'python3', '-'], input=code.encode()).decode().strip()
    if not key:
        raise SystemExit('Missing cloud API credential')
    env = {**os.environ, 'MINI_DROP_API_KEY': key, 'MINI_DROP_BROWSER_BASE_URL': 'https://120.24.187.205'}
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', str(root/'scripts/verify_local_sre_browser.mjs'), *sys.argv[1:]], env=env, cwd=root)
    raise SystemExit(result.returncode)
