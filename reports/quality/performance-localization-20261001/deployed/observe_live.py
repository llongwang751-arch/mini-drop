from datetime import datetime, timezone
from pathlib import Path
import importlib.util
import json

stage = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('p', Path('output/acceptance/deployment-20260930/run_strict.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
c = p.authenticated_client()
c.proxy_mode = 'direct'
from scripts.verify_interview_demo import items_of
rows = items_of(c.request('GET', '/api/v2/diagnoses?limit=10'))
recent = [x for x in rows if x.get('created_at', '') >= '2026-10-01T09:01:14']
result = []
for row in recent:
    base = '/api/v2/diagnoses/' + row['diagnosis_id']
    reports = items_of(c.request('GET', base + '/reports'))
    calls = items_of(c.request('GET', base + '/tool-calls'))
    result.append({'diagnosis_id': row['diagnosis_id'], 'status': row['status'],
        'reports': [{k: r.get(k) for k in ('report_id', 'conclusion', 'verification')} for r in reports],
        'tools': [{k: t.get(k) for k in ('tool_name', 'status', 'task_id')} for t in calls]})
out = stage / ('live-status-' + datetime.now(timezone.utc).strftime('%H%M%S') + '.json')
out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps([{'id': r['diagnosis_id'], 'status': r['status'], 'tools': r['tools'],
    'reports': [{'status': x['verification'].get('status'),
    'localized': x['verification'].get('bottleneck_localization', {}).get('status')} for x in r['reports']]} for r in result]))
