"""Activate only the explicit committed three-service candidate with rollback."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile

STAGE = Path(__file__).resolve().parent
RELEASE = STAGE / 'final-release-r3'
SERVICES = ['diagnosis-worker', 'analyzer', 'web']
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
       '-o', 'ConnectTimeout=15', 'root@120.24.187.205']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-head', required=True)
    args = parser.parse_args()
    manifest = json.loads((RELEASE / 'manifest.json').read_text(encoding='utf-8'))
    baseline = json.loads((STAGE / 'immediate-runtime-preactivation.json').read_text(encoding='utf-8'))
    tag = manifest['release_tag']
    assert re.fullmatch(r'\d{8}T\d{6}Z', tag)
    assert manifest['git_head'] == args.expected_head == baseline['source_head']
    assert manifest['services'] == SERVICES == baseline['changed_services']
    assert baseline['release_tag'] == tag
    observed = datetime.fromisoformat(baseline['recorded_at'])
    assert 0 <= (datetime.now(timezone.utc) - observed).total_seconds() <= 180, 'capture a fresh baseline near activation'
    assert not (RELEASE / 'platform-deployment.json').exists(), 'preserve prior activation evidence'
    assert not (RELEASE / 'platform-deployment.log').exists(), 'preserve prior activation log'
    run = json.loads((RELEASE / 'ci-run.json').read_text(encoding='utf-8'))
    jobs = json.loads((RELEASE / 'ci-jobs.json').read_text(encoding='utf-8'))['jobs']
    assert run['head_sha'] == manifest['git_head'] and run['conclusion'] == 'success' and run['status'] == 'completed'
    assert len(jobs) == 14 and len({item['id'] for item in jobs}) == 14
    assert all(item['head_sha'] == manifest['git_head'] and item['run_id'] == run['id']
               and item['status'] == 'completed' and item['conclusion'] == 'success' for item in jobs)
    for name, field in [('ci-run.json', 'run_sha256'), ('ci-jobs.json', 'jobs_sha256')]:
        assert hashlib.sha256((RELEASE / name).read_bytes()).hexdigest() == manifest['ci_evidence'][field]
    assert hashlib.sha256((RELEASE / 'deploy_runtime.py').read_bytes()).hexdigest() == manifest['deploy_runtime_sha256']
    bundle = RELEASE / 'runtime-bundle.tgz'
    bundle_sha = hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert bundle_sha == baseline['bundle_sha256']
    with tarfile.open(bundle) as archive:
        names = [member.name for member in archive.getmembers()]
        assert len(names) == len(set(names))
        assert archive.extractfile('manifest.json').read() == (RELEASE / 'manifest.json').read_bytes()
        assert archive.extractfile('deploy_runtime.py').read() == (RELEASE / 'deploy_runtime.py').read_bytes()
        for name, expected in {**manifest['files'], **manifest['deployment_files']}.items():
            assert hashlib.sha256(archive.extractfile('source/' + name).read()).hexdigest() == expected, name
    remote_archive = '/root/planning-retrieval-v2-' + tag + '.tgz'
    subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                    str(bundle), 'root@120.24.187.205:' + remote_archive], check=True, timeout=90)
    code = '''from pathlib import Path
import hashlib,tarfile,subprocess
tag=%r
archive_path=Path(%r)
assert hashlib.sha256(archive_path.read_bytes()).hexdigest()==%r
root=Path('/root/incoming-'+tag)
assert not root.exists();root.mkdir(mode=0o700)
with tarfile.open(archive_path) as archive:
    for member in archive.getmembers():
        assert member.isfile() and not Path(member.name).is_absolute() and (root/member.name).resolve().is_relative_to(root)
    archive.extractall(root)
subprocess.run(['python3',str(root/'deploy_runtime.py')],check=True)
''' % (tag, remote_archive, bundle_sha)
    result = subprocess.run(SSH + ['python3 -'], input=code, text=True, encoding='utf-8',
                            capture_output=True, timeout=600)
    (RELEASE / 'platform-deployment.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode:
        raise RuntimeError('Activation failed; preserve log and review retained rollback status')
    summary = json.loads(next(line for line in reversed(result.stdout.splitlines()) if line.startswith('{"release":')))
    assert summary['source_head'] == manifest['git_head'] and summary['changed_services'] == SERVICES
    (RELEASE / 'platform-deployment.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
