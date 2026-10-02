"""Provisional progress; only completed buckets, never a completed-hour verdict."""
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path.cwd()))
from scripts.run_load_endurance import percentile

root = Path(__file__).resolve().parent / 'after-multi-hour'
rows = []
for line in (root / 'soak.jsonl').read_text(encoding='utf-8').splitlines():
    try:
        rows.append(json.loads(line))
    except json.JSONDecodeError:
        continue
complete = int(max(r['scheduled_offset_seconds'] for r in rows) // 30)
buckets = []
for i in range(complete):
    selected = [r for r in rows if int(r['scheduled_offset_seconds'] // 30) == i]
    buckets.append({'offset_seconds': i * 30,
                    'p95_ms': percentile([r['latency_ms'] for r in selected], .95),
                    'failed_requests': sum(not r['success'] or not r['quality_passed'] for r in selected)})
print(json.dumps({'provisional': True, 'recorded': len(rows), 'completed_windows': complete,
                  'failed_windows': [b for b in buckets if b['p95_ms'] > 200 or b['failed_requests'] > 1],
                  'failed_requests': sum(not r['success'] or not r['quality_passed'] for r in rows)}))
