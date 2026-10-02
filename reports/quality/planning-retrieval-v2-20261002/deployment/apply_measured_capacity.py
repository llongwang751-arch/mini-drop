from pathlib import Path
import hashlib
import importlib.util
import json
import shutil

STAGE=Path(__file__).parent
def load(name):
 spec=importlib.util.spec_from_file_location('capacity_'+name,STAGE/(name+'.py'))
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
preflight=load('preflight')
program="import json,subprocess\nfrom pathlib import Path\np=Path('/opt/mini-drop-current').resolve()\nn=int(subprocess.check_output(['du','-sb',str(p)],text=True).split()[0])\nprint(json.dumps({'old_release':str(p),'old_release_bytes':n}))\n"
old=json.loads(preflight.provider().remote(program))
capacity=json.loads((STAGE/'preflight-fixed-threshold-failure.json').read_bytes())
capacity.update(old)
capacity['required_free_bytes']+=old['old_release_bytes']
capacity['formula']+=' + exact existing release copy size'
assert capacity['free_bytes']>capacity['required_free_bytes']
(STAGE/'measured-deployment-capacity.json').write_text(json.dumps(capacity,indent=2)+'\n',encoding='utf-8')
release=STAGE/'final-release-r3'
backup=release/'prepared-before-capacity'
assert not backup.exists();backup.mkdir()
for name in ('manifest.json','deploy_runtime.py','runtime-bundle.tgz'):
 shutil.copy2(release/name,backup/name)

# Re-render through the generator and its source contract, keeping prior bytes.
path=STAGE/'prepare_release.py'
text=path.read_text(encoding='utf-8')
anchor='    tree = ast.parse(result)'
assert text.count(anchor)==1
text=text.replace(anchor,"    disk_gate = \"    assert shutil.disk_usage('/').free > 3 * 2**30\"\n    assert result.count(disk_gate) == 1\n    result = result.replace(disk_gate, \"    assert shutil.disk_usage('/').free > json.loads((STAGE / 'manifest.json').read_text())['deployment_capacity']['required_free_bytes']\", 1)\n"+anchor,1)
path.write_text(text,encoding='utf-8',newline='\n')
prepare=load('prepare_release')
deployer=prepare.render_deployer()
(release/'deploy_runtime.py').write_text(deployer,encoding='utf-8',newline='\n')
manifest=json.loads((release/'manifest.json').read_bytes())
manifest['deployment_capacity']=capacity
manifest['deploy_runtime_sha256']=hashlib.sha256((release/'deploy_runtime.py').read_bytes()).hexdigest()
(release/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
for name in ('preflight.py','immediate_baseline.py'):
 path=STAGE/name
 text=path.read_text(encoding='utf-8')
 old_limit=str(capacity['required_free_bytes']-old['old_release_bytes'])
 assert old_limit in text
 path.write_text(text.replace(old_limit,str(capacity['required_free_bytes']),1),encoding='utf-8',newline='\n')
sha=prepare.write_bundle()
print(json.dumps({'measured_free_bytes':capacity['free_bytes'],'required_free_bytes':capacity['required_free_bytes'],'old_release_bytes':old['old_release_bytes'],'bundle_sha256':sha,'deleted_files':0}))
