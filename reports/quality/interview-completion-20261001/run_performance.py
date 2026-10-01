from pathlib import Path
import hashlib
import importlib.util
import json
import sys

ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
STAGE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('provider',ROOT/'output/acceptance/deployment-20260930/run_strict.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
from scripts.run_engineering_diagnosis_acceptance import run_campaign
plan=json.loads((ROOT/'contracts/engineering_diagnosis.json').read_text(encoding='utf-8'))
contracts={s['scenario_id']:s for s in plan['scenarios']}
priority=['go-memory-growth','java-gc-pressure','java-lock-contention','queue-backlog',
    'memory-pressure','load-saturation','java-offheap-growth','cpp-lock-contention',
    'cpp-memory-growth','source-hotspot','cpu-hotspot','cpp-cpu-hotspot',
    'java-downstream-latency','cpp-downstream-latency','io-write-latency','java-file-io','cpp-file-io','noisy-neighbor']
c=p.authenticated_client();c.proxy_mode='direct'
provenance={'runtime':json.loads((STAGE/'runtime-before.json').read_text(encoding='utf-8'))['runtime'],
    'source_sha256':{'scripts/run_engineering_diagnosis_acceptance.py':hashlib.sha256((ROOT/'scripts/run_engineering_diagnosis_acceptance.py').read_bytes()).hexdigest()},
    'scope':'18 new isolated engineering trials, no repeated one-hour test, no old record changes'}
result=run_campaign(c,p.snapshot,STAGE/'performance-18.json',scenario_ids=priority,contracts=contracts,provenance=provenance)
print(json.dumps({'run_status':result['run_status'],'completed':result['completed_count'],'accepted':result['diagnosis_accepted']}))
