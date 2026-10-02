"""Copy reviewed publication generators with exact byte inventories."""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
PUBLIC = ROOT / 'reports/quality/planning-boundary-v3-20261002/publication'

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def main():
    PUBLIC.mkdir(exist_ok=True)
    rows = []
    for name in ['prepare_final_documents.py', 'build_delivery_manifest.py', 'stage_final_delivery.py',
                 'publish_delivery_tools.py', 'clarify_final_evaluation_scope.py',
                 'audit_publication_secrets.py', 'final-document-source-map.json']:
        raw = (STAGE / name).read_bytes()
        assert not any(re.search(pattern, raw) for pattern in [rb'ghp_[A-Za-z0-9]{36,}', rb'github_pat_[A-Za-z0-9_]{50,}',
                    rb'sk-[A-Za-z0-9]{24,}', rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'])
        target = PUBLIC / name
        if target.exists():
            assert target.read_bytes() == raw, 'Publication tools are immutable after capture'
        else:
            target.write_bytes(raw)
        rows.append({'path': name, 'bytes': len(raw), 'sha256': sha(raw), 'original_path': (STAGE / name).relative_to(ROOT).as_posix()})
    receipt = {'schema': 'mini-drop.publication-tools-capture.v1', 'status': 'VERIFIED',
               'scope': 'Reviewed final documentation/evidence generators and source contract, captured byte for byte; never production model or raw score rewriting. Paths and original workspace inputs define invocation context.',
               'application_source_head': 'cfea6744fc2395d5392440b2404ee275ada0a69f', 'inventory': rows}
    raw = (json.dumps(receipt, ensure_ascii=False, indent=2) + '\n').encode()
    target = PUBLIC / 'archive-manifest.json'
    if target.exists():
        assert target.read_bytes() == raw
    else:
        target.write_bytes(raw)
    print(json.dumps({'status': 'VERIFIED', 'files': len(rows), 'sha256': sha(raw)}))

if __name__ == '__main__':
    main()
