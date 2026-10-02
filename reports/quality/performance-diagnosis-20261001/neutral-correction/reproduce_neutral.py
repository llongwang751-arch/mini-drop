from pathlib import Path
import hashlib
import json
import subprocess

root = Path.cwd()
stage = Path(__file__).resolve().parent
output = stage / 'neutral-reproduction'
output.mkdir(exist_ok=True)
before = subprocess.check_output(['git', 'show', '51228f313c5ce2514107498de7521e5787ef8678:web/src/utils/observationAssessment.js'])
after = (root / 'web/src/utils/observationAssessment.js').read_bytes()
for name, raw in [('before.mjs', before), ('after.mjs', after)]:
    (output / name).write_bytes(raw)
records = json.loads((stage / 'health-live/records.json').read_text(encoding='utf-8'))
evidence = next(x for x in records['evidence'] if x['classification']['decision'] == 'ACCEPT_NEUTRAL')
payload = json.dumps({'evidence': evidence, 'target': records['detail']['target']}, ensure_ascii=False).encode()
(output / 'input.json').write_bytes(payload)
(output / 'check.mjs').write_text('''import { readFileSync } from 'node:fs';
import { assessObservationWindow as before } from './before.mjs';
import { assessObservationWindow as after } from './after.mjs';
const input = JSON.parse(readFileSync(new URL('./input.json', import.meta.url)));
const result = { before: before(input.evidence, input.target), after: after(input.evidence, input.target) };
if(result.before.code !== 'INSUFFICIENT_OBSERVABILITY' || result.after.code !== 'NORMAL_OBSERVED') throw Error('Regression did not reproduce');
console.log(JSON.stringify(result));
''', encoding='utf-8')
result = json.loads(subprocess.check_output(['node', str(output / 'check.mjs')]))
result.update({'before_commit': '51228f313c5ce2514107498de7521e5787ef8678',
               'source_sha256': {name: hashlib.sha256(raw).hexdigest() for name, raw in [('before', before), ('after', after)]},
               'input_sha256': hashlib.sha256(payload).hexdigest(), 'diagnosis_id': records['detail']['diagnosis_id']})
(output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({key: result[key] for key in ['before', 'after', 'source_sha256']}, ensure_ascii=False))
