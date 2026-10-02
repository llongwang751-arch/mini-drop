"""Create/check the complete public evidence inventory without rewriting receipts."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / 'AGENTS.md').is_file())
REPORT = ROOT / 'reports/quality/planning-retrieval-v2-20261002'
DEST = REPORT / 'manifest.json'
HEAD = '73b4b18ad83553a5012a0025dfb78bb21ff8dc7f'

def read(name):
    return json.loads((REPORT / name).read_bytes())

def generate(created_at):
    runtime = read('deployment/prompt-final/r7-publication-verification.json')
    release = read('deployment/prompt-final/final-release-r7/manifest.json')
    main = read('ci/prompt-boundaries/main-final-36996499159/summary.json')
    core = read('ci/prompt-boundaries/clean-final-36996499257/summary.json')
    live = read('live/post-prompt/normal-smoke/summary.json')
    browser = read('live/post-prompt/browser/result.json')
    regression = read('retrieval-regression/regression-summary.json')
    first = read('evaluation/first-run/report.json')
    assert release['git_head'] == runtime['source_head'] == main['source_head'] == core['source_head'] == live['source_head'] == HEAD
    assert main['status'] == core['status'] == 'VERIFIED' and main['successful_jobs'] == 14
    assert runtime['healthy_containers'] == 13 and runtime['untouched_containers'] == 11
    assert runtime['public_web_sha_verified'] == 58
    assert browser['passed'] and browser['legacy_score_ui_absent']
    assert release['retrieval_regression_source_equivalence']['same_git_tree']
    assert regression['chat_calls_attempted'] == 0 and not regression['new_blind_evaluation']
    rows = []
    for path in sorted(REPORT.rglob('*')):
        if not path.is_file() or path == DEST:
            continue
        assert path.resolve().is_relative_to(REPORT.resolve()) and not path.is_symlink()
        raw = path.read_bytes()
        rows.append({'path': path.relative_to(REPORT).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
    return {'schema': 'mini-drop.planning-retrieval-final-evidence.v1',
        'status': 'DELIVERED_WITH_REMAINING_EFFECTIVENESS_GAPS', 'created_at': created_at,
        'source_head': HEAD, 'release_tag': release['release_tag'],
        'scope': 'Exact application release, original failed and successful CI, frozen first evaluation, exposed retrieval regression and separate real Agent/browser cohorts. Deployment health is not model effectiveness or causal diagnosis accuracy.',
        'main_ci': {'run_id': main['run_id'], 'successful_jobs': 14, 'counts': main['main_suite_counts']},
        'clean_core_ci': {'run_id': core['run_id'], 'gate_counts': core['gate_counts'], 'actual_report_status': core['actual_report_status']},
        'first_frozen_model_metrics': first['metrics'],
        'exposed_retrieval_regression': {'source_head': regression['source_head'], 'new_blind_evaluation': False, 'chat_calls_attempted': 0, 'metrics': regression['regression_metrics'], 'same_implementation_at_final_release': True},
        'actual_post_prompt_normal': {'status': live['status'], 'expected_disposition': live['cases'][0]['expected_disposition'], 'actual_disposition': live['cases'][0].get('actual_disposition'), 'planner_invocations': live['planner_invocations'], 'new_persisted_tasks': sum(r['persisted_tasks'] for r in live['persisted_task_counts']), 'health_check_performed': False, 'causal_root_cause_verified': False},
        'browser': {'status': browser['status'], 'rendered_live_cases': browser['rendered_live_cases'], 'total_live_cases': browser['total_live_cases'], 'layouts': len(browser['layouts']), 'verified_downloads': len(browser['verified_downloads']), 'normal_certificate_verification': browser['normal_certificate_verification']},
        'file_count': len(rows), 'total_bytes': sum(r['bytes'] for r in rows),
        'immutable_child_manifests': [r for r in rows if Path(r['path']).name in {'manifest.json', 'archive-manifest.json'}],
        'excluded': ['credentials', 'private release env and rollback compose', 'database/object data', 'runtime source bundles', 'unrelated user changes and personal resumes'],
        'generator': 'publication/build_final_evidence_manifest.py', 'files': rows}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        current = json.loads(DEST.read_bytes())
        assert generate(current['created_at']) == current, 'public evidence changed after final inventory'
        print(json.dumps({'status': 'VERIFIED_EXACT_FINAL_INVENTORY', 'files': current['file_count'], 'source_head': HEAD}))
    else:
        assert not DEST.exists(), 'preserve the published final inventory'
        result = generate(datetime.now(timezone.utc).isoformat())
        DEST.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
        print(json.dumps({'status': result['status'], 'files': result['file_count'], 'bytes': result['total_bytes'], 'source_head': HEAD}))

if __name__ == '__main__':
    main()
