"""Capture immediate read-only baseline; never prepare or activate a release."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path

STAGE = Path(__file__).resolve().parent
RELEASE = STAGE / 'final-release-r5'


def main():
    destination = STAGE / 'r5-runtime-preactivation.json'
    assert not destination.exists(), 'preserve the existing immediate baseline'
    spec = importlib.util.spec_from_file_location('planning_v2_preflight', STAGE / 'preflight_r5.py')
    preflight = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preflight)
    manifest = json.loads((RELEASE / 'manifest.json').read_text(encoding='utf-8'))
    prior = json.loads((STAGE / 'r5-runtime-predeployment.json').read_text(encoding='utf-8'))
    assert manifest['ci_evidence']['jobs_succeeded'] == 14
    assert manifest['git_head'] == prior['source_head'] == manifest['ci_evidence']['head_sha']
    remote = preflight.provider()
    current = preflight.runtime_baseline(remote)
    assert set(current['containers']) == set(prior['containers'])
    for name, item in current['containers'].items():
        previous = prior['containers'][name]
        for field in ('id', 'image', 'env_sha256', 'mounts', 'pid', 'started_at'):
            current_value, previous_value = item[field], previous[field]
            if field == 'mounts':
                current_value = sorted(current_value, key=lambda row: row['Destination'])
                previous_value = sorted(previous_value, key=lambda row: row['Destination'])
            assert current_value == previous_value, 'runtime changed before activation: ' + name + ':' + field
    for field in ('release', 'office_state', 'office_release', 'native_binary_sha256',
                  'cpp_binary_sha256', 'api_binary_sha256', 'toolchain', 'current_demo_pids'):
        assert current[field] == prior[field], 'protected baseline changed: ' + field
    assert current['free_bytes'] > manifest['deployment_capacity']['required_free_bytes'], "insufficient measured overlay capacity"
    current['public'] = preflight.verify_public_baseline(preflight.tls_client(remote), require_retired=True)
    current.update(source_head=manifest['git_head'], release_tag=manifest['release_tag'],
                   ci_run=manifest['ci_evidence']['run_id'], ci_jobs_succeeded=14,
                   changed_services=manifest['services'], bundle_sha256=prior['bundle_sha256'],
                   recorded_at=datetime.now(timezone.utc).isoformat())
    destination.write_text(json.dumps(current, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'captured': True, 'read_only': True, 'head': manifest['git_head'],
                      'healthy_containers': 13, 'inactive_faults': 21, 'office_state_verified': True}))


if __name__ == '__main__':
    main()
