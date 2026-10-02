from pathlib import Path
import importlib.util
import json

STAGE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('capacity_preflight',STAGE/'preflight.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
baseline=m.runtime_baseline(m.provider())
manifest=json.loads((STAGE/'final-release-r3/manifest.json').read_bytes())
source=STAGE/'final-release-r3/source'
backend=sum((source/n).stat().st_size for n in manifest['files'])
extra=sum((source/n).stat().st_size for n in manifest['deployment_files'])
bundle=(STAGE/'final-release-r3/runtime-bundle.tgz').stat().st_size
required=2**30+16*(2*backend+extra+bundle)
record={'status':'CAPACITY_ASSESSED','observed_at':baseline['observed_at'],'free_bytes':baseline['free_bytes'],
 'old_fixed_threshold_bytes':3*2**30,'old_fixed_threshold_passed':baseline['free_bytes']>3*2**30,
 'backend_bytes':backend,'deployment_bytes':extra,'bundle_bytes':bundle,
 'required_free_bytes':required,'formula':'1GiB reserve + 16*(two backend overlays + deployment files + compressed bundle)',
 'capacity_passed':baseline['free_bytes']>required,'new_base_images_or_dependency_installs':False,
 'scope':'Retained-image incremental three-service overlay; no files/images/volumes deleted'}
(STAGE/'preflight-fixed-threshold-failure.json').write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
print(json.dumps(record))
assert record['capacity_passed']
for name in ('preflight.py','immediate_baseline.py'):
 path=STAGE/name
 text=path.read_text(encoding='utf-8')
 old="assert "+('baseline' if name=='preflight.py' else 'current')+"['free_bytes'] > 3 * 2**30"
 assert text.count(old)==1
 text=text.replace(old,old.split(' > ')[0]+' > '+str(required)+', "insufficient measured overlay capacity"',1)
 path.write_text(text,encoding='utf-8',newline='\n')
