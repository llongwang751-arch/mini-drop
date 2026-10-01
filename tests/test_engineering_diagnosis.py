"""Weaker engineering acceptance still rejects bad evidence and false claims."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.build_engineering_diagnosis import generate
from scripts.evaluate_engineering_diagnosis import evaluate_engineering_case

ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((ROOT/'contracts/engineering_diagnosis.json').read_text(encoding='utf-8'))


def fixture(sid='go-network-latency'):
    spec = next(s for s in PLAN['scenarios'] if s['scenario_id'] == sid)
    return json.loads((ROOT/spec['case_path']).read_text(encoding='utf-8')), spec


@pytest.mark.parametrize('sid,outcome', [('go-cpu-hotspot','LOCALIZED_ANOMALY'),
    ('go-network-latency','LOCALIZED_ANOMALY'), ('go-file-io','REFUTED')])
def test_frozen_live_decisions_pass_engineering_response_without_rewriting_causal_grade(sid, outcome):
    case, spec = fixture(sid)
    original = deepcopy(case)
    result = evaluate_engineering_case(case, spec)
    assert result['diagnosis_accepted'] is True
    assert result['outcome'] == outcome
    assert result['localization_accepted'] is (outcome == 'LOCALIZED_ANOMALY')
    assert result['causal_root_cause_verified'] is False
    assert case == original and case['root_cause_accepted'] is False


def test_independent_control_and_fixed_rounds_are_optional_for_engineering_measurement():
    case, spec = fixture()
    case['diagnosis_contract_verified'] = False
    for r in case['records']['reports']:
        r['verification']['has_independent_counter_or_control'] = False
        r['verification']['status'] = 'PARTIAL_WITHOUT_COUNTER'
    assert evaluate_engineering_case(case, spec)['diagnosis_accepted'] is True


@pytest.mark.parametrize('fault', ['target','hash','claim_value','measurement','domain','missing_reference',
    'unchecked','cleanup','recovery','running','canceled','missing_support','claim_direction','boolean_ratio'])
def test_relaxed_profile_does_not_accept_wrong_identity_tampered_measurements_or_unsafe_lifecycle(fault):
    case, spec = fixture()
    spec = deepcopy(spec)
    r = case['records']['reports'][-1]
    v = r['verification']
    if fault == 'target': case['target']['pid'] += 1
    elif fault == 'hash': v['claims'][0]['artifact_sha256'] = '0'*64
    elif fault == 'claim_value': v['claims'][0]['claimed_value'] = 'invented'
    elif fault == 'measurement': v['observation_verification']['criteria'][0]['measurement']['value'] = 999
    elif fault == 'domain': spec['domain'] = 'io_latency'
    elif fault == 'missing_reference': r['evidence_refs'] = ['unknown']
    elif fault == 'unchecked': v['observation_verification']['criteria'][0]['checked'] = False
    elif fault == 'cleanup': case['cleanup_verified'] = False
    elif fault == 'recovery': case['intervention']['recovery_observed'] = False
    elif fault == 'running': case['records']['diagnosis']['status'] = 'COLLECTING_EVIDENCE'
    elif fault == 'canceled': case['records']['diagnosis']['status'] = 'CANCELLED'
    elif fault == 'missing_support': v['claims'] = []
    elif fault == 'claim_direction': v['claims'][0]['direction'] = 'COUNTER'
    else: v['observation_verification']['checked_ratio'] = True
    assert evaluate_engineering_case(case, spec)['diagnosis_accepted'] is False


def test_new_registered_memory_signal_does_not_need_another_hardcoded_evaluator():
    case, _ = fixture()
    case['scenario_id'] = 'new-memory-case'
    report = case['records']['reports'][-1]
    obs = report['verification']['observation_verification']
    eid = report['evidence_refs'][0]
    evidence = next(e for e in case['records']['evidence'] if e['evidence_id'] == eid)
    metadata = evidence['envelope']['observation']['metadata']
    metadata['hypothesis_predicate']['signal'] = 'memory_growth'
    metadata['signals']['memory_growth'] = {'metrics': {'rss_delta_mb': 12}}
    for c, op, match in zip(obs['criteria'], ['>=','<'], [True,False]):
        c.update(criterion=f'memory_growth.rss_delta_mb {op} 8', matches=match,
            measurement={'signal':'memory_growth','field':'rss_delta_mb','value':12,'threshold':8.0,'matches':match})
    result = evaluate_engineering_case(case, {'domain':'memory_growth'})
    assert result['diagnosis_accepted'] and result['outcome'] == 'SUPPORTED_OBSERVATION'
    assert not result['localization_accepted']


def test_generated_view_distinguishes_new_trials_from_prior_regrading():
    document = json.loads(generate(ROOT))
    assert document['diagnosis_accepted'] == 14
    assert document['localization_accepted'] == 4 and document['refuted'] == 5
    assert document['registered_scenarios'] == document['evaluated_scenarios'] == 21
    assert document['not_evaluated'] == []
    assert document['evaluation_mode'] == 'MIXED_LIVE_CAMPAIGNS'
    assert document['fresh_live_scenarios'] == 18 and document['regraded_prior_scenarios'] == 3
    assert document['fresh_live_run'] is False


@pytest.mark.parametrize('fault', ['duplicate','unknown_domain','case_hash','manifest_hash'])
def test_generator_rejects_bad_source_contract(tmp_path, fault):
    plan = deepcopy(PLAN)
    if fault == 'duplicate': plan['scenarios'].append(deepcopy(plan['scenarios'][0]))
    elif fault == 'unknown_domain': plan['scenarios'][0]['domain'] = 'invented'
    elif fault == 'case_hash': next(s for s in plan['scenarios'] if s.get('case_path'))['case_sha256'] = '0'*64
    else: plan['evidence_manifests'][0]['sha256'] = '0'*64
    (tmp_path/'contracts').mkdir()
    (tmp_path/'contracts/engineering_diagnosis.json').write_text(json.dumps(plan),encoding='utf-8')
    # Bad hashes and missing pinned files both fail closed.
    with pytest.raises((ValueError,FileNotFoundError)):
        generate(tmp_path)
