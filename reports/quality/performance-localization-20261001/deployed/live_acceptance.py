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
deployment = json.loads((stage / 'release-r3/platform-deployment.json').read_text())
result = run_campaign(client, p.snapshot, stage / 'strict-three-paths.json',
    scenario_ids=['go-cpu-hotspot', 'go-network-latency', 'go-file-io'], deployment_provenance=deployment)
downloads = stage / 'raw-downloads'
downloads.mkdir(exist_ok=False)
grades = []
for summary in result['results']:
    scenario = summary['scenario_id']
    case = json.loads((stage / 'strict-three-paths-cases' / (scenario + '.json')).read_text())
    records = case.get('records') or {}
    grade = evaluate_localization_reports(scenario, records.get('reports', []))
    grade.update(diagnosis_id=case.get('diagnosis_id'), lineage_verified=case['lineage_verified'],
        cleanup_verified=case['cleanup_verified'], session_drained=case['session_drained'],
        root_cause_accepted=case['root_cause_accepted'], raw_downloads=[])
    for task in records.get('tasks', []):
        task_id = task['task_id']
        for artifact in task.get('artifacts', []):
            kind = artifact['artifact_type']
            raw = client.request_raw('GET', '/api/tasks/' + quote(task_id, safe='') + '/artifacts/' + quote(kind, safe='') + '/download')
            digest = hashlib.sha256(raw).hexdigest()
            assert digest == artifact['sha256'], (task_id, kind)
            path = downloads / (task_id + '-' + kind + '.raw')
            if path.exists():
                assert path.read_bytes() == raw
            else:
                path.write_bytes(raw)
            grade['raw_downloads'].append({'task_id': task_id, 'artifact_type': kind, 'sha256': digest, 'bytes': len(raw)})
    grade['acceptance_passed'] = bool(grade['localization_accepted'] and grade['lineage_verified']
        and grade['cleanup_verified'] and grade['session_drained'] and grade['raw_downloads'])
    grades.append(grade)
final = {'schema': 'mini-drop.performance-localization-acceptance.v1', 'deployment': deployment,
    'selected_count': 3, 'localized_count': sum(r['localization_accepted'] for r in grades),
    'passed_count': sum(r['acceptance_passed'] for r in grades), 'results': grades,
    'boundary': 'Three fresh observed paths; old 21 RCA grades unchanged; no causal or same-load fix claim.'}
(stage / 'localization-acceptance.json').write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'event': 'LOCALIZATION_FINISHED', 'localized': final['localized_count'], 'passed': final['passed_count']}), flush=True)
