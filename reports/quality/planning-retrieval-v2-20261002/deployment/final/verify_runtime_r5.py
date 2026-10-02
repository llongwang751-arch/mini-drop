"""Read-only final source, HTTPS publication and retained-runtime verification."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
RELEASE = STAGE / 'final-release-r5'
SERVICES = ('diagnosis-worker', 'analyzer', 'web')


def main():
    destination = STAGE / 'r5-publication-verification.json'
    assert not destination.exists(), 'verification receipt already exists; preserve it'
    spec = importlib.util.spec_from_file_location('planning_retrieval_v2_preflight', STAGE / 'preflight_r5.py')
    preflight = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preflight)
    manifest = json.loads((RELEASE / 'manifest.json').read_text(encoding='utf-8'))
    before = json.loads((STAGE / 'r5-runtime-preactivation.json').read_text(encoding='utf-8'))
    assert manifest['services'] == list(SERVICES)
    inventory = manifest['backend_inventory']
    assert inventory['file_count'] == len(manifest['files']) and inventory['paths'] == sorted(manifest['files'])
    assert hashlib.sha256(('\n'.join(inventory['paths']) + '\n').encode()).hexdigest() == inventory['paths_sha256']
    assert before['source_head'] == manifest['git_head'] and before['release_tag'] == manifest['release_tag']
    ci = manifest['ci_evidence']
    assert ci['head_sha'] == manifest['git_head'] and ci['jobs_succeeded'] == 14
    for name, field in (('ci-run.json', 'run_sha256'), ('ci-jobs.json', 'jobs_sha256')):
        assert hashlib.sha256((RELEASE / name).read_bytes()).hexdigest() == ci[field]
    assert hashlib.sha256((RELEASE / 'deploy_runtime.py').read_bytes()).hexdigest() == manifest['deploy_runtime_sha256']
    web = {n: sha for n, sha in manifest['deployment_files'].items() if n.startswith('web/dist/')}
    assert len(web) == manifest['web_source']['dist_file_count'] and len(web) >= 1
    assert manifest['web_source']['source_kind'] == 'EXACT_GIT_HEAD' and manifest['web_source']['git_head'] == manifest['git_head']
    assert manifest['native_source_equivalence']['current_head'] == manifest['git_head']
    assert manifest['native_source_equivalence']['subtrees'] == ['native', 'proto', 'demo']
    remote_provider = preflight.provider()
    after = preflight.runtime_baseline(remote_provider)
    expected_release = '/opt/mini-drop-releases/' + manifest['release_tag']
    assert after['release'] == expected_release and len(after['containers']) == len(before['containers']) == 13
    assert after['api_binary_sha256'] == before['api_binary_sha256'] == preflight.API_SHA
    assert after['native_binary_sha256'] == before['native_binary_sha256'] == manifest['retained_native_binary_sha256']
    assert after['cpp_binary_sha256'] == before['cpp_binary_sha256']
    assert after['office_state'] == before['office_state'] and after['office_release'] == before['office_release']
    changed = {'/mini-drop-control-' + service + '-1' for service in SERVICES}
    untouched = 0

    def stable_mounts(rows):
        return sorted([{**row, 'Source': 'WORKSPACE_SOURCE' if row['Destination'] == '/workspace-source' else row['Source']}
                       for row in rows], key=lambda row: row['Destination'])

    for name, current in after['containers'].items():
        prior = before['containers'][name]
        assert current['env_sha256'] == prior['env_sha256'], name
        assert stable_mounts(current['mounts']) == stable_mounts(prior['mounts']), name
        if name in changed:
            assert current['id'] != prior['id'], 'requested service was not replaced: ' + name
            for mount in current['mounts']:
                if mount['Destination'] == '/workspace-source':
                    assert mount['Source'] == expected_release, name
        else:
            untouched += 1
            assert current['id'] == prior['id'] and current['image'] == prior['image'], name
            assert sorted(current['mounts'], key=lambda row: row['Destination']) == sorted(prior['mounts'], key=lambda row: row['Destination']), name
    assert untouched == 10
    code = '''import hashlib,json,subprocess
from pathlib import Path
files=%r
web=%r
expected_release=%r
head=%r
ci=%r
def output(args):return subprocess.check_output(args,text=True).strip()
services={}
for service in ('diagnosis-worker','analyzer'):
 name='mini-drop-control-'+service+'-1'
 lines=output(['docker','exec',name,'sha256sum',*['/app/'+n for n in files]]).splitlines()
 actual={line.split()[1].removeprefix('/app/'):line.split()[0] for line in lines}
 assert actual==files,service
 obj=json.loads(output(['docker','inspect',name]))[0]
 assert obj['State']['Health']['Status']=='healthy'
 services[service]={'files_verified':len(actual),'image':obj['Image'],'health':'healthy'}
lines=output(['docker','exec','mini-drop-control-web-1','sha256sum',*['/usr/share/nginx/html/'+n.removeprefix('web/dist/') for n in web]]).splitlines()
actual={'web/dist/'+line.split()[1].removeprefix('/usr/share/nginx/html/'):line.split()[0] for line in lines}
assert actual==web
obj=json.loads(output(['docker','inspect','mini-drop-control-web-1']))[0]
assert obj['State']['Health']['Status']=='healthy'
services['web']={'files_verified':len(actual),'image':obj['Image'],'health':'healthy'}
assert str(Path('/opt/mini-drop-current').resolve())==expected_release
deployed=json.loads((Path(expected_release)/'source-manifest-20260930.json').read_text())
assert deployed['git_head']==head and deployed['files']==files and deployed['ci_evidence']==ci
print(json.dumps({'services':services,'web_files_verified':len(web),'deployed_manifest_head':deployed['git_head']}))
''' % (manifest['files'], web, expected_release, manifest['git_head'], ci)
    containers = json.loads(remote_provider.remote(code))
    client = preflight.tls_client(remote_provider)
    for name, expected in web.items():
        raw = client.request_raw('GET', '/' + name.removeprefix('web/dist/'))
        assert hashlib.sha256(raw).hexdigest() == expected, name
    public = preflight.verify_public_baseline(client, require_retired=True)
    marker = client.request('GET', '/report-assets/performance-audit/index.json')
    assert marker == {
        'schema': 'mini-drop.retired-performance-index.v1', 'status': 'RETIRED',
        'replacement_profile': 'engineering-diagnosis.v1',
        'replacement_url': '/report-assets/engineering-diagnosis/index.json',
    }, 'old public index did not become the exact score-free marker'
    assert manifest['retired_current_score']['status'] == 'RETIRED'
    assert hashlib.sha256(client.request_raw('GET', '/report-assets/performance-audit/index.json')).hexdigest() == manifest['retired_current_score']['marker_sha256']
    index = client.request('GET', '/report-assets/engineering-diagnosis/index.json')
    assert (index['diagnosis_accepted'], index['localization_accepted'], index['refuted']) == (21, 6, 8)
    assert index['current_campaign_id'] == 'seven-gaps-20261002'
    assert (index['fresh_live_scenarios'], index['regraded_prior_scenarios'], index['fresh_live_run']) == (7, 14, False)
    assert len(index['cases']) == 21 and all(row['causal_root_cause_verified'] is False and row['same_load_fix_verified'] is False for row in index['cases'])
    current_cases = [row for row in index['cases'] if row['campaign_id'] == index['current_campaign_id']]
    assert len(current_cases) == 7 and all(row['fresh_live_run'] is True and row['originally_live_record'] is True for row in current_cases)
    result = {
        **containers, **public, 'source_head': manifest['git_head'], 'release': after['release'],
        'healthy_containers': 13, 'untouched_containers': untouched, 'changed_services': list(SERVICES),
        'all_container_env_hashes_verified': True, 'all_container_mounts_verified': True,
        'web_source_kind': manifest['web_source']['source_kind'], 'public_web_sha_verified': len(web),
        'native_source_equivalence': manifest['native_source_equivalence'],
        'native_binary_sha256': after['native_binary_sha256'], 'cpp_binary_sha256': after['cpp_binary_sha256'],
        'api_binary_sha256': after['api_binary_sha256'], 'toolchain': after['toolchain'],
        'agent_receipt': after['agent_receipt'], 'current_demo_pids': after['current_demo_pids'],
        'office_state': after['office_state'], 'office_release': after['office_release'],
        'ci_run': ci['run_id'], 'ci_jobs_succeeded': 14, 'engineering_summary': manifest['engineering_summary'],
        'causal_root_cause_verified': False, 'same_load_fix_verified': False, 'verified_at': after['observed_at'],
        'retired_score_asset_verified': True, 'retired_score_asset': marker, 'public_fault_catalog_score_fields_omitted': True,
    }
    destination.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'verified': True, 'source_head': manifest['git_head'], 'services': list(SERVICES),
                      'backend_files_per_container': len(manifest['files']), 'public_web_files': len(web), 'untouched_containers': untouched,
                      'healthy_containers': 13, 'inactive_faults': 21, 'engineering_summary': manifest['engineering_summary']}))


if __name__ == '__main__':
    main()
