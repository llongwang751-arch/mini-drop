from copy import deepcopy
from types import SimpleNamespace

import pytest

from server.app.drop_insight.claim_verifier import verify_report_claims
from server.app.drop_insight.evidence import EvidenceEnvelope
from server.app.drop_insight.hypothesis_predicate import _structured_signal_predicate
from server.app.drop_insight.performance_criteria import performance_observation_plan


def measured(plan, value, name='one'):
    signal, tail = plan['expected'][0].split('.', 1)
    field = tail.split()[0]
    h = SimpleNamespace(expected_observations_json=plan['expected'], falsification_criteria_json=plan['falsification'])
    metadata = {'schema_version': 'sys_metrics_analysis.v2', 'sample_count': 10,
                'process_identity': {'verified': True, 'pid': 123, 'start_ticks': 42},
                'signals': {signal: {'metrics': {field: value}}}}
    metadata['hypothesis_predicate'] = _structured_signal_predicate(h, metadata)
    return EvidenceEnvelope(evidence_id=name, diagnosis_id='diagnosis', evidence_type='SYS_METRICS',
        source={'tool_name': 'sys_metrics', 'task_id': name, 'task_attempt_id': name,
                'artifact_id': name, 'artifact_sha256': 'a' * 64, 'analysis_job_id': name, 'analyzer_version': '1',
                'analyzer_output_schema_version': 'sys_metrics_analysis.v2'},
        scope={'agent_id': 'agent', 'pid': 123},
        time_range={'start': '2026-10-01T00:00:00Z', 'end': '2026-10-01T00:00:10Z'},
        observation={'metadata': metadata},
        quality={'level': 'HIGH', 'sample_count': 10, 'schema_valid': True, 'analyzer_validated': True,
                 'target_match': True, 'time_overlap': True, 'degraded': False})


def verify(plan, *envelopes):
    return verify_report_claims([(e.observation['metadata']['hypothesis_predicate']['outcome'], e) for e in envelopes],
        expected_observations=plan['expected'], falsification_criteria=plan['falsification'])


@pytest.mark.parametrize('category', ['DOWNSTREAM_DEPENDENCY', 'NETWORK_LATENCY', 'QUEUE_CONGESTION', 'LOAD_SATURATION', 'MEMORY_PRESSURE'])
def test_abnormal_numeric_plan_is_checkable_without_requiring_its_opposite_to_be_true(category):
    plan = performance_observation_plan(category)
    threshold = float(plan['expected'][0].split()[-1])
    result = verify(plan, measured(plan, threshold * 2))
    observation = result['verification']['observation_verification']
    assert observation['status'] == 'VERIFIED'
    assert observation['checked_ratio'] == 1
    assert [item['matches'] for item in observation['criteria']] == [True, False]
    assert result['coverage_ratio'] == .5
    assert result['has_independent_counter_or_control'] is False
    assert result['status'] == 'PARTIAL_WITHOUT_COUNTER'


@pytest.mark.parametrize('rejection', ['schema_valid', 'analyzer_validated', 'target_match', 'time_overlap', 'degraded', 'sample_count', 'artifact_sha256', 'pid'])
def test_observation_check_cannot_bypass_trust_quality_or_target_gates(rejection):
    plan = performance_observation_plan('NETWORK_LATENCY')
    e = measured(plan, 200)
    if rejection == 'artifact_sha256': e.source.artifact_sha256 = ''
    elif rejection == 'pid': e.observation['metadata']['process_identity']['pid'] = 999
    elif rejection == 'sample_count': e.quality.sample_count = 2
    else: setattr(e.quality, rejection, rejection == 'degraded')
    assert verify(plan, e)['verification']['observation_verification']['status'] != 'VERIFIED'


def test_unknown_slot_is_unchecked_and_conflicting_windows_are_not_merged():
    plan = performance_observation_plan('NETWORK_LATENCY')
    high, low = measured(plan, 200), measured(plan, 20, 'two')
    conflict = verify(plan, high, low)['verification']['observation_verification']
    assert conflict['status'] == 'CONFLICTING_OBSERVATIONS'
    plan = deepcopy(plan)
    plan['expected'].append('packet loss explains the HTTP wait')
    assert verify(plan, high)['verification']['observation_verification']['status'] == 'UNSUPPORTED_PLAN'


def test_measured_refutation_and_missing_metric_are_distinct():
    plan = performance_observation_plan('NETWORK_LATENCY')
    assert verify(plan, measured(plan, 20))['verification']['observation_verification']['status'] == 'REFUTED'
    e = measured(plan, 200)
    e.observation['metadata']['signals']['network_latency']['metrics'].clear()
    assert verify(plan, e)['verification']['observation_verification']['checked_ratio'] == 0


