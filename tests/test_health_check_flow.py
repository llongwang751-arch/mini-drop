"""A healthy check ends without manufacturing hypothesis support or a root report."""
from copy import deepcopy

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight import service
from server.app.drop_insight.health_assessment import assess_health_window
from server.app.drop_insight.schemas import CreateDiagnosisRequestV2
from server.app.models import (AgentModel, TaskModel, DropInsightSessionModel,
    DropInsightHypothesisModel, DropInsightToolCallModel, DropInsightEvidenceModel,
    DropInsightReportModel, DropInsightEventModel)

TARGET = {'agent_id': 'health-agent', 'pid': 100,
          'process_binding': {'boot_id': 'boot', 'process_start_ticks': 1000}}


def evidence(cpu=10):
    return {'evidence_id': 'health-evidence', 'classification': {'decision': 'ACCEPT_NEUTRAL'},
        'envelope': {'scope': {'agent_id': 'health-agent', 'pid': 100},
            'source': {'tool_name': 'sys_metrics', 'task_id': 'health-task', 'task_attempt_id': 'attempt',
                       'artifact_id': 'artifact', 'analysis_job_id': 'analysis', 'artifact_sha256': 'a'*64},
            'quality': {'schema_valid': True, 'analyzer_validated': True, 'target_match': True,
                        'time_overlap': True, 'degraded': False},
            'observation': {'metadata': {'sample_count': 15, 'window_duration_seconds': 14,
                'process_identity': {'pid': 100, 'start_ticks': 1000, 'verified': True},
                'summary': {'process_cpu_core_usage': cpu, 'vmrss_mb': 100, 'vmrss_mb_delta': 0}}}}}


@pytest.mark.parametrize('cpu,code', [(10,'NORMAL_OBSERVED'), (50,'ANOMALY_OBSERVED'),
    (240,'ANOMALY_OBSERVED'), (True,'INSUFFICIENT_OBSERVABILITY'),
    (None,'INSUFFICIENT_OBSERVABILITY'), (float('inf'),'INSUFFICIENT_OBSERVABILITY')])
def test_measured_outcome_never_requires_positive_root_support(cpu, code):
    result = assess_health_window(evidence(cpu), TARGET)
    assert result['code'] == code and result['causal_root_cause_verified'] is False


@pytest.mark.parametrize('fault', ['pid','start','agent','sha','analyzer','samples','duration','rss',
                                 'degraded','truncated','admission','attempt'])
def test_missing_or_untrusted_data_never_reports_normal(fault):
    e=evidence();m=e['envelope']['observation']['metadata'];q=e['envelope']['quality']
    if fault=='pid': m['process_identity']['pid']=999
    elif fault=='start': m['process_identity']['start_ticks']=999
    elif fault=='agent': e['envelope']['scope']['agent_id']='other'
    elif fault=='sha': e['envelope']['source']['artifact_sha256']='not-a-sha'
    elif fault=='analyzer': q['analyzer_validated']=False
    elif fault=='samples': m['sample_count']=1
    elif fault=='duration': m['window_duration_seconds']=0
    elif fault=='rss': m['summary'].pop('vmrss_mb_delta')
    elif fault in {'degraded','truncated'}: q[fault]=True
    elif fault=='admission': e['classification']['decision']='REJECTED'
    else: e['envelope']['source'].pop('task_attempt_id')
    assert assess_health_window(e,TARGET)['code']=='INSUFFICIENT_OBSERVABILITY'


def test_http_failure_is_detected_but_no_requests_does_not_claim_http_health():
    e=evidence();m=e['envelope']['observation']['metadata']
    m['application_metrics']={'identity':{'identity_verified':True},
        'delta':{'http_requests':10,'http_failures':1,'http_duration_ms':100},
        'max':{'http_recent_p95_latency_ms':20}}
    assert assess_health_window(e,TARGET)['code']=='ANOMALY_OBSERVED'
    m['application_metrics']['delta'].update(http_requests=0,http_failures=0)
    r=assess_health_window(e,TARGET)
    assert r['code']=='NORMAL_OBSERVED' and len(r['checked'])==2


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setenv('DATABASE_URL','sqlite:///:memory:');reset_engine();init_db()
    yield
    reset_engine()


