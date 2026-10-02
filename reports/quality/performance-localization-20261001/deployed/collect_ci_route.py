from pathlib import Path
import hashlib
import importlib.util
import io
import json
import zipfile

ROOT=Path.cwd();stage=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('g',ROOT/'output/quality/ci-validation-20260927/github_ci.py')
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
client=g.session();client.trust_env=False
run=json.loads((stage/'release-r7/ci-run.json').read_text())
assert run['status']=='completed' and run['conclusion']=='success'
base='https://api.github.com/repos/'+g.REPO
r=client.get(base+f"/actions/runs/{run['id']}/artifacts",params={'per_page':100},timeout=30);r.raise_for_status()
artifacts=r.json()['artifacts']
output=stage/'ci-artifacts-route';output.mkdir(exist_ok=False)
(output/'artifacts.json').write_text(json.dumps(artifacts,indent=2),encoding='utf-8')
result=[]
for artifact in artifacts:
    if not any(word in artifact['name'].lower() for word in ('python','postgres','web','hotspot')):continue
    r=client.get(artifact['archive_download_url'],timeout=60);r.raise_for_status();raw=r.content
    name=artifact['name']
    assert Path(name).name==name
    (output/(name+'.zip')).write_bytes(raw)
    selected=[]
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for member in archive.namelist():
            if not member.endswith(('.xml','runtime.json','report.json','coverage.json')):continue
            target=(output/name/member).resolve();assert target.is_relative_to(output.resolve())
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(member))
            selected.append(member)
    result.append({'name':name,'sha256':hashlib.sha256(raw).hexdigest(),'files':selected})
(output/'download-manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
