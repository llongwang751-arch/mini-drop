"""Ephemeral PostgreSQL test database, tmpfs only; never touch production DB."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4

stage = Path(__file__).resolve().parent
name = 'mini-drop-test-skill-race-' + uuid4().hex[:10]
password = secrets.token_urlsafe(24)
ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
       '-o', 'ConnectTimeout=15', 'root@120.24.187.205']

def remote(code):
    r = subprocess.run(ssh + ['python3 -'], input=code, text=True, encoding='utf-8',
                       capture_output=True, timeout=90)
    if r.returncode:
        print(r.stderr.replace(password, '[REDACTED]')[-900:])
        raise RuntimeError('Isolated PostgreSQL SSH operation failed')
    return r.stdout

tunnel = None
try:
    code = '''import json,subprocess,time
NAME = %r
PASSWORD = %r
obj=json.loads(subprocess.check_output(['docker','inspect','mini-drop-control-postgres-1']))[0]
subprocess.run(['docker','run','-d','--name',NAME,'--label','mini-drop-purpose=isolated-skill-race-test',
 '--memory','512m','--cpus','1','--tmpfs','/var/lib/postgresql/data:rw,size=256m',
 '--publish','127.0.0.1::5432','--env','POSTGRES_USER=mini_drop_test',
 '--env','POSTGRES_DB=mini_drop_test','--env','POSTGRES_PASSWORD='+PASSWORD,obj['Image']],check=True,capture_output=True)
for _ in range(40):
 r=subprocess.run(['docker','exec',NAME,'pg_isready','-U','mini_drop_test','-d','mini_drop_test'],capture_output=True)
 if r.returncode==0:break
 time.sleep(.5)
else:raise RuntimeError('test postgres not ready')
d=json.loads(subprocess.check_output(['docker','inspect',NAME]))[0]
assert '/var/lib/postgresql/data' in d['HostConfig']['Tmpfs']
assert subprocess.check_output(['docker','exec',NAME,'stat','-f','-c','%%T','/var/lib/postgresql/data'],text=True).strip()=='tmpfs'
print(json.dumps({'port':int(d['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort']),'image':obj['Image']}))
''' % (name, password)
    metadata = json.loads(remote(code))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        local_port = sock.getsockname()[1]
    tunnel = subprocess.Popen(ssh + ['-o', 'ExitOnForwardFailure=yes', '-N', '-L',
        f'127.0.0.1:{local_port}:127.0.0.1:{metadata["port"]}'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for _ in range(40):
        try:
            with socket.create_connection(('127.0.0.1', local_port), timeout=.5):
                break
        except OSError:
            if tunnel.poll() is not None:
                raise RuntimeError('test tunnel exited')
            time.sleep(.2)
    else:
        raise RuntimeError('test tunnel not ready')
    env = dict(os.environ, RUN_POSTGRES_TESTS='1', PYTHONIOENCODING='utf-8',
        MINI_DROP_TEST_POSTGRES_URL=f'postgresql+psycopg://mini_drop_test:{password}@127.0.0.1:{local_port}/mini_drop_test')
    label = sys.argv[1]
    assert label in {'before', 'after'}
    r = subprocess.run([sys.executable, '-m', 'pytest',
        'tests/test_drop_insight_report_effects_postgres.py::test_postgres_skill_activation_serializes_competing_planners',
        '-q', '--tb=short', f'--junitxml={stage}/postgres-race-{label}.xml'],
        env=env, text=True, encoding='utf-8', capture_output=True, timeout=90)
    log = (r.stdout + r.stderr).replace(password, '[REDACTED]')
    (stage / f'postgres-race-{label}.log').write_text(log, encoding='utf-8')
    print(json.dumps({'phase': label, 'test_exit': r.returncode, 'postgres_image': metadata['image'],
                      'database': 'dedicated mini_drop_test, isolated tmpfs container'}))
    print(log[-1800:])
finally:
    if tunnel is not None:
        tunnel.terminate()
        tunnel.wait(timeout=15)
    cleanup = remote("import subprocess; subprocess.run(['docker','rm','-f'," + repr(name) + "],check=True,capture_output=True);print('isolated test container removed')")
    print(cleanup.strip())
