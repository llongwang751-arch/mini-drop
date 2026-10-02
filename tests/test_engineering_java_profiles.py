"""Frozen new live Java measurements must survive independent numeric validation."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.evaluate_engineering_diagnosis import evaluate_engineering_case

ROOT=Path(__file__).resolve().parents[1]


def fixture(sid):
    case=json.loads((ROOT/'reports/quality/interview-completion-20261001/performance-18-cases'/f'{sid}.json').read_text(encoding='utf-8'))
    plan=json.loads((ROOT/'contracts/engineering_diagnosis.json').read_text(encoding='utf-8'))
    spec=next(s for s in plan['scenarios'] if s['scenario_id']==sid)
    return case,spec


@pytest.mark.parametrize('sid',['java-gc-pressure','java-lock-contention'])
def test_real_java_profile_is_a_bounded_engineering_observation(sid):
    case,spec=fixture(sid);original=deepcopy(case)
    result=evaluate_engineering_case(case,spec)
    assert result['diagnosis_accepted'] is True
    assert result['outcome']=='SUPPORTED_OBSERVATION'
    assert result['localization_accepted'] is False
    assert result['causal_root_cause_verified'] is False
    assert case==original and case['engineering_evaluation']['diagnosis_accepted'] is False


@pytest.mark.parametrize('fault',['collector','event','missing_top','percent','sample_bool','wrong_domain',
    'gc_missing_counter','gc_missing_value','gc_forged_delta','framework_wrapper'])
def test_java_measurement_rejects_wrong_domains_missing_counters_or_tampered_numbers(fault):
    case,spec=fixture('java-gc-pressure')
    ev=next(e for e in case['records']['evidence'] if e.get('role')=='SUPPORT')
    m=ev['envelope']['observation']['metadata'];metrics=m['hypothesis_predicate']['metrics']
    if fault=='collector':ev['envelope']['source']['tool_name']='pyspy'
    elif fault=='event':m['profile_event']='lock'
    elif fault=='missing_top':m['top_functions']=[]
    elif fault=='percent':metrics['dominant_percent']=100
    elif fault=='sample_bool':m['sample_count']=True
    elif fault=='wrong_domain':spec['domain']='network_latency'
    elif fault=='gc_missing_counter':case['records']['evidence']=[ev]
    elif fault=='gc_missing_value':m['jvm_gc_counters']['delta'].pop('allocated_bytes')
    elif fault=='gc_forged_delta':m['jvm_gc_counters']['delta']['gc_count']+=1
    else:
        metrics['dominant_function']='java/lang/Thread.run';metrics['dominant_percent']=100
    assert evaluate_engineering_case(case,spec)['diagnosis_accepted'] is False


def test_cpu_refutation_does_not_refute_a_measured_java_lock_wait():
    case,spec=fixture('java-lock-contention')
    assert any(r.get('counter_evidence_refs') for r in case['records']['reports'])
    assert evaluate_engineering_case(case,spec)['diagnosis_accepted'] is True
    # A valid refutation in the same domain must still block positive support.
    for e in case['records']['evidence']:
        if e.get('role')=='COUNTER':
            predicate=e['envelope']['observation']['metadata'].get('hypothesis_predicate')
            if predicate:predicate['signal']='lock_contention'
    assert evaluate_engineering_case(case,spec)['diagnosis_accepted'] is False


def test_python_profile_and_independent_os_counters_are_recomputed():
    case,spec=fixture('cpu-hotspot')
    assert evaluate_engineering_case(case,spec)['diagnosis_accepted'] is True


@pytest.mark.parametrize('fault',['location','samples','concentration','os_ticks','missing_os','low_cpu','wrong_python_domain'])
def test_python_profile_rejects_tampered_locations_and_unverified_cpu(fault):
    case,spec=fixture('cpu-hotspot')
    e=next(e for e in case['records']['evidence'] if e.get('role')=='SUPPORT')
    m=e['envelope']['observation']['metadata'];metrics=m['hypothesis_predicate']['metrics']
    if fault in {'location','samples','concentration'}:
        # The same task was imported for two hypotheses; corrupt both copies.
        for profile in case['records']['evidence']:
            if profile.get('role')!='SUPPORT':continue
            pm=profile['envelope']['observation']['metadata'];pv=pm['hypothesis_predicate']['metrics']
            if fault=='location':pv['concentrated_functions'][0]['locations'][0]['line']+=10000
            elif fault=='samples':pm['top_functions'][0]['samples']=1
            else:pv['concentrated_percent']=100
    elif fault=='wrong_python_domain':spec['domain']='network_latency'
    else:
        for other in case['records']['evidence']:
            if other['envelope']['source']['tool_name']=='sys_metrics':
                v=other['envelope']['observation']['metadata'].get('hypothesis_predicate',{}).get('metrics',{})
                if v.get('source')!='linux_proc_stat':continue
                if fault=='os_ticks':v['end_cpu_ticks']+=10000
                elif fault=='missing_os':v.pop('start_unix_ms')
                else:
                    v['end_cpu_ticks']=v['start_cpu_ticks']
                    v['process_cpu_core_usage']=0
    assert evaluate_engineering_case(case,spec)['diagnosis_accepted'] is False
