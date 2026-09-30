from pathlib import Path
import subprocess
import json

stage=Path(__file__).resolve().parent
tag=(stage/'release-tag.txt').read_text().strip()
manifest=json.loads((stage/'manifest.json').read_text())
run=json.loads((stage/'ci-run.json').read_text())
assert run['head_sha']==manifest['git_head'] and run['conclusion']=='success' and run['status']=='completed'
ssh=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','root@120.24.187.205']
subprocess.run(['scp','-q','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',str(stage/'runtime-bundle.tgz'),'root@120.24.187.205:/root/performance-'+tag+'.tgz'],check=True,timeout=90)
code='''from pathlib import Path
import tarfile,subprocess
tag=%r
root=Path('/root/incoming-'+tag)
assert not root.exists();root.mkdir(mode=0o700)
with tarfile.open('/root/performance-'+tag+'.tgz') as archive:
    for member in archive.getmembers():
        assert member.isfile() and (root/member.name).resolve().is_relative_to(root)
    archive.extractall(root)
subprocess.run(['python3',str(root/'deploy_runtime.py')],check=True)
'''%tag
r=subprocess.run(ssh+['python3 -'],input=code,text=True,encoding='utf-8',capture_output=True,timeout=360)
(stage/'platform-deployment.log').write_text(r.stdout+r.stderr,encoding='utf-8')
if r.returncode:raise RuntimeError('Platform activation failed; retained log/rollback status must be reviewed')
summary=json.loads(next(line for line in reversed(r.stdout.splitlines()) if line.startswith('{"release":')))
(stage/'platform-deployment.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps(summary))
