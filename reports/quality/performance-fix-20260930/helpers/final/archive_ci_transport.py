import hashlib,importlib.util,io,json,re,zipfile
from pathlib import Path
stage=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('ci',Path('output/quality/ci-validation-20260927/github_ci.py'))
ci=importlib.util.module_from_spec(spec);spec.loader.exec_module(ci)
c=ci.session();c.trust_env=False
dest=Path('reports/quality/performance-fix-20260930/transport-ci');dest.mkdir(parents=True,exist_ok=True)
run=json.loads((stage/'multi/ci-run.json').read_text());assert run['status']=='completed' and run['conclusion']=='success'
assert run['head_sha']=='52881ec2db0ad3622f00e9e55e5bff1e1fe71391'
def get(path):
 r=c.get('https://api.github.com/repos/'+ci.REPO+path,timeout=45);r.raise_for_status();return r.json()
jobs=get('/actions/runs/'+str(run['id'])+'/jobs');assert len(jobs['jobs'])==13 and all(j['conclusion']=='success' for j in jobs['jobs'])
arts=get('/actions/runs/'+str(run['id'])+'/artifacts')
for name,data in [('ci-run.json',run),('ci-jobs.json',jobs),('ci-artifacts.json',arts)]:
 target=dest/name;assert not target.exists();target.write_text(json.dumps(data,indent=2),encoding='utf-8')
entries=[]
for a in arts['artifacts']:
 if not a['name'].startswith(('python-quality-','postgres-concurrency-','business-measurements-','web-browser-')):continue
 r=c.get(a['archive_download_url'],timeout=90);r.raise_for_status();raw=r.content
 with zipfile.ZipFile(io.BytesIO(raw)) as z:assert z.testzip() is None
 name='ci-'+re.sub(r'-[0-9a-f]{40}(?:-\d+)?$','',a['name'])+'.zip'
 target=dest/name;assert not target.exists();target.write_bytes(raw)
 entries.append({'name':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
(dest/'ci-archive-manifest.json').write_text(json.dumps(entries,indent=2),encoding='utf-8')
print(json.dumps({'jobs':13,'artifacts':len(entries),'run':run['id'],'source':run['head_sha']}))
