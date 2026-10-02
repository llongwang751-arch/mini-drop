"""Seal the completed deployment; keep the earlier local/CI snapshot independently intact."""
from pathlib import Path
import hashlib
import json
import subprocess

folder=Path(__file__).resolve().parent
root=folder.parents[2]
manifest=folder/'delivery-manifest.json'
earlier=json.loads((folder/'evidence-manifest.json').read_text(encoding='utf-8'))
for entry in earlier:
    assert hashlib.sha256((folder/entry['path']).read_bytes()).hexdigest()==entry['sha256'],entry['path']
paths=sorted(p for p in folder.rglob('*') if p.is_file() and p!=manifest
             and '__pycache__' not in p.parts and p.suffix!='.pyc')
entries=[{'path':p.relative_to(folder).as_posix(),'bytes':p.stat().st_size,
          'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]
raw=json.dumps(entries,ensure_ascii=False,indent=2).encode('utf-8')
if manifest.exists():assert manifest.read_bytes()==raw, 'sealed delivery differs'
else:manifest.write_bytes(raw)
subprocess.run(['git','add','-f','--',*[p.relative_to(root).as_posix() for p in paths],manifest.relative_to(root).as_posix()],check=True,cwd=root)
for path,item in zip(paths,entries):
    indexed=subprocess.check_output(['git','show',':'+path.relative_to(root).as_posix()],cwd=root)
    assert hashlib.sha256(indexed).hexdigest()==item['sha256'],path
print(json.dumps({'verified_worktree_and_index_files':len(paths),'bytes':sum(x['bytes'] for x in entries),'earlier_snapshot_files_verified':len(earlier)}))
