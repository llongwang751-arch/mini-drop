from pathlib import Path
import json

from server.app.drop_insight.observation_verifier import verify_performance_observation
from server.app.drop_insight.performance_criteria import performance_observation_plan
from server.app.drop_insight.evidence import EvidenceEnvelope

stage = Path(__file__).resolve().parent
rows = []
for sid, category in [('go-network-latency', 'NETWORK_DEGRADATION'), ('go-file-io', 'IO_LATENCY')]:
    records = json.loads((stage / 'strict-three-paths-cases' / (sid + '.json')).read_text(encoding='utf-8'))['records']
    plan = performance_observation_plan(category)
    evidence = [(row['role'], EvidenceEnvelope.model_validate(row['envelope'])) for row in records['evidence']]
    observation = verify_performance_observation(evidence, plan['expected'], plan['falsification'])
    rows.append({'scenario_id': sid, 'diagnosis_id': records['diagnosis']['diagnosis_id'],
                 'scope': 'Read-only replay with the declared numeric contract; original hypotheses/reports unchanged',
                 'plan': plan, 'observation': observation})
(stage / 'observation-input-replay.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps([{'scenario': r['scenario_id'], 'status': r['observation']['status'],
                   'checked': r['observation']['checked_ratio']} for r in rows]))
