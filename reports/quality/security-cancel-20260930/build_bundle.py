"""Bundle exact tracked runtime sources and built outputs; set ELF executable mode."""
from pathlib import Path
import subprocess,json,hashlib,tarfile
from datetime import datetime,timezone
stage=Path(__file__).resolve().parent
head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
paths=subprocess.check_output(['git','ls-files','server','analyzer','scripts'],text=True).splitlines()
others=subprocess.check_output(['git','ls-files','apiserver','web/src','web/package.json','web/package-lock.json','deploy/dockerfiles/apiserver.Dockerfile'],text=True).splitlines()
others += ['docs/'+n for n in ['PROJECT_CONTEXT.md','RESTART_HANDOFF.md','AI_DIAGNOSIS.md','REPLICATION.md','REMAINING_WORK_20260930.md','DEMO_WALKTHROUGH.md']]
others += [p.as_posix() for p in Path('web/dist').rglob('*') if p.is_file()]
files={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}
extra={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in others}
extra['deploy/bin/apiserver']=hashlib.sha256((stage/'apiserver').read_bytes()).hexdigest()
manifest={'git_head':head,'release_tag':tag,'fault_lab_agent_id':'control-interview-demo-agent','files':files,'deployment_files':extra}
(stage/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
(stage/'release-tag.txt').write_text(tag,encoding='ascii')
with tarfile.open(stage/'runtime-bundle.tgz','w:gz') as tar:
    tar.add(stage/'manifest.json',arcname='manifest.json')
    tar.add(stage/'deploy_runtime.py',arcname='deploy_runtime.py')
    for p in paths+others:
        tar.add(p,arcname='source/'+p)
    binary=tar.gettarinfo(stage/'apiserver',arcname='source/deploy/bin/apiserver')
    binary.mode=0o755
    with (stage/'apiserver').open('rb') as stream:tar.addfile(binary,stream)
with tarfile.open(stage/'runtime-bundle.tgz') as tar:
    assert tar.getmember('source/deploy/bin/apiserver').mode == 0o755
print(json.dumps({'tag':tag,'binary_sha256':extra['deploy/bin/apiserver'],'executable_mode':'0755'}))