def seed(*, cpu=10, task_status='DONE', terminal='EVIDENCE_IMPORTED', health=True, status='COLLECTING_EVIDENCE'):
    d=service.create_diagnosis(CreateDiagnosisRequestV2(query='检查当前状态',health_check=health),
        created_by='test:operator',managed_service_id='agi-office-backend')
    t=service.now_utc()
    with new_session() as s:
        row=s.get(DropInsightSessionModel,d.id);row.target_json=deepcopy(TARGET);row.status=status
        s.add(AgentModel(id='health-agent',hostname='host',ip_addr='127.0.0.1',last_heartbeat_at=t,created_at=t,updated_at=t));s.flush()
        s.add(TaskModel(id='health-task',name='health',agent_id='health-agent',target_pid=100,
            collector_type='sys_metrics',status=task_status,created_at=t,updated_at=t))
        s.add(DropInsightHypothesisModel(id='health-hypothesis',diagnosis_id=d.id,statement='建立当前系统基线',
            expected_observations_json=[],falsification_criteria_json=[],status='OPEN',source='RULE',
            round_index=1,created_at=t,updated_at=t));s.flush()
        s.add(DropInsightToolCallModel(id='health-call',diagnosis_id=d.id,hypothesis_id='health-hypothesis',
            tool_name='collect_sys_metrics',arguments_json={},policy_decision='ALLOW',policy_reason='test',
            status='COMPLETED' if task_status=='DONE' else 'RUNNING',task_id='health-task',requested_by='test',
            terminal_processing_status=terminal,created_at=t))
        e=evidence(cpu)
        s.add(DropInsightEvidenceModel(id=e['evidence_id'],diagnosis_id=d.id,hypothesis_id='health-hypothesis',
            role='NEUTRAL',envelope_json=e['envelope'],classification_json=e['classification'],created_at=t))
        s.commit()
    return d.id


@pytest.mark.parametrize('cpu,code,status', [(10,'NORMAL_OBSERVED','COMPLETED'),
    (80,'ANOMALY_OBSERVED','COMPLETED'), (None,'INSUFFICIENT_OBSERVABILITY','INSUFFICIENT_EVIDENCE')])
def test_check_result_is_durable_idempotent_and_does_not_create_a_root_report(db,cpu,code,status):
    did=seed(cpu=cpu)
    first=service._finish_health_check(did,'health-call')
    assert first['code']==code
    version=service.get_diagnosis(did).version
    assert service._finish_health_check(did,'health-call')==first
    with new_session() as s:
        assert s.get(DropInsightSessionModel,did).status==status
        assert s.get(DropInsightSessionModel,did).version==version
        assert s.query(DropInsightReportModel).count()==0
        assert s.query(DropInsightEventModel).filter_by(event_type='health_check.completed').count()==1
        assert s.get(DropInsightToolCallModel,'health-call').terminal_processing_status=='REPORT_EFFECTS_DONE'
        assert s.get(DropInsightHypothesisModel,'health-hypothesis').status=='OPEN'


@pytest.mark.parametrize('status', ['CANCELLED','FAILED','COMPLETED','INSUFFICIENT_EVIDENCE'])
def test_late_window_does_not_rewrite_terminal_history(db,status):
    did=seed(status=status)
    assert service._finish_health_check(did,'health-call') is None
    assert service.get_diagnosis(did).status==status


def test_an_incident_is_not_short_circuited_as_a_health_check(db):
    did=seed(health=False)
    assert service._finish_health_check(did,'health-call') is None


def test_advance_completes_health_without_cpu_control_or_root_report(db,monkeypatch):
    did=seed()
    monkeypatch.setattr(service,'import_task_evidence',lambda *args,**kwargs: [])
    monkeypatch.setattr(service,'generate_report',lambda *args,**kwargs: pytest.fail('health check must not invent a root report'))
    monkeypatch.setattr(service,'_request_cpu_control_before_report',lambda *args: pytest.fail('healthy baseline does not require causal control'))
    monkeypatch.setattr(service,'_score_candidate_hypotheses',lambda *args: pytest.fail('health check must not score root hypotheses'))
    result=service.advance_diagnosis(did)
    assert result['actions'][0]['action']=='HEALTH_CHECK_COMPLETED'
    assert service.get_diagnosis(did).status=='COMPLETED'
    assert service.advance_diagnosis(did)['actions']==[]


def test_failed_sampling_finishes_unknown_without_replanning(db,monkeypatch):
    did=seed(task_status='FAILED',terminal='NONE')
    monkeypatch.setattr(service,'_replan_after_insufficient_evidence',lambda *args: pytest.fail('failed check must offer explicit resampling'))
    monkeypatch.setattr(service,'_score_candidate_hypotheses',lambda *args: pytest.fail('health check must not score root hypotheses'))
    result=service.advance_diagnosis(did)
    assert result['actions'][0]['check_result']['code']=='INSUFFICIENT_OBSERVABILITY'
    assert service.get_diagnosis(did).status=='INSUFFICIENT_EVIDENCE'
