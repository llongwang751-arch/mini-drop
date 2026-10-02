"""Stage only exact focused candidates and the authorized evidence directory."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).parent
REPORT = ROOT / 'reports/quality/planning-retrieval-v2-20261002'
HEAD = '73b4b18ad83553a5012a0025dfb78bb21ff8dc7f'

def git(*args, raw=None):
    return subprocess.check_output(['git', '-c', 'core.autocrlf=false', *args], cwd=ROOT, input=raw)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def main():
    assert git('rev-parse', 'HEAD').decode().strip() == HEAD
    assert not git('diff', '--cached', '--name-only').strip()
    assert json.loads((REPORT / 'manifest.json').read_bytes())['source_head'] == HEAD
    candidate = {}
    source_paths = {}
    for map_name in ['final-document-source-map.json', 'final-generator-source-map.json']:
        doc = json.loads((STAGE / map_name).read_bytes())
        assert doc['base_head'] == HEAD
        for row in doc['files']:
            raw = Path(row['clean_candidate']).read_bytes()
            assert sha(raw) == row['sha256'], row['path']
            assert row['path'] not in candidate
            candidate[row['path']] = raw
            source_paths[row['path']] = Path(row['clean_candidate'])
    for path in sorted(REPORT.rglob('*')):
        if path.is_file():
            name = path.relative_to(ROOT).as_posix()
            assert not git('ls-tree', HEAD, '--', name).strip(), 'new receipt directory contains an existing tracked file'
            assert path.suffix.lower() not in {'.env', '.tgz', '.pem', '.key'}, 'private deployment data cannot be published'
            candidate[name] = path.read_bytes()
            source_paths[name] = path
    for name, raw in candidate.items():
        for pattern in [rb'ghp_[A-Za-z0-9]{36,}', rb'github_pat_[A-Za-z0-9_]{50,}', rb'sk-[A-Za-z0-9]{24,}', rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----']:
            assert not re.search(pattern, raw), 'credential-shaped material requires review: ' + name
    paths = sorted(set(git('ls-files', '-z').decode().split('\0')) - {''} | set(candidate))
    assert all((ROOT / name).is_file() for name in paths)
    spec = importlib.util.spec_from_file_location('final_guide_generator', ROOT / 'scripts/generate_learning_guide_file_index.py')
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    block = generator.render(paths)
    def guide(text):
        before, rest = text.split(generator.START, 1)
        _, after = rest.split(generator.END, 1)
        return before.rstrip() + '\n\n' + block + after.lstrip('\r\n')
    name = 'docs/PROJECT_LEARNING_GUIDE.md'
    candidate[name] = guide(git('show', HEAD + ':' + name).decode()).encode()
    guide_candidate = STAGE / 'final-learning-guide-candidate.md'
    assert not guide_candidate.exists()
    guide_candidate.write_bytes(candidate[name])
    source_paths[name] = guide_candidate
    (ROOT / name).write_text(guide((ROOT / name).read_text(encoding='utf-8')), encoding='utf-8', newline='\n')
    initial = json.loads((STAGE / 'initial-worktree.json').read_bytes())
    owned = set(candidate)
    for receipt in ['scoped-candidate.json', 'followup-scoped-candidate.json', 'subject-final-scoped-candidate.json', 'prompt-final-scoped-candidate.json']:
        owned.update(row['path'] for row in json.loads((STAGE / receipt).read_bytes())['files'])
    owned.update({'docs/HELDOUT_EVALUATION.md', 'docs/TEST_ENGINEERING.md', 'web/vite.config.js'})
    protected = [row for row in initial['tracked_dirty_files'] if row['path'] not in owned]
    for row in protected:
        assert sha((ROOT / row['path']).read_bytes()) == row['sha256'], 'unrelated user edit changed: ' + row['path']
    # Identical focused transforms are applied to clean HEAD and dirty documents.
    # The exact candidates exclude pre-existing user annotations from the index.
    names = sorted(candidate)
    # This is Git's stdin path protocol, not shell command interpolation.
    input_paths = ''.join(json.dumps(source_paths[name].as_posix(), ensure_ascii=False) + '\n' for name in names).encode()
    oids = git('hash-object', '-w', '--no-filters', '--stdin-paths', raw=input_paths).decode().splitlines()
    assert len(oids) == len(names) and all(re.fullmatch('[0-9a-f]{40}', oid) for oid in oids)
    index_info = ''.join('100644 ' + oid + '\t' + name + '\n' for oid, name in zip(oids, names)).encode()
    git('update-index', '--index-info', raw=index_info)
    staged = set(git('diff', '--cached', '--name-only', '-z').decode().split('\0')) - {''}
    assert staged == set(candidate), 'unexpected staged scope'
    indexed = git('cat-file', '--batch', raw=''.join(':' + name + '\n' for name in names).encode())
    cursor = 0
    for name, oid in zip(names, oids):
        end = indexed.index(b'\n', cursor)
        found_oid, kind, size = indexed[cursor:end].decode().split()
        assert found_oid == oid and kind == 'blob'
        cursor = end + 1
        count = int(size)
        assert indexed[cursor:cursor + count] == candidate[name], 'staged bytes changed: ' + name
        assert indexed[cursor + count:cursor + count + 1] == b'\n'
        cursor += count + 1
    assert cursor == len(indexed)
    git('diff', '--cached', '--check')
    contract = json.loads(candidate['contracts/interview_delivery.json'])
    for descriptor in contract['sources'].values():
        raw = git('show', ':' + descriptor['path']) if descriptor['path'] in candidate else git('show', HEAD + ':' + descriptor['path'])
        if descriptor.get('normalization') == 'LF_TEXT':
            raw = raw.replace(b'\r\n', b'\n')
        assert sha(raw) == descriptor['sha256'], 'staged delivery evidence pin mismatch: ' + descriptor['path']
    for acceptance in contract['additional_acceptances']:
        descriptor = acceptance.get('report')
        if descriptor:
            raw = git('show', ':' + descriptor['path']) if descriptor['path'] in candidate else git('show', HEAD + ':' + descriptor['path'])
            assert sha(raw) == descriptor['sha256'], 'staged acceptance pin mismatch: ' + descriptor['path']
    result = {'status': 'VERIFIED_FOCUSED_INDEX', 'base_head': HEAD,
        'staged_files': len(candidate), 'staged_bytes': sum(len(raw) for raw in candidate.values()),
        'unrelated_dirty_files_preserved_byte_for_byte': len(protected),
        'new_evidence_files': sum(name.startswith('reports/quality/') for name in candidate),
        'user_annotations_excluded_from_candidates': True, 'secret_shape_check_passed': True,
        'files': [{'path': name, 'sha256': sha(raw)} for name, raw in sorted(candidate.items())]}
    (STAGE / 'final-scoped-index-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k != 'files'}))

if __name__ == '__main__':
    main()
