from pathlib import Path
import importlib.util
import json

STAGE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('mount_order_preflight',STAGE/'preflight.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
before=json.loads((STAGE/'final-runtime-predeployment.json').read_bytes())
after=m.runtime_baseline(m.provider())
differences=[]
for name, current in after['containers'].items():
 prior=before['containers'][name]
 assert current['id']==prior['id'] and current['image']==prior['image'] and current['env_sha256']==prior['env_sha256']
 if current['mounts']!=prior['mounts']:
  assert sorted(current['mounts'],key=lambda row:row['Destination'])==sorted(prior['mounts'],key=lambda row:row['Destination']),name
  differences.append(name)
(STAGE/'preactivation-mount-order-failure.json').write_text(json.dumps({'status':'ORDER_ONLY_CONFIRMED','containers':differences,'all_mount_records_identical_when_sorted':True,'no_runtime_mutations':True,'observed_at':after['observed_at']},indent=2)+'\n',encoding='utf-8')
path=STAGE/'immediate_baseline.py'
text=path.read_text(encoding='utf-8')
old="            assert item[field] == previous[field], 'runtime changed before activation: ' + name + ':' + field"
assert text.count(old)==1
new="            current_value, previous_value = item[field], previous[field]\n            if field == 'mounts':\n                current_value = sorted(current_value, key=lambda row: row['Destination'])\n                previous_value = sorted(previous_value, key=lambda row: row['Destination'])\n            assert current_value == previous_value, 'runtime changed before activation: ' + name + ':' + field"
path.write_text(text.replace(old,new,1),encoding='utf-8',newline='\n')
print(json.dumps({'order_only_confirmed':True,'affected_container_count':len(differences)}))
