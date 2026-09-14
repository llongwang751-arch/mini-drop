"""Install independent upstream applications on the two existing Workers."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'output/lightweight-business'
ROWS=[]
OPTS=['-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10']

def remote(host,code):
    result=subprocess.run(['ssh',*OPTS,'ubuntu@'+host,'sudo -n python3 -'],input=code,text=True,encoding='utf-8',capture_output=True)
    if result.returncode:
        # Scripts deliberately never include secrets in exceptions or output.
        print(result.stderr[-3000:]);raise RuntimeError('Worker setup failed: '+host)
    return result.stdout

def deploy(row):
    if row['kind']=='binary':
        subprocess.run(['scp','-q',*OPTS,str(OUT/'upstream'/row['id']/row['binary']),f"ubuntu@{row['host']}:/tmp/mini-drop-business-{row['id']}"],check=True)
    code=r"""
import hashlib,json,os,pathlib,pwd,secrets,subprocess,time,urllib.request
ROW=__ROW__
name='mini-drop-business-'+ROW['id'];root=pathlib.Path('/opt/mini-drop-business')/ROW['id']/ROW['version']
state=pathlib.Path('/var/lib/mini-drop-business')/ROW['id'];conf=pathlib.Path('/etc/mini-drop-business')
def run(args,**kw):return subprocess.check_output(args,text=True,stderr=subprocess.PIPE,**kw).strip()
root.mkdir(parents=True,exist_ok=True);state.mkdir(parents=True,exist_ok=True);conf.mkdir(mode=0o700,exist_ok=True)
try:user=pwd.getpwnam('mini-drop-business')
except KeyError:
 subprocess.run(['useradd','--system','--home-dir','/nonexistent','--shell','/usr/sbin/nologin','mini-drop-business'],check=True)
 user=pwd.getpwnam('mini-drop-business')
account=conf/(ROW['id']+'-account.json')
if not account.exists():
 account.write_text(json.dumps({'username':'mini-drop-demo','password':secrets.token_urlsafe(24)}));account.chmod(0o600)
