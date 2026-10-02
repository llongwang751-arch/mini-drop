"""Publish a knowledge-only worker layer on Control, retaining old images/indexes.

Run from a new release directory populated with the previous release source and
the reviewed knowledge delta. No credentials are accepted or logged.
"""
import json
import argparse
import os
from pathlib import Path
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', action='store_true', help='Also publish the reviewed retrieval/working-memory runtime files')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    assert root.parent == Path('/opt/mini-drop-releases')
    previous = Path('/opt/mini-drop-current').resolve()
    assert previous != root
    private = root / 'private'
    private.mkdir(mode=0o700)
    original = (previous / 'private/runtime.compose.json').read_bytes()
    config = json.loads(original)
    spec = config['services']['diagnosis-worker']
    base = spec['image']
    image = 'mini-drop-knowledge:' + root.name
    dockerfile = root / 'Knowledge.Dockerfile'
    recipe = 'FROM ' + base + '\nCOPY knowledge /app/knowledge\nCOPY scripts/evaluate_sre_retrieval.py /app/scripts/evaluate_sre_retrieval.py\nCOPY benchmarks/retrieval /app/benchmarks/retrieval\n'
    if args.runtime:
        for file in ('server/app/agent_runtime/retrieval.py', 'server/app/agent_runtime/semantic_retrieval.py',
                     'server/app/agent_runtime/context.py', 'server/app/agent_runtime/memory.py',
                     'server/app/agent_runtime/themes.py', 'server/app/agent_runtime/deadlines.py',
                     'server/app/drop_insight/diagnosis_agent.py', 'server/app/drop_insight/service.py',
                     'server/app/drop_insight/claim_verifier.py'):
            recipe += 'COPY ' + file + ' /app/' + file + '\n'
    dockerfile.write_text(recipe)
    subprocess.run(['docker', 'build', '-f', str(dockerfile), '-t', image, str(root)], check=True)
    (private / 'rollback.compose.json').write_bytes(original)
    spec['image'] = image
    candidate = private / 'runtime.compose.json'
    candidate.write_text(json.dumps(config, indent=2))
    for path in private.iterdir():
        path.chmod(0o600)
    prefix = ['docker', 'compose', '-p', 'mini-drop-control', '-f', str(candidate)]
    # Build and test before changing the running worker. The old collection stays.
    subprocess.run(prefix + ['run', '--rm', '--no-deps', 'diagnosis-worker',
                              'python', 'scripts/build_knowledge_index.py'], check=True)
    evaluation = subprocess.check_output(prefix + ['run', '--rm', '--no-deps', '--user', '0', '--entrypoint', 'python', '-v', str(root) + ':/evaluation', 'diagnosis-worker',
                              'scripts/evaluate_sre_retrieval.py', '--backend', 'hybrid',
                              '--output', '/evaluation/knowledge-evaluation.json'])
    summary = json.loads(evaluation.decode().strip().splitlines()[-1])
    (root / 'knowledge-evaluation-summary.json').write_text(json.dumps(summary, indent=2))
    assert summary['recall_at_3'] >= 0.9 and summary['no_answer_false_positive_rate'] == 0
    assert summary['hybrid_backend_confirmed']
    try:
        subprocess.run(prefix + ['up', '-d', '--no-deps', 'diagnosis-worker'], check=True)
        for _ in range(30):
            state = json.loads(subprocess.check_output(['docker', 'inspect', 'mini-drop-control-diagnosis-worker-1']))[0]['State']
            if state.get('Health', {}).get('Status') == 'healthy':
                break
            time.sleep(2)
        else:
            raise RuntimeError('Worker failed health check')
        link = root.parent / ('.knowledge-current-' + root.name)
        link.symlink_to(root)
        os.replace(link, '/opt/mini-drop-current')
    except Exception:
        subprocess.run(['docker', 'compose', '-p', 'mini-drop-control', '-f',
                        str(private / 'rollback.compose.json'), 'up', '-d', '--no-deps', 'diagnosis-worker'], check=True)
        raise
    (root / 'knowledge-release.json').write_text(json.dumps({'previous_release': str(previous),
          'release': str(root), 'image': image, 'runtime_delta': args.runtime, 'evaluation': summary}, indent=2))
    print(json.dumps({'published': str(root), 'chunks': summary['chunks']}))


if __name__ == '__main__':
    main()
