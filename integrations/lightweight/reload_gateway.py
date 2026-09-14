"""Publish generated gateway config with syntax validation and graceful reload.

Run from the repository on the operator workstation with existing SSH access.
Does not restart business processes or change the API/DB/Python worker images.
"""
import base64,json,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def reload():
    files=['integrations/lightweight/generate_gateway.py','deploy/nginx/business-apps.conf']
    payload={name:base64.b64encode((ROOT/name).read_bytes()).decode() for name in files}
    code='import base64,json,pathlib,subprocess,hashlib\nfiles='+repr(payload)+'''
root=pathlib.Path('/opt/mini-drop-current').resolve()
assert root.parent==pathlib.Path('/opt/mini-drop-releases')
previous={n:(root/n).read_bytes() for n in files}
try:
 for n,v in files.items():(root/n).write_bytes(base64.b64decode(v))
 subprocess.run(['docker','exec','mini-drop-control-web-1','nginx','-t'],capture_output=True,check=True)
 subprocess.run(['docker','exec','mini-drop-control-web-1','nginx','-s','reload'],capture_output=True,check=True)
except Exception:
 for n,v in previous.items():(root/n).write_bytes(v)
 raise
print(json.dumps({'release':str(root),'nginx_reload':True,'generated_config_sha256':hashlib.sha256((root/'deploy/nginx/business-apps.conf').read_bytes()).hexdigest()}))
'''
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','root@120.24.187.205','python3 -'],input=code,text=True,encoding='utf-8',capture_output=True,check=True)
    print(result.stdout.strip())

if __name__=='__main__':reload()
