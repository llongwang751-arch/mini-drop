"""Clarify the measured adapter scope through documents and their source contract."""
from pathlib import Path
import hashlib
import importlib.util
import json

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
ANCHOR = '32新题零重试真实chat：'
SCOPE = '32新题通过当前公共生产schema的JSON DTO适配器执行离线规划实评，未运行LangGraph Agent、未派发采集任务或注入故障；维护者出题、独立代码复算，非第三方盲测。'

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def clarify(text):
    assert text.count(ANCHOR) == 1 and SCOPE not in text
    return text.replace(ANCHOR, SCOPE + ANCHOR, 1)

def main():
    mapping_path = STAGE / 'final-document-source-map.json'
    mapping = json.loads(mapping_path.read_bytes())
    for row in mapping['files']:
        name = row['path']
        candidate = Path(row['clean_candidate'])
        assert sha(candidate.read_bytes()) == row['sha256']
        if name.endswith('.md') and name not in {'docs/CURRENT_DELIVERY.md', 'docs/README.md'}:
            candidate.write_text(clarify(candidate.read_text(encoding='utf-8')), encoding='utf-8', newline='\n')
            (ROOT / name).write_text(clarify((ROOT / name).read_text(encoding='utf-8')), encoding='utf-8', newline='\n')
        elif name == 'contracts/interview_delivery.json':
            contract = json.loads(candidate.read_bytes())
            assert json.loads((ROOT / name).read_bytes()) == contract
            item = next(row for row in contract['additional_acceptances'] if row['title'] == 'v3新冻结32题模型与检索实评')
            item['scope'] = clarify(item['scope'])
            raw = (json.dumps(contract, ensure_ascii=False, indent=2) + '\n').encode()
            candidate.write_bytes(raw)
            (ROOT / name).write_bytes(raw)
        row['sha256'] = sha(candidate.read_bytes())
    spec = importlib.util.spec_from_file_location('scope_delivery_generator', ROOT / 'scripts/build_interview_delivery.py')
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    rendered = generator.generate(root=ROOT).encode()
    row = next(row for row in mapping['files'] if row['path'] == 'docs/CURRENT_DELIVERY.md')
    Path(row['clean_candidate']).write_bytes(rendered)
    (ROOT / row['path']).write_bytes(rendered)
    row['sha256'] = sha(rendered)
    mapping_path.write_text(json.dumps(mapping, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'CLARIFIED_THROUGH_SOURCE_CONTRACT', 'source_head': mapping['base_head'], 'files': len(mapping['files'])}))

if __name__ == '__main__':
    main()
