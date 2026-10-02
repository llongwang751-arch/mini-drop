"""Build the current engineering diagnosis view from versioned, pinned evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.evaluate_engineering_diagnosis import evaluate_engineering_case
from server.app.drop_insight.performance_criteria import SIGNAL_FIELDS

OUTPUT = ROOT / 'web/public/report-assets/engineering-diagnosis/index.json'


def _verify_required_downloads(root, case, spec, pins):
    """Cross-check actual pinned downloads against every persisted artifact.

    The live runner's evaluation override is not a download proof. This check
    independently verifies the task/artifact linkage, digest, raw bytes and
    archive membership before publishing a newly required live case.
    """
    required = spec.get('requires_verified_downloads')
    if required is not None and type(required) is not bool:
        raise ValueError('required live downloads flag must be boolean')
    if required is not True:
        return
    if case.get('records_error'):
        raise ValueError('required live artifact collection failed')
    expected = {}
    tasks = (case.get('records') or {}).get('tasks')
    if not isinstance(tasks, list):
        raise ValueError('required live task artifact records are missing')
    task_ids = set()
    for task in tasks:
        task_id = task.get('task_id') if isinstance(task, dict) else None
        artifacts = task.get('artifacts') if isinstance(task, dict) else None
        if not isinstance(task_id, str) or not task_id or not isinstance(artifacts, list):
            raise ValueError('invalid required live task artifact record')
        if task_id in task_ids:
            raise ValueError('duplicate required live task record')
        task_ids.add(task_id)
        for artifact in artifacts:
            artifact_id = artifact.get('id') if isinstance(artifact, dict) else None
            kind = artifact.get('artifact_type') if isinstance(artifact, dict) else None
            digest = artifact.get('sha256') if isinstance(artifact, dict) else None
            if (type(artifact_id) not in (str, int) or not artifact_id
                or (type(artifact_id) is int and artifact_id <= 0)
                or not isinstance(kind, str) or not kind or not isinstance(digest, str)
                or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)):
                raise ValueError('invalid required live artifact identity or SHA')
            key = (task_id, artifact_id, kind)
            if key in expected:
                raise ValueError('duplicate required live artifact record')
            expected[key] = digest
    downloads = case.get('downloads')
    if not expected or not isinstance(downloads, list) or not downloads:
        raise ValueError('required live artifact downloads are missing')
    matched = set()
    for row in downloads:
        if not isinstance(row, dict):
            raise ValueError('invalid required live download record')
        key = (row.get('task_id'), row.get('artifact_id'), row.get('artifact_type'))
        if (type(key[1]) not in (str, int) or not key[1]
            or not isinstance(key[0], str) or not isinstance(key[2], str)
            or key not in expected or key in matched or row.get('sha256') != expected[key]):
            raise ValueError('live download does not match its unique task artifact')
        name = row.get('file')
        if not isinstance(name, str) or not name or name in {'.', '..'} or '/' in name or '\\' in name:
            raise ValueError('invalid required live download filename')
        relative = Path(spec['case_path']).parent / (case['scenario_id'] + '-artifacts') / name
        file = (root / relative).resolve()
        if (not file.is_relative_to(root.resolve()) or pins.get(relative.as_posix()) != expected[key]
            or hashlib.sha256(file.read_bytes()).hexdigest() != expected[key]):
            raise ValueError('live download bytes are not verified by the pinned archive')
        matched.add(key)
    if matched != set(expected):
        raise ValueError('required live artifact download coverage is incomplete')


def _pinned(root, path, digest):
    file = (root / path).resolve()
    if not file.is_relative_to(root.resolve()):
        raise ValueError('evidence path outside workspace')
    raw = file.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('pinned evidence SHA mismatch: ' + path)
    return json.loads(raw)


def generate(root=ROOT):
    root = Path(root)
    plan = json.loads((root / 'contracts/engineering_diagnosis.json').read_text(encoding='utf-8'))
    if plan.get('schema') != 'mini-drop.engineering-diagnosis-contract.v1' or plan.get('profile') != 'engineering-diagnosis.v1':
        raise ValueError('unsupported acceptance contract')
    registry = plan['scenarios']
    current_campaign = plan.get('current_campaign_id')
    if current_campaign is not None and (not isinstance(current_campaign, str) or not current_campaign.strip()):
        raise ValueError('invalid current campaign provenance')
    if len({s['scenario_id'] for s in registry}) != len(registry):
        raise ValueError('duplicate scenario registration')
    if any(s.get('domain') not in {*SIGNAL_FIELDS, 'cpu_hot_path'} for s in registry):
        raise ValueError('unregistered observation domain')
    pins = {}
    for entry in plan['evidence_manifests']:
        manifest = _pinned(root, entry['path'], entry['sha256'])
        directory = str(Path(entry['path']).parent).replace('\\', '/')
        # New batches add a manifest; previous evidence remains unchanged.
        for row in manifest['files']:
            path = (root / directory / row['path']).resolve()
            if not path.is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
                raise ValueError('archived evidence bytes differ from manifest')
            name = directory+'/'+row['path']
            if name in pins and pins[name] != row['sha256']:
                raise ValueError('conflicting evidence manifest entries')
            pins[name] = row['sha256']
    results = []
    pending = []
    for spec in registry:
        if not spec.get('case_path'):
            pending.append(spec['scenario_id'])
            continue
        if pins.get(spec['case_path']) != spec.get('case_sha256'):
            raise ValueError('case is not part of the pinned evidence manifest')
        case = _pinned(root, spec['case_path'], spec['case_sha256'])
        if case.get('scenario_id') != spec['scenario_id']:
            raise ValueError('case/scenario mismatch')
        _verify_required_downloads(root, case, spec, pins)
        result = evaluate_engineering_case(case, spec)
        result.update(title=case['title'], case_sha256=spec['case_sha256'],
            fresh_live_run=case.get('fresh_live_run') is True)
        if current_campaign is not None:
            campaign = spec.get('campaign_id')
            if not isinstance(campaign, str) or not campaign.strip():
                raise ValueError('selected case is missing its campaign provenance')
            original_live = case.get('fresh_live_run') is True
            if campaign == current_campaign and not original_live:
                raise ValueError('current campaign cannot promote a prior regrading into a fresh live trial')
            result.update(campaign_id=campaign, originally_live_record=original_live,
                          fresh_live_run=campaign == current_campaign and original_live)
        results.append(result)
    fresh = sum(r['fresh_live_run'] for r in results)
    document = {'schema': 'mini-drop.engineering-diagnosis-index.v1', 'profile': plan['profile'],
        'evaluation_mode': ('MIXED_LIVE_CAMPAIGNS' if fresh and fresh < len(results)
            else 'FRESH_LIVE_CAMPAIGN' if fresh else 'REGRADING_FROZEN_LIVE_RECORDS'),
        'fresh_live_run': bool(results) and fresh == len(results),
        'fresh_live_scenarios': fresh, 'regraded_prior_scenarios': len(results) - fresh,
        'registered_scenarios': len(registry), 'evaluated_scenarios': len(results),
        'diagnosis_accepted': sum(r['diagnosis_accepted'] for r in results),
        'localization_accepted': sum(r['localization_accepted'] for r in results),
        'refuted': sum(r['outcome'] == 'REFUTED' for r in results),
        'not_evaluated': pending, 'cases': results,
        'download_url': '/report-assets/engineering-diagnosis/index.json',
        'causal_and_fix_acceptance': 'NOT_EVALUATED_BY_THIS_PROFILE',
        'boundary': 'Engineering diagnosis response, not causal proof or whole-business health; historical results unchanged.'}
    if current_campaign is not None:
        document['current_campaign_id'] = current_campaign
        document['campaigns'] = [{'campaign_id': campaign,
            'evaluated_scenarios': sum(r['campaign_id'] == campaign for r in results),
            'current_campaign': campaign == current_campaign}
            for campaign in dict.fromkeys(r['campaign_id'] for r in results)]
    return json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False)+'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    expected = generate()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding='utf-8') != expected:
            raise SystemExit('engineering diagnosis index is stale; run its generator')
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(expected, encoding='utf-8')
    print('Versioned engineering diagnosis evidence and generated index verified')


if __name__ == '__main__':
    main()
