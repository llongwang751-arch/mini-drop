"""Seal exact public evidence bytes without staging unrelated workspace output."""
from pathlib import Path
import hashlib
import json
import subprocess

folder = Path('reports/quality/performance-fix-20260930')
manifest = folder / 'evidence-manifest.json'
paths = sorted(p for p in folder.rglob('*') if p.is_file() and p != manifest)
entries = [{'path': p.relative_to(folder).as_posix(), 'bytes': p.stat().st_size,
            'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]
encoded = json.dumps(entries, ensure_ascii=False, indent=2).encode('utf-8')
if manifest.exists():
    assert manifest.read_bytes() == encoded, 'existing sealed evidence differs'
else:
    manifest.write_bytes(encoded)
subprocess.run(['git', 'add', '-f', '--', *[p.as_posix() for p in paths], manifest.as_posix()], check=True)
for path, entry in zip(paths, entries):
    indexed = subprocess.check_output(['git', 'show', ':' + path.as_posix()])
    assert len(indexed) == entry['bytes'], path
    assert hashlib.sha256(indexed).hexdigest() == entry['sha256'], path
print(json.dumps({'verified_worktree_and_index_files': len(paths), 'bytes': sum(e['bytes'] for e in entries)}))
