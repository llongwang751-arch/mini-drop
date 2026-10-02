from pathlib import Path
import subprocess,json
stage=Path('output/acceptance/security-cancel-20260930')
tag=(stage/'release-tag.txt').read_text()
code='''from pathlib import Path
import tarfile,subprocess
TAG=%r
stage=Path('/root/incoming-'+TAG)
assert not stage.exists()
stage.mkdir(mode=0o700)
with tarfile.open('/root/security-cancel-'+TAG+'.tgz') as archive:
    for member in archive.getmembers():
        target=(stage/member.name).resolve()
        assert target.is_relative_to(stage) and member.isfile(),member.name
    archive.extractall(stage)
subprocess.run(['python3',str(stage/'deploy_runtime.py')],check=True)
''' % tag
r=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','root@120.24.187.205','python3 -'],input=code,text=True,encoding='utf-8',capture_output=True,timeout=360)
(stage/'deployment-raw.log').write_text(r.stdout+r.stderr,encoding='utf-8')
if r.returncode:
    raise RuntimeError('Deployment failed; inspect retained log and rollback status')
summary=json.loads(next(line for line in reversed(r.stdout.splitlines()) if line.startswith('{"release":')))
(stage/'deployment.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps(summary))
