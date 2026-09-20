"""Control-host wrapper: load existing API credentials in memory and verify TLS."""
from pathlib import Path
import json
import os
import subprocess
import sys

from verify_local_sre import main

if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    if root.parent != Path('/opt/mini-drop-releases'):
        raise SystemExit('Run on the deployed Control host.')
    obj = json.loads(subprocess.check_output(['docker', 'inspect', 'mini-drop-control-apiserver-1']))[0]
    env = dict(item.split('=', 1) for item in obj['Config']['Env'] if '=' in item)
    os.environ['MINI_DROP_API_KEY'] = env['MINI_DROP_API_KEY']
    cert = next(m['Source'] for m in obj['Mounts'] if m['Destination'] == '/certs')
    os.environ['SSL_CERT_FILE'] = str(Path(cert) / 'ca.crt')
    sys.argv[1:1] = ['--deployment', 'cloud', '--base-url', 'https://120.24.187.205']
    raise SystemExit(main())
