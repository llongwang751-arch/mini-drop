"""Generate or check the aggregate inventory from immutable evidence subcontracts."""
from pathlib import Path
import argparse
import hashlib
import json

ROOT = next(parent for parent in Path(__file__).resolve().parents
            if (parent / 'contracts/interview_delivery.json').is_file())
PREFIX = ROOT / 'reports/quality/planning-boundary-v3-20261002'

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def pinned(name):
    path = PREFIX / name
    raw = path.read_bytes()
    return {'path': name, 'sha256': digest(raw)}, json.loads(raw)

def generate():
    release_pin, release = pinned('deployment/final/manifest.json')
    ci_pin, ci = pinned('ci/final/main-37004140437/summary.json')
    core_pin, core = pinned('ci/final/clean-37004140452/reports/clean-stack/report.json')
    runtime_pin, runtime = pinned('deployment/v3-publication-verification.json')
    knowledge_pin, knowledge = pinned('deployment/v3-knowledge-verification.json')
    live_pin, live = pinned('live/smoke/summary.json')
    readback_pin, readback = pinned('live/live-contract-readback.json')
    browser_pin, browser = pinned('live/browser/result.json')
    model_pin, model = pinned('evaluation/first-run/report.json')
    hybrid_pin, hybrid = pinned('retrieval/hybrid/report.json')
    audit_pin, audit = pinned('evaluation/independent-audit.json')
    head = release['git_head']
    assert ci['status'] == 'VERIFIED' and ci['source_head'] == head
    assert ci['successful_jobs'] == 14 and core['status'] == 'PASSED'
    assert runtime['source_head'] == model['source_head'] == hybrid['source_head'] == head
    assert all(item['source_head'] == head for item in [knowledge, live, readback, browser, audit])
    assert knowledge['knowledge_sha256_verified'] and knowledge['current']['chunks'] == 44
    assert readback['status'] == audit['status'] == 'VERIFIED'
    assert live['status'] == 'FAILED' and browser['passed']
    assert browser['status'] == 'PASSED_AVAILABLE_CASES' and browser['rendered_live_cases'] == 2
    assert browser['total_live_cases'] == readback['total_original_cases'] == 3
    assert readback['verified_planning_result_cases'] == 2 and readback['all_three_planning_results_passed'] is False
    assert model['actual_langgraph_agent_execution'] is False and model['server_deterministic_route_counted_as_model'] is False
    assert model['third_party_independent_author'] is False
    inventory = []
    for path in sorted(PREFIX.rglob('*')):
        if not path.is_file() or path == PREFIX / 'manifest.json':
            continue
        raw = path.read_bytes()
        inventory.append({'path': path.relative_to(PREFIX).as_posix(), 'bytes': len(raw), 'sha256': digest(raw)})
    return {
        'schema': 'mini-drop.planning-boundary-delivery.v3',
        'status': 'DELIVERED_WITH_MEASURED_EFFECTIVENESS_GAPS',
        'application_source_head': head,
        'release': release['release_tag'],
        'scope': 'Application source, measured CI, immutable knowledge snapshot, actual live contracts and read-only browser; independent frozen model/retrieval evaluation. Documentation-only publication is a later commit.',
        'claims': {
            'information_only_server_closure_verified': True,
            'server_normal_counted_as_model_success': False,
            'zero_model_count_scope': 'Diagnosis chat only; upstream HYBRID query embedding still occurs',
            'model_batch': {'case_count': model['metrics']['case_count'], 'metrics': model['metrics'],
                            'scope': model['scope'], 'evaluation_adapter': model['evaluation_adapter'],
                            'actual_langgraph_agent_execution': model['actual_langgraph_agent_execution'],
                            'third_party_independent_author': model['third_party_independent_author']},
            'hybrid': {'metrics': hybrid['metrics'], 'actual_backend_counts': hybrid['actual_backend_counts']},
            'live_original_status': live['status'],
            'live_available_outputs': browser['rendered_live_cases'],
            'live_attempts': browser['total_live_cases'],
            'raw_failures_retained': True,
            'independent_audit_is_metric_recomputation_not_perfect_accuracy': True,
            'independent_audit_scope': audit['independence_scope'],
            'causal_root_cause_score': False,
        },
        'sources': {'release': release_pin, 'main_ci': ci_pin, 'core_ci': core_pin, 'runtime': runtime_pin,
                    'knowledge': knowledge_pin, 'live_original': live_pin, 'live_readback': readback_pin,
                    'browser': browser_pin, 'model': model_pin, 'hybrid': hybrid_pin, 'independent_audit': audit_pin},
        'inventory_count': len(inventory),
        'inventory_bytes': sum(row['bytes'] for row in inventory),
        'inventory': inventory,
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    raw = (json.dumps(generate(), ensure_ascii=False, indent=2) + '\n').encode()
    target = PREFIX / 'manifest.json'
    if args.check:
        assert target.read_bytes() == raw, 'Delivery manifest differs from original evidence'
    else:
        target.write_bytes(raw)
    report = json.loads(raw)
    print(json.dumps({'status': 'VERIFIED' if args.check else 'GENERATED', 'files': report['inventory_count'],
                      'bytes': report['inventory_bytes'], 'sha256': digest(raw)}))

if __name__ == '__main__':
    main()