credentials=json.loads(account.read_text())
config_changed=False
if ROW['kind']=='binary':
 binary=pathlib.Path('/tmp/mini-drop-business-'+ROW['id'])
 assert hashlib.sha256(binary.read_bytes()).hexdigest()==ROW['binary_sha256']
 dest=root/ROW['binary']
 if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest()!=ROW['binary_sha256']:
  incoming=dest.with_suffix('.incoming');incoming.write_bytes(binary.read_bytes());incoming.chmod(0o755);os.replace(incoming,dest)
  config_changed=True
 os.chown(state,user.pw_uid,user.pw_gid)
 if ROW['id']=='memos':
  command=f"{dest} --addr 127.0.0.1 --port {ROW['port']} --data {state}"
 elif ROW['id']=='files':
  data=state/'files';data.mkdir(exist_ok=True);os.chown(data,user.pw_uid,user.pw_gid)
  db=state/'filebrowser.db'
  if not db.exists():
   run([str(dest),'config','init','--database',str(db),'--root',str(data),'--address','127.0.0.1','--port',str(ROW['port'])])
   run([str(dest),'users','add',credentials['username'],credentials['password'],'--perm.admin','--database',str(db)])
   os.chown(db,user.pw_uid,user.pw_gid)
  command=f"{dest} --database {db}"
 else:
  cfg=state/'server.yml'
  value=f'base-url: {ROW["entry_url"].rstrip("/")}\nlisten-http: 127.0.0.1:{ROW["port"]}\ncache-file: {state}/cache.db\nauth-file: {state}/auth.db\nauth-default-access: deny-all\nbehind-proxy: true\ncache-duration: 1h\ncache-batch-size: 0\nenable-login: true\nenable-signup: false\nrequire-login: true\n'
  config_changed=config_changed or not cfg.exists() or cfg.read_text()!=value
  cfg.write_text(value)
  os.chown(cfg,user.pw_uid,user.pw_gid)
  for p in state.iterdir():os.chown(p,user.pw_uid,user.pw_gid)
  command=f"{dest} serve --config {cfg}"
 unit=f'''[Unit]
Description=Mini-Drop independent business: {ROW['id']}
After=network.target
[Service]
User=mini-drop-business
Group=mini-drop-business
WorkingDirectory={state}
ExecStart={command}
Restart=always
RestartSec=5
MemoryMax={ROW['memory_mb']}M
CPUQuota=60%
TasksMax=96
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={state}
[Install]
WantedBy=multi-user.target
'''
else:
 # Container content remains upstream. Dedicated cgroup gives the native
 # Agent a stable service identity without storing Docker IDs in inventory.
 image=ROW['image'];run(['docker','pull',image])
 info=json.loads(run(['docker','image','inspect',image]))[0]
 ROW['image_id']=info['Id'];ROW['repo_digests']=info.get('RepoDigests',[])
 original=run(['docker','run','--rm','--entrypoint','cat',ROW['image_id'],'/etc/linkding/uwsgi.ini'])
 assert 'processes = 2' in original
 worker_config=conf/'linkding-uwsgi.ini'
 value=original.replace('processes = 2','processes = 1')+'\nmaster = true\n'
 config_changed=not worker_config.exists() or worker_config.read_text()!=value
 worker_config.write_text(value)
 original_boot=run(['docker','run','--rm','--entrypoint','cat',ROW['image_id'],'/etc/linkding/bootstrap.sh'])
 assert 'exec uwsgi --http ' in original_boot
 boot=conf/'linkding-bootstrap.sh';value=original_boot.replace('exec uwsgi --http ','exec uwsgi --http-socket ')+'\n'
 config_changed=config_changed or not boot.exists() or boot.read_text()!=value
 boot.write_text(value);boot.chmod(0o755)
 env=conf/'linkding.env'
 value='LD_SUPERUSER_NAME='+credentials['username']+'\nLD_SUPERUSER_PASSWORD='+credentials['password']+'\nLD_DISABLE_BACKGROUND_TASKS=False\nLD_ENABLE_AUTH_PROXY=False\nLD_CSRF_TRUSTED_ORIGINS='+ROW['entry_url'].rstrip('/')+'\n'
 config_changed=config_changed or not env.exists() or env.read_text()!=value
 env.write_text(value)
 env.chmod(0o600)
 unit=f'''[Unit]
Description=Mini-Drop independent business: linkding
After=docker.service
Requires=docker.service
[Service]
Type=simple
ExecStart=/usr/bin/docker run --name {name} --rm --cgroup-parent={name}.slice --memory={ROW['memory_mb']}m --cpus=0.6 --pids-limit=96 --env-file={env} -p 127.0.0.1:{ROW['port']}:9090 -v {state}:/etc/linkding/data -v {worker_config}:/etc/linkding/uwsgi.ini:ro -v {boot}:/etc/linkding/bootstrap.sh:ro {ROW['image_id']}
ExecStop=/usr/bin/docker stop -t 15 {name}
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
'''
unit_path=pathlib.Path('/etc/systemd/system')/(name+'.service')
old=unit_path.read_text() if unit_path.exists() else None
if old!=unit or config_changed:
 unit_path.write_text(unit);run(['systemctl','daemon-reload']);run(['systemctl','enable',name]);run(['systemctl','restart',name])
else:run(['systemctl','start',name])
time.sleep(2)
status=run(['systemctl','is-active',name]);assert status=='active'
if ROW['id']=='ntfy':
 run([str(dest),'user','--config',str(cfg),'add','--ignore-exists','--role=admin',credentials['username']],env={**os.environ,'NTFY_PASSWORD':credentials['password']})
ROW['systemd_service']=name+'.service';ROW['state']='started_private_loopback';ROW['unit_sha256']=hashlib.sha256(unit.encode()).hexdigest()
(root/'deployment.json').write_text(json.dumps(ROW,indent=2))
print(json.dumps(ROW))
""".replace('__ROW__',repr(row))
    result=json.loads(remote(row['host'],code).strip().splitlines()[-1])
    (OUT/(row['id']+'-deployment.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(row['id']+' private service installed',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--work-dir',type=Path,default=OUT);parser.add_argument('services',nargs='*',choices=['memos','files','linkding','ntfy']);args=parser.parse_args();OUT=args.work_dir.resolve()
    ROWS=json.loads((OUT/'upstream-lock.json').read_text(encoding='utf-8'));selected=args.services
    catalog={r['id']:r for r in json.loads(Path(__file__).with_name('catalog.json').read_text(encoding='utf-8'))['services']}
    locked={r['id']:r for r in json.loads(Path(__file__).with_name('artifact-lock.json').read_text(encoding='utf-8'))['artifacts']}
    for receipt in ROWS:
        entry=catalog[receipt['id']];artifact=locked[receipt['id']]
        assert receipt['version']==entry['version']==artifact['version'], 'Artifact receipt and catalog version mismatch'
        row={**entry,**artifact}
        if row['kind']=='container':row['image']=artifact['image_digest']
        if not selected or row['id'] in selected:deploy(row)
