from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import sys
from urllib.parse import quote

sys.path.insert(0, str(Path.cwd()))
from scripts.evaluate_performance_localization import evaluate_localization_reports

parser = argparse.ArgumentParser()
parser.add_argument('campaign', type=Path)
parser.add_argument('destination', type=Path)
args = parser.parse_args()
args.destination.mkdir(exist_ok=False)
spec = importlib.util.spec_from_file_location('p', Path('output/acceptance/deployment-20260930/run_strict.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
client = p.authenticated_client()
client.proxy_mode = 'direct'
campaign = json.loads(args.campaign.read_text(encoding='utf-8'))
case_dir = args.campaign.parent / (args.campaign.stem + '-cases')
results = []
for summary in campaign['results']:
    scenario = summary['scenario_id']
    case = json.loads((case_dir / (scenario + '.json')).read_text(encoding='utf-8'))
    records = case.get('records') or {}
    grade = evaluate_localization_reports(scenario, records.get('reports', []))
    observations = [r.get('verification', {}).get('observation_verification', {}) for r in records.get('reports', [])]
    observation = max(observations, key=lambda x: x.get('checked_ratio', 0), default={})
    grade.update(diagnosis_id=case.get('diagnosis_id'), lineage_verified=case['lineage_verified'],
        cleanup_verified=case['cleanup_verified'], session_drained=case['session_drained'],
        root_cause_accepted=case['root_cause_accepted'], observation=observation, raw_downloads=[])
    for task in records.get('tasks', []):
        for artifact in task.get('artifacts', []):
            tid, kind = task['task_id'], artifact['artifact_type']
            raw = client.request_raw('GET', '/api/tasks/' + quote(tid, safe='') + '/artifacts/' + quote(kind, safe='') + '/download')
            digest = hashlib.sha256(raw).hexdigest()
            assert digest == artifact['sha256'], (tid, kind)
            path = args.destination / (tid + '-' + kind + '.raw')
            if path.exists():
                assert path.read_bytes() == raw
            else:
                path.write_bytes(raw)
            grade['raw_downloads'].append({'task_id': tid, 'artifact_type': kind, 'sha256': digest, 'bytes': len(raw)})
    integrity = bool(grade['lineage_verified'] and grade['cleanup_verified'] and grade['session_drained'] and grade['raw_downloads'])
    grade['integrity_verified'] = integrity
    grade['acceptance_passed'] = integrity and grade['localization_accepted']
    grade['measurement_checked'] = integrity and observation.get('checked_ratio') == 1
    results.append(grade)
final = {'schema': 'mini-drop.performance-localization-acceptance.v1', 'provenance': campaign['provenance'],
    'selected_count': len(results), 'localized_count': sum(r['localization_accepted'] for r in results),
    'passed_count': sum(r['acceptance_passed'] for r in results), 'results': results,
    'boundary': 'Observed paths only; old 21 RCA grades unchanged; no causal or same-load fix claim.'}
(args.destination / 'localization-acceptance.json').write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'localized': final['localized_count'], 'integrity': sum(r['integrity_verified'] for r in results),
                  'raw_downloads': sum(len(r['raw_downloads']) for r in results)}))
