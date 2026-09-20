"""Package runtime sources and every built web asset; exclude local state/secrets."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ('server', 'analyzer', 'skills', 'knowledge', 'scripts', 'demo', 'native', 'deploy/dockerfiles', 'web/dist', 'benchmarks/retrieval')
SKIP = {'__pycache__', 'node_modules', 'build', '.git', 'private'}
EXTENSIONS = {'.py', '.json', '.md', '.yaml', '.yml', '.cpp', '.c', '.cc', '.h', '.hpp', '.java', '.proto', '.bt', '.cmake', '.sh', '.js', '.jsx', '.css', '.toml', '.ini', '.html', '.map', '.svg', '.txt', '.ps1', '.mjs', '.go', '.sum', '.mod', '.Dockerfile'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Choose a new archive path; old release evidence is retained.')
    files = []
    for directory in DIRECTORIES:
        for path in (ROOT/directory).rglob('*'):
            rel = path.relative_to(ROOT)
            if not path.is_file() or SKIP.intersection(rel.parts) or path.name.startswith('.env'):
                continue
            if directory == 'web/dist' or path.suffix in EXTENSIONS or path.name in {'Dockerfile', 'CMakeLists.txt'}:
                files.append(path)
    files += [ROOT/name for name in ['pyproject.toml', 'README.md', 'alembic.ini', '.dockerignore']]
    manifest = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.output, 'w:gz') as archive:
        for p in sorted(files):
            archive.add(p, arcname=p.relative_to(ROOT).as_posix(), recursive=False)
        data = json.dumps(manifest, indent=2).encode()
        info = tarfile.TarInfo('source-manifest.json')
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    print(json.dumps({'files':len(files), 'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest(), 'output':str(args.output)}))

if __name__ == '__main__':
    main()
