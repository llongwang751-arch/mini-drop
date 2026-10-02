"""Publish only the focused documentation generators and read-only reviews."""
from pathlib import Path
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).parent
DEST = ROOT / 'reports/quality/planning-retrieval-v2-20261002/publication'
assert not DEST.exists()
assert json.loads((STAGE / 'final-document-review.json').read_bytes())['status'] == 'VERIFIED'
post = json.loads((STAGE / 'final-readonly-document-audit-postdeployment.json').read_bytes())
names = [
    'finalize_delivery_docs.py', 'prepare_final_generator.py', 'stage_final_delivery.py',
    'build_final_evidence_manifest.py', 'archive_final_delivery_materials.py',
    'final-document-source-map.json', 'final-generator-source-map.json',
    'final-document-review.json', 'final-readonly-document-audit.json',
    'final-readonly-document-audit-postdeployment.json',
]
DEST.mkdir(parents=True)
rows = []
for name in names:
    source = STAGE / name
    target = DEST / name
    shutil.copy2(source, target)
    raw = target.read_bytes()
    assert raw == source.read_bytes()
    rows.append({'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
(DEST / 'archive-manifest.json').write_text(json.dumps({
    'schema': 'mini-drop.focused-document-publication-archive.v1',
    'status': 'VERIFIED', 'source_head': '73b4b18ad83553a5012a0025dfb78bb21ff8dc7f',
    'scope': 'Focused clean candidates, generator source, actual deployment review and immutable source/evaluation audit. Original audit labels reflect the old task-message baseline; postdeployment review explicitly corrects that interpretation.',
    'excluded': ['credentials', 'private env', 'personal resumes', 'dirty unrelated source', 'private database/checkpoint contents'],
    'files': rows}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'status': 'VERIFIED', 'files': len(rows)}))
