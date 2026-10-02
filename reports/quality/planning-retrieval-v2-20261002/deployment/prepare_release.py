"""Prepare an exact committed-source release. Never deploy or change Git state."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
RELEASE = STAGE / 'final-release-r3'
CLEAN = ROOT / '.tmp-planning-retrieval-v2-source-r3'
SERVICES = ('diagnosis-worker', 'analyzer', 'web')
BACKEND_PREFIXES = ('server/', 'analyzer/', 'scripts/', 'contracts/')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def git_files(head, *paths):
    return [n for n in git('ls-tree', '-r', '--name-only', '-z', head, *paths).decode('utf-8').split('\0') if n]


def owned_target(base, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or '\\' in name:
        raise ValueError('unsafe release path')
    target = base.joinpath(*relative.parts)
    if not target.resolve().is_relative_to(base.resolve()):
        raise ValueError('release write would escape its owned directory')
    return target


def write_blob(base, name, raw):
    target = owned_target(base, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return digest(raw)


def render_deployer():
    template = (ROOT / 'output/acceptance/seven-gaps-20261002/phase2-release/deploy_runtime.py').read_text(encoding='utf-8')
    old = "SERVICES = ('diagnosis-worker', 'analyzer')"
    assert template.count(old) == 1, 'phase2 deployer service scope changed'
    result = template.replace(old, "SERVICES = ('diagnosis-worker', 'analyzer', 'web')", 1)
    disk_gate = "    assert shutil.disk_usage('/').free > 3 * 2**30"
    assert result.count(disk_gate) == 1
    result = result.replace(disk_gate, "    assert shutil.disk_usage('/').free > json.loads((STAGE / 'manifest.json').read_text())['deployment_capacity']['required_free_bytes']", 1)
    tree = ast.parse(result)
    scope = [ast.literal_eval(node.value) for node in tree.body
             if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'SERVICES' for t in node.targets)]
    assert scope == [SERVICES]
    assert "'FROM ' + cpp_symbol_image + ' AS symbols" in result
    assert "'FROM ' + obj['Image']" in result
    assert 'addr2line' in result
    return result


def write_bundle():
    manifest = json.loads((RELEASE / 'manifest.json').read_text(encoding='utf-8'))
    destinations = {**manifest['files'], **manifest['deployment_files']}
    target = RELEASE / 'runtime-bundle.tgz'
    temporary = RELEASE / 'runtime-bundle.new.tgz'
    assert not temporary.exists(), 'bundle temporary file already exists'
    with tarfile.open(temporary, 'w:gz') as archive:
        for name in ('manifest.json', 'deploy_runtime.py', 'ci-run.json', 'ci-jobs.json'):
            path = RELEASE / name
            if path.exists():
                archive.add(path, arcname=name)
        for name, expected in destinations.items():
            path = owned_target(RELEASE / 'source', name)
            assert path.is_file() and digest(path.read_bytes()) == expected, name
            archive.add(path, arcname='source/' + name)
    os.replace(temporary, target)
    return digest(target.read_bytes())


def make_dependency_junction(target, dependencies):
    assert os.name == 'nt', 'this preparation uses the existing Windows dependency junction'
    assert target.parent.resolve().is_relative_to(CLEAN.resolve()) and not target.exists()
    assert dependencies.is_dir() and dependencies.resolve().is_relative_to(ROOT.resolve())
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    command = ("$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path "
               + quote(target) + ' -Value ' + quote(dependencies.resolve()) + ' | Out-Null')
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command], check=True)
    assert target.resolve() == dependencies.resolve()


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-head', required=True)
    args = parser.parse_args()
    assert git('rev-parse', 'HEAD').decode().strip() == args.expected_head, 'HEAD must be the explicit committed candidate'
    assert not RELEASE.exists() and not CLEAN.exists(), 'choose a new preparation directory; existing evidence is preserved'
    head = git('rev-parse', 'HEAD').decode().strip()
    original = json.loads((ROOT / 'output/acceptance/seven-gaps-20261002/release/manifest.json').read_text(encoding='utf-8'))
    producer = original['git_head']
    assert producer.startswith('b7a545'), 'unexpected original native/demo producer'
    subtrees = ('native', 'proto', 'demo')
    before = git('ls-tree', '-r', producer, *subtrees)
    after = git('ls-tree', '-r', head, *subtrees)
    assert before == after, 'native, proto or demo changed; a retained-runtime release is inappropriate'
    backend_paths = git_files(head, 'server', 'analyzer', 'scripts', 'contracts')
    assert backend_paths and len(backend_paths) == len(set(backend_paths))
    assert all(name.startswith(BACKEND_PREFIXES) for name in backend_paths)
    required = {'server/app/agent_runtime/relevance.py', 'server/app/agent_runtime/planning_output.py',
                'server/app/agent_runtime/retrieval.py', 'server/app/agent_runtime/semantic_retrieval.py'}
    assert required.issubset(backend_paths), 'required production v2 modules absent from committed source'
    prior = json.loads((ROOT / 'output/acceptance/retire-strict-score-20261002/final-release/manifest.json').read_text(encoding='utf-8'))
    assert git('ls-tree', '-r', prior['git_head'], 'knowledge') == git('ls-tree', '-r', head, 'knowledge'), 'knowledge changed; review index and deploy scope before retaining it' 
    web_paths = git_files(head, 'web')
    assert web_paths and not any(n.startswith(('web/node_modules/', 'web/dist/')) for n in web_paths)
    index_bytes = git('show', head + ':web/public/report-assets/engineering-diagnosis/index.json')
    index = json.loads(index_bytes)
    assert (index['diagnosis_accepted'], index['localization_accepted'], index['refuted']) == (21, 6, 8)
    assert index['current_campaign_id'] == 'seven-gaps-20261002'
    assert (index['fresh_live_scenarios'], index['regraded_prior_scenarios'], index['fresh_live_run']) == (7, 14, False)
    assert len(index['cases']) == 21 and all(c['causal_root_cause_verified'] is False and c['same_load_fix_verified'] is False for c in index['cases'])
    marker_path = 'web/public/report-assets/performance-audit/index.json'
    marker_bytes = git('show', head + ':' + marker_path)
    marker = json.loads(marker_bytes)
    assert marker == {
        'schema': 'mini-drop.retired-performance-index.v1', 'status': 'RETIRED',
        'replacement_profile': 'engineering-diagnosis.v1',
        'replacement_url': '/report-assets/engineering-diagnosis/index.json',
    }, 'retired public asset must contain exactly four replacement-marker keys'
    RELEASE.mkdir()
    CLEAN.mkdir()
    source = RELEASE / 'source'
    source.mkdir()
    backend = {name: write_blob(source, name, git('show', head + ':' + name)) for name in backend_paths}
    # Canonical top-level domain/context docs and Dockerfile sources remain auditable.
    extra_names = [n for n in git_files(head, 'docs') if n.startswith('docs/') and n.count('/') == 1 and n.endswith('.md')]
    extra_names += git_files(head, 'deploy/dockerfiles')
    extra = {name: write_blob(source, name, git('show', head + ':' + name)) for name in extra_names}
    web_source = {name: write_blob(CLEAN, name, git('show', head + ':' + name)) for name in web_paths}
    assert (CLEAN / 'web/package.json').is_file() and (CLEAN / 'web/vite.config.js').is_file()
    checker = 'scripts/check_web_bundle.mjs'
    write_blob(CLEAN, checker, git('show', head + ':' + checker))
    make_dependency_junction(CLEAN / 'web/node_modules', ROOT / 'web/node_modules')
    # Native Vite config loading avoids writing .vite-temp through the dependency
    # junction. The preload changes only CLI arguments; committed source stays exact.
    preload = CLEAN / 'build-loader.mjs'
    preload.write_text("if ((process.argv[1] || '').replaceAll('\\\\', '/').endsWith('/vite/bin/vite.js') && process.argv.includes('build')) { process.argv.push('--configLoader', 'native'); }\n", encoding='utf-8')
    for directory in ('tmp', 'npm-cache'):
        (CLEAN / directory).mkdir()
    environment = os.environ.copy()
    environment.update(NODE_OPTIONS='--import=' + preload.as_uri(),
                       npm_config_cache=str(CLEAN / 'npm-cache'), TEMP=str(CLEAN / 'tmp'), TMP=str(CLEAN / 'tmp'))
    npm = shutil.which('npm.cmd')
    assert npm, 'npm.cmd unavailable'
    result = subprocess.run([npm, 'run', 'build:check'], cwd=CLEAN / 'web', env=environment,
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
    (RELEASE / 'web-build.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    assert result.returncode == 0, 'exact-source Web build failed; inspect private-free web-build.log'
    assert all(owned_target(CLEAN, name).read_bytes() == git('show', head + ':' + name) for name in web_paths), 'Web source changed during build'
    dist = CLEAN / 'web/dist'
    assert dist.resolve().is_relative_to(CLEAN.resolve())
    dist_files = [p for p in sorted(dist.rglob('*')) if p.is_file()]
    assert 1 <= len(dist_files) <= 128, 'review unexpected public Web asset inventory'
    for path in dist_files:
        assert path.resolve().is_relative_to(dist.resolve()), 'dist contains a link outside its owned directory'
        name = 'web/dist/' + path.relative_to(dist).as_posix()
        extra[name] = write_blob(source, name, path.read_bytes())
    assert (dist / 'report-assets/engineering-diagnosis/index.json').read_bytes() == index_bytes
    assert (dist / 'report-assets/performance-audit/index.json').read_bytes() == marker_bytes, 'retired marker must overwrite the same old public path'
    assert git('rev-parse', 'HEAD').decode().strip() == head, 'HEAD changed during preparation'
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    deployer = render_deployer()
    (RELEASE / 'deploy_runtime.py').write_text(deployer, encoding='utf-8')
    manifest = {
        'git_head': head, 'release_tag': tag, 'fault_lab_agent_id': 'control-interview-demo-agent',
        'files': backend, 'deployment_files': extra, 'services': list(SERVICES),
        'backend_inventory': {'prefixes': list(BACKEND_PREFIXES), 'paths': sorted(backend),
                              'file_count': len(backend),
                              'paths_sha256': digest(('\n'.join(sorted(backend)) + '\n').encode())},
        'retained_knowledge_equivalence': {'producer_head': prior['git_head'], 'current_head': head,
                                         'tree_sha256': digest(git('ls-tree', '-r', head, 'knowledge'))},
        'web_source': {'source_kind': 'EXACT_GIT_HEAD', 'git_head': head, 'source_files': web_source,
                       'dist_file_count': len(dist_files), 'build_command': 'npm run build:check', 'config_loader': 'native'},
        'native_source_equivalence': {'producer_source_head': producer, 'current_head': head,
                                     'subtrees': list(subtrees), 'native_proto_demo_tree_sha256': digest(after)},
        'retained_native_binary_sha256': original['deployment_files']['deploy/bin/mini-drop-native-agent'],
        'engineering_summary': {'accepted': 21, 'localized': 6, 'refuted': 8, 'current_campaign': 7, 'historical': 14},
        'retired_current_score': {'status': 'RETIRED', 'public_marker_path': marker_path, 'marker_sha256': digest(marker_bytes)},
        'deploy_runtime_sha256': digest((RELEASE / 'deploy_runtime.py').read_bytes()),
        'scope': 'Three-service v2 planning output/domain relevance and Web event overlay; current engineering21/6/8, retired score marker, public corpus, binutils and identical native/demo/CPP/API retained',
    }
    (RELEASE / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    (RELEASE / 'release-tag.txt').write_text(tag, encoding='utf-8')
    (RELEASE / 'source-head.txt').write_text(head, encoding='utf-8')
    bundle_sha = write_bundle()
    print(json.dumps({'head': head, 'tag': tag, 'backend_files': len(backend), 'web_dist_files': len(dist_files),
                      'services': list(SERVICES), 'bundle_sha256': bundle_sha, 'scope': 'PREPARED_ONLY; NOT_DEPLOYED'}))


if __name__ == '__main__':
    main()
