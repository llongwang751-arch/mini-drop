#!/usr/bin/env python3
"""为 Core/contracts 代码与夹具计算指纹，排除持续更新的证据与文档。"""
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(__file__).resolve().parents[2]
rows = []
for directory in ('core', 'contracts'):
    for path in sorted((root / directory).rglob('*')):
        if not path.is_file() or path.is_symlink() or any(part in ('evidence', '__pycache__', 'node_modules', 'build') for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() in ('.md', '.pyc', '.log', '.gz'):
            continue
        rows.append({'path':path.relative_to(root).as_posix(),'size':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
rows.sort(key=lambda row: row['path'])
canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
result = {'schema_version':1,'scope':'Core/contracts code and fixtures; excludes mutable evidence, logs, documentation and generated caches','algorithm':'SHA256 of UTF-8 canonical JSON files array (sort_keys=true, separators=comma/colon)','source_sha256':hashlib.sha256(canonical).hexdigest(),'files':rows}
if len(sys.argv) > 1:
    pathlib.Path(sys.argv[1]).write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n')
else:
    print(json.dumps(result, indent=2, ensure_ascii=False))
