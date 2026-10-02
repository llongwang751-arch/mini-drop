"""Stage only reviewed v3 delivery documents and immutable measured evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
BASE = 'cfea6744fc2395d5392440b2404ee275ada0a69f'
PREFIX = 'reports/quality/planning-boundary-v3-20261002'

def git(*args, raw=None):
    return subprocess.check_output(['git', '-c', 'core.autocrlf=false', *args], cwd=ROOT, input=raw)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def main():
    assert git('rev-parse', 'HEAD').decode().strip() == BASE
    assert not git('diff', '--cached', '--name-only').strip(), 'Unexpected pre-existing index'
    mapping = json.loads((STAGE / 'final-document-source-map.json').read_bytes())
    assert mapping['base_head'] == BASE
    candidates, sources = {}, {}
    for row in mapping['files']:
        source = Path(row['clean_candidate'])
        raw = source.read_bytes()
        assert sha(raw) == row['sha256']
        candidates[row['path']] = raw
        sources[row['path']] = source
    for path in sorted((ROOT / PREFIX).rglob('*')):
        if path.is_file():
            name = path.relative_to(ROOT).as_posix()
            assert name not in candidates
            candidates[name] = path.read_bytes()
            sources[name] = path
    tracked = set(git('ls-files', '-z').decode().split('\0')) - {''}
    paths = sorted(tracked | set(candidates))
    spec = importlib.util.spec_from_file_location('delivery_guide', ROOT / 'scripts/generate_learning_guide_file_index.py')
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    block = generator.render(paths)
    def replace_index(text):
        before, rest = text.split(generator.START, 1)
        _, after = rest.split(generator.END, 1)
        return before.rstrip() + '\n\n' + block + after.lstrip('\r\n')
    name = 'docs/PROJECT_LEARNING_GUIDE.md'
    clean = replace_index(git('show', BASE + ':' + name).decode()).encode()
    source = STAGE / 'final-learning-guide-candidate.md'
    assert not source.exists()
    source.write_bytes(clean)
    before = (ROOT / name).read_text(encoding='utf-8')
    after = replace_index(before)
    # The generator changes only the declared file inventory, retaining teaching prose.
    assert before.split(generator.START, 1)[0].rstrip() == after.split(generator.START, 1)[0].rstrip()
    assert before.split(generator.END, 1)[1].lstrip('\r\n') == after.split(generator.END, 1)[1].lstrip('\r\n')
    (ROOT / name).write_text(after, encoding='utf-8', newline='\n')
    candidates[name] = clean
    sources[name] = source
    initial = json.loads((STAGE / 'initial-worktree.json').read_bytes())
    previous_owned = set()
    for filename in ['source-scoped-index-verification.json', 'line-ending-source-index-verification.json']:
        receipt = json.loads((STAGE / filename).read_bytes())
        previous_owned.update(row['path'] for row in receipt['files'])
    protected = [row for row in initial['tracked_dirty_files'] if row['path'] not in previous_owned | set(candidates)]
    for row in protected:
        assert sha((ROOT / row['path']).read_bytes()) == row['sha256'], row['path']
    patterns = (rb'ghp_[A-Za-z0-9]{36,}', rb'github_pat_[A-Za-z0-9_]{50,}',
                rb'sk-[A-Za-z0-9]{24,}', rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
    for name, raw in candidates.items():
        assert not any(re.search(pattern, raw) for pattern in patterns), name
    names = sorted(candidates)
    protocol = ''.join(json.dumps(sources[name].as_posix(), ensure_ascii=False) + '\n' for name in names).encode()
    oids = git('hash-object', '-w', '--no-filters', '--stdin-paths', raw=protocol).decode().splitlines()
    assert len(oids) == len(names)
    git('update-index', '--index-info', raw=''.join('100644 ' + oid + '\t' + name + '\n' for name, oid in zip(names, oids)).encode())
    assert set(git('diff', '--cached', '--name-only', '-z').decode().split('\0')) - {''} == set(names)
    batch = git('cat-file', '--batch', raw=''.join(':' + name + '\n' for name in names).encode())
    cursor = 0
    for name, oid in zip(names, oids):
        end = batch.index(b'\n', cursor)
        found, kind, size = batch[cursor:end].decode().split()
        assert found == oid and kind == 'blob'
        cursor = end + 1
        assert batch[cursor:cursor + int(size)] == candidates[name], name
        cursor += int(size) + 1
    assert cursor == len(batch)
    git('diff', '--cached', '--check')
    receipt = {'status': 'VERIFIED_FOCUSED_DELIVERY_INDEX', 'application_source_head': BASE,
               'source_changed': False, 'unrelated_dirty_files_preserved_byte_for_byte': len(protected),
               'staged_files': len(names), 'preexisting_annotations_excluded': True,
               'files': [{'path': name, 'sha256': sha(candidates[name])} for name in names]}
    (STAGE / 'final-delivery-index-verification.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in receipt.items() if key != 'files'}))

if __name__ == '__main__':
    main()
