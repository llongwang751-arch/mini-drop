from pathlib import Path
import hashlib,json,shutil,zipfile
root=Path(__file__).resolve().parent
dest=Path('reports/quality/performance-fix-20260930');dest.mkdir(exist_ok=True)
def copy(source,name):
 target=dest/name;target.parent.mkdir(parents=True,exist_ok=True)
 if target.exists():
  assert target.read_bytes()==source.read_bytes(),name
  return
 shutil.copyfile(source,target)
for name in ['targeted.xml','targeted-after.xml','targeted-final.xml','python-full.xml','python-full-branches.xml','python-full-cache.xml','python-full-cache-empty.xml','cache-empty-targeted.xml','office-three-phase.json','office-three-phase-branches.json','ci-run.json','ci-branches-run.json','manifest.json','platform-deployment.json','office-deployment.json']:
 copy(root/name,"initial-observer-ci-run.json" if name=="ci-run.json" else name)
logs=Path('output/quality/ci-validation-20260927')
for name in ['toast-before.log','toast-after-fixed.log']:
 source=root/name
 if not source.exists():
  matches=list(Path('output').rglob(name));assert len(matches)==1;source=matches[0]
 copy(source,name)
for name in ['web-cache-full.log','web-cache-serial.log','web-cache-final.log','web-cache-targeted-fixed.log','web-cache-verified.log','web-cache-boundary.log','web-cache-build-deployed.log']:
 copy(root/name,name)
for sub in ['branches','cache']:
 for name in ['manifest.json','platform-deployment.json','office-deployment.json','ci-run.json']:
  copy(root/sub/name,sub+'/'+name)
for name in ['source-verification.json','office-three-phase-cache-1.json','office-three-phase-cache-2.json']:
 copy(root/'cache'/name,'cache/'+name)
for folder,snapshot,targetname in [('before-quick-valid','before-source','load-before-short'),('after-quick','after-source','load-after-short'),('after-hour','after-source','load-interrupted-hour')]:
 source=root/folder;target=dest/targetname;target.mkdir()
 for name in ['report.json','report.html','interruption.json']:
  if (source/name).exists():shutil.copyfile(source/name,target/name)
 with zipfile.ZipFile(target/'evidence.zip','w',zipfile.ZIP_DEFLATED) as z:
  for f in source.iterdir():
   if f.suffix in ['.json','.jsonl']:z.write(f,f.name)
  for name in ['scripts/run_load_endurance.py','scripts/run_distributed_endurance.py','scripts/process_resource_monitor.py','demo/rag_service/app.py']:
   z.write(root/snapshot/name,'source/'+name)
print(json.dumps({'files':len([x for x in dest.rglob('*') if x.is_file()]),'source':str(root)}))
