from pathlib import Path
import hashlib
import json
import subprocess
import tarfile

stage=Path(__file__).resolve().parent
release=stage/'release'
m=json.loads((release/'manifest.json').read_text(encoding='utf-8'))
(release/'manifest-pre-contract.json').write_bytes((release/'manifest.json').read_bytes())
head=subprocess.check_output(['git','rev-parse','HEAD']).decode().strip()
for name,sha in m['files'].items():
    assert hashlib.sha256(subprocess.check_output(['git','show',head+':'+name])).hexdigest()==sha
name='docs/contracts/openapi.v1.json';raw=subprocess.check_output(['git','show',head+':'+name])
dst=release/'source'/name;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(raw)
m['deployment_files'][name]=hashlib.sha256(raw).hexdigest()
m['git_head']=head
(release/'manifest.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
(release/'source-head.txt').write_text(head)
with tarfile.open(release/'runtime-bundle.tgz','w:gz') as archive:
    archive.add(release/'manifest.json',arcname='manifest.json')
    archive.add(release/'deploy_runtime.py',arcname='deploy_runtime.py')
    for name in [*m['files'],*m['deployment_files']]:archive.add(release/'source'/name,arcname='source/'+name)
print(json.dumps({'head':head,'runtime_files':len(m['files'])}))
