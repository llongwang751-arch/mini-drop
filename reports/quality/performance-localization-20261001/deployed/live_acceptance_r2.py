from pathlib import Path
import hashlib
import importlib.util
import json
import sys
from urllib.parse import quote

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
stage = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('provider', ROOT / 'output/acceptance/deployment-20260930/run_strict.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
from scripts.run_fault_plaza_strict_acceptance import run_campaign
from scripts.evaluate_performance_localization import evaluate_localization_reports

client = p.authenticated_client()
client.proxy_mode = 'direct'
deployment = json.loads((stage / 'release-r5/platform-deployment.json').read_text())
result = run_campaign(client, p.snapshot, stage / 'strict-three-paths-r2.json',
    scenario_ids=['go-cpu-hotspot', 'go-network-latency', 'go-file-io'], deployment_provenance=deployment)
print('Fresh three-case campaign completed; grade with grade_campaign.py.', flush=True)