@pytest.fixture
def persisted_observation(monkeypatch):
    from datetime import datetime
    from server.app.database import init_db, new_session, reset_engine
    from server.app.drop_insight import service
    from server.app.models import DropInsightSessionModel, DropInsightHypothesisModel, DropInsightEvidenceModel, DropInsightReportModel
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///:memory:')
    reset_engine();init_db()
    monkeypatch.setattr(service, 'map_hot_functions', lambda *_a, **_k: {})
    def no_dispatch(report_id):
        with new_session() as db:
            report=db.get(DropInsightReportModel, report_id);db.expunge(report);return report
    monkeypatch.setattr(service, '_apply_report_effects', no_dispatch)
    def save(plan, e):
        now=datetime(2026,10,1)
        with new_session() as db:
            db.add(DropInsightSessionModel(id='diagnosis', query='Go performance observation',
                target_json={'agent_id':'agent','pid':123,'runtime':'GO'},
                time_range_json={'start':'2026-10-01T00:00:00Z','end':'2026-10-01T00:00:10Z'},
                mode='AUTONOMOUS',status='COLLECTING_EVIDENCE',version=1,budget_json={},created_at=now,updated_at=now))
            db.add(DropInsightHypothesisModel(id='hypothesis',diagnosis_id='diagnosis',statement=plan['statement'],
                expected_observations_json=plan['expected'],falsification_criteria_json=plan['falsification'],
                status='OPEN',source='RULE',round_index=1,created_at=now,updated_at=now))
            db.add(DropInsightEvidenceModel(id=e.evidence_id,diagnosis_id='diagnosis',hypothesis_id='hypothesis',
                role=e.observation['metadata']['hypothesis_predicate']['outcome'],envelope_json=e.model_dump(mode='json'),
                classification_json={'decision':'ACCEPT_SUPPORT'},created_at=now))
            db.commit()
    yield save
    reset_engine()


def test_persisted_numeric_observation_is_verified_but_cannot_pass_causal_gate(persisted_observation):
    from server.app.drop_insight import service
    from server.app.drop_insight.schemas import GenerateReportRequest
    from scripts.run_fault_plaza_strict_acceptance import evaluate_reports
    plan=performance_observation_plan('NETWORK_LATENCY')
    persisted_observation(plan,measured(plan,240))
    report=service.generate_report('diagnosis',GenerateReportRequest(hypothesis_id='hypothesis'))
    v=report.verification_json
    assert v['status']=='VERIFIED' and v['matched_verification_status']=='PARTIAL_WITHOUT_COUNTER'
    assert v['coverage_ratio']==.5 and v['observation_verification']['checked_ratio']==1
    assert v['has_independent_counter_or_control'] is False
    assert v['claim_scope']=='BOUNDED_OBSERVATION' and v['causal_root_cause_verified'] is False
    assert v['bottleneck_localization']['status']=='LOCALIZED'
    assert report.conclusion.startswith('已验证观测：')
    assert evaluate_reports('go-network-latency',[report.to_dict()])['root_cause_accepted'] is False


def test_persisted_normal_measurement_does_not_become_a_verified_bottleneck(persisted_observation):
    from server.app.drop_insight import service
    from server.app.drop_insight.schemas import GenerateReportRequest
    plan=performance_observation_plan('NETWORK_LATENCY')
    persisted_observation(plan,measured(plan,20))
    report=service.generate_report('diagnosis',GenerateReportRequest(hypothesis_id='hypothesis'))
    assert report.verification_json['observation_verification']['status']=='REFUTED'
    assert report.verification_json['bottleneck_localization']['status']=='NOT_LOCALIZED'


def test_target_io_requires_operation_count_and_explicit_application_scope():
    from server.app.drop_insight.bottleneck_localization import localize_verified_observation
    plan=performance_observation_plan('IO_LATENCY');e=measured(plan,30)
    signal=e.observation['metadata']['signals']['io_latency']
    signal['metrics']['operation_count_delta']=10
    signal['measurement_scope']='TARGET_APPLICATION_SYNC_IO'
    # Re-evaluate the full declared plan after adding the actual operation count.
    h=SimpleNamespace(expected_observations_json=plan['expected'],falsification_criteria_json=plan['falsification'])
    e.observation['metadata']['hypothesis_predicate']=_structured_signal_predicate(h,e.observation['metadata'])
    verification=verify(plan,e)['verification']
    verification.update(status='VERIFIED',claim_scope='BOUNDED_OBSERVATION',causal_root_cause_verified=False)
    assert localize_verified_observation(verification,[e])['status']=='LOCALIZED'
    del signal['measurement_scope']
    assert localize_verified_observation(verification,[e])['status']=='NOT_LOCALIZED'


def test_cpu_hot_path_needs_registered_profile_and_independent_cpu_observation():
    from server.app.drop_insight.bottleneck_localization import localize_verified_observation
    e=measured(performance_observation_plan('NETWORK_LATENCY'),240)
    e.observation['metadata']['schema_version']='go_pprof_analysis.v1'
    e.observation['metadata']['hypothesis_predicate']['metrics']={
        'dominant_function':'main.goCPUHotFunction','observation_contract':'go-profile-and-os-cpu.v1'}
    verification={'status':'VERIFIED','claim_scope':'BOUNDED_OBSERVATION','causal_root_cause_verified':False,
        'coverage_ratio':1,'has_independent_counter_or_control':True,
        'observation_contract':{'contract_id':'go-profile-and-os-cpu.v1'}}
    result=localize_verified_observation(verification,[e])
    assert result['location']=='main.goCPUHotFunction' and result['same_load_fix_verified'] is False
    verification['has_independent_counter_or_control']=False
    assert localize_verified_observation(verification,[e])['status']=='NOT_LOCALIZED'
    verification['has_independent_counter_or_control']=True
    e.observation['metadata']['schema_version']='sys_metrics_analysis.v2'
    assert localize_verified_observation(verification,[e])['status']=='NOT_LOCALIZED'
