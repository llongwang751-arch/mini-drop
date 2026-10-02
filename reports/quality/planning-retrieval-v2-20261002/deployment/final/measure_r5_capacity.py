from pathlib import Path
import importlib.util
import json

STAGE = Path(__file__).parent
def load(name):
    spec = importlib.util.spec_from_file_location(name, STAGE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

preflight = load('preflight_r5')
prepare = load('prepare_release_r5')
release = STAGE / 'final-release-r5'
receipt = STAGE / 'r5-measured-deployment-capacity.json'
assert not receipt.exists()
manifest_path = release / 'manifest.json'
manifest = json.loads(manifest_path.read_bytes())
assert 'deployment_capacity' not in manifest
remote = preflight.provider()
baseline = preflight.runtime_baseline(remote)
program = "import json,subprocess\nfrom pathlib import Path\np=Path('/opt/mini-drop-current').resolve()\nn=int(subprocess.check_output(['du','-sb',str(p)],text=True).split()[0])\nprint(json.dumps({'old_release':str(p),'old_release_bytes':n}))\n"
old = json.loads(remote.remote(program))
source = release / 'source'
backend = sum((source / n).stat().st_size for n in manifest['files'])
extra = sum((source / n).stat().st_size for n in manifest['deployment_files'])
bundle = (release / 'runtime-bundle.tgz').stat().st_size
required = 2**30 + 16 * (2 * backend + extra + bundle) + old['old_release_bytes']
capacity = {
    'status': 'CAPACITY_ASSESSED', 'observed_at': baseline['observed_at'],
    'free_bytes': baseline['free_bytes'], 'backend_bytes': backend,
    'deployment_bytes': extra, 'bundle_bytes': bundle, **old,
    'required_free_bytes': required, 'capacity_passed': baseline['free_bytes'] > required,
    'formula': '1GiB reserve + 16*(two backend overlays + deployment files + compressed bundle) + exact existing release copy size',
    'new_base_images_or_dependency_installs': False, 'files_images_volumes_deleted': 0,
}
receipt.write_text(json.dumps(capacity, indent=2) + '\n', encoding='utf-8')
print(json.dumps(capacity))
assert capacity['capacity_passed'], 'measured disk capacity insufficient; do not prune existing data'
manifest['deployment_capacity'] = capacity
manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
prepare.write_bundle()
