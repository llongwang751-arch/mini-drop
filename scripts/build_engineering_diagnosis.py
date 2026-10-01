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
        result = evaluate_engineering_case(case, spec)
        result.update(title=case['title'], case_sha256=spec['case_sha256'],
            fresh_live_run=case.get('fresh_live_run') is True)
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
