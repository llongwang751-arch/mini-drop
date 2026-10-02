"""Preserve completed experiments, exact source bytes, and independent recomputation."""
from pathlib import Path
import hashlib
import json
import shutil
import sys
import zipfile

sys.path.insert(0, str(Path.cwd()))
from scripts.run_distributed_endurance import verify_distributed

stage = Path(__file__).resolve().parent
destination = Path('reports/quality/performance-fix-20260930')


def copy(source, relative):
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        assert source.read_bytes() == target.read_bytes(), relative
    else:
        shutil.copyfile(source, target)


def archive(folder, snapshot, label):
    source = stage / folder
    result = verify_distributed(source / 'report.json')
    report = json.loads((source / 'report.json').read_text(encoding='utf-8'))
    assert report['status'] in {'PASSED', 'FAILED', 'INVALID'}
    for name, sha in report['source_hashes'].items():
        assert hashlib.sha256((stage / snapshot / name).read_bytes()).hexdigest() == sha
    target = destination / label
    target.mkdir(exist_ok=True)
    for name in ['report.json', 'report.html']:
        copy(source / name, label + '/' + name)
    verified = json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8')
    path = target / 'independent-verification.json'
    if path.exists():
        assert path.read_bytes() == verified
    else:
        path.write_bytes(verified)
    bundle = target / 'evidence.zip'
    if not bundle.exists():
        with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as output:
            for path in sorted(source.iterdir()):
                if path.suffix in {'.json', '.jsonl', '.log'}:
                    output.write(path, path.name)
            for name in sorted(report['source_hashes']):
                output.write(stage / snapshot / name, 'source/' + name)
            verifier = stage / snapshot / 'scripts/verify_load_report.py'
            if verifier.exists():
                output.write(verifier, 'source/scripts/verify_load_report.py')
    with zipfile.ZipFile(bundle) as contents:
        assert contents.testzip() is None
        assert contents.read('report.json') == (source / 'report.json').read_bytes()
        for name, sha in report['source_hashes'].items():
            assert hashlib.sha256(contents.read('source/' + name)).hexdigest() == sha
    return {'experiment': label, **result}


results = [
    archive('after-hour-isolated', 'after-source', 'load-single-tunnel-hour'),
    archive('after-multi-short', 'after-multi-source', 'load-multi-short'),
]
for name in ['single-tunnel-tail-analysis.json', 'multi-source-manifest.json',
             'multi-remote-source.json', 'python-full-tunnels.xml', 'tunnels-targeted.xml']:
    copy(stage / name, name)
if '--include-hour' in sys.argv:
    results.append(archive('after-multi-hour', 'after-multi-source', 'load-after-hour'))
print(json.dumps(results, ensure_ascii=False))
