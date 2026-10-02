from pathlib import Path
import json,hashlib
root=Path.cwd();clean=root/'.tmp-planning-retrieval-v2-source-r3';stage=root/'output/acceptance/planning-retrieval-v2-20261002';manifest=json.loads((clean/'benchmarks/retrieval/planning_retrieval_v2_manifest.json').read_bytes())
for path in sorted((stage/'hybrid-first-run/records').glob('*.json')):
 row=json.loads(path.read_bytes())
 for m in row['trace']['matches']:
  raw=(clean/m['document']).read_bytes();h=hashlib.sha256(raw).hexdigest();lf=hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()
  if m['content_hash']!=h:
   print(json.dumps({'case_id':row['case_id'],'knowledge_id':m['knowledge_id'],'document':m['document'],'match_sha256':m['content_hash'],'git_sha256':h,'lf_sha256':lf,'crlf_sha256':hashlib.sha256(raw.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')).hexdigest(),'chunk_id':m.get('chunk_id')},ensure_ascii=False))
