"""Real PostgreSQL authority for check completion, interruption and late results."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from copy import deepcopy
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier

import pytest
from sqlalchemy import select

from server.app.drop_insight import service
from server.app.drop_insight.schemas import CreateDiagnosisRequestV2
from server.app.models import (
    AgentModel, TaskModel, DropInsightSessionModel, DropInsightHypothesisModel,
    DropInsightToolCallModel, DropInsightEvidenceModel, DropInsightEventModel,
    DropInsightReportModel,
)
from tests.test_health_check_flow import TARGET, evidence


def seed(factory, *, status='DONE', imported=True):
    diagnosis=service.create_diagnosis(CreateDiagnosisRequestV2(query='检查当前状态',health_check=True),
        created_by='test:health-postgres',managed_service_id='isolated-test')
    now=service.now_utc()
    with factory.begin() as session:
        row=session.get(DropInsightSessionModel,diagnosis.id)
        row.target_json=deepcopy(TARGET);row.status='COLLECTING_EVIDENCE'
        session.add(AgentModel(id='health-agent',hostname='isolated',ip_addr='127.0.0.1',
            created_at=now,updated_at=now,last_heartbeat_at=now));session.flush()
        session.add(TaskModel(id='health-task',name='test',agent_id='health-agent',target_pid=100,
            collector_type='sys_metrics',status=status,created_at=now,updated_at=now))
        session.add(DropInsightHypothesisModel(id='health-hypothesis',diagnosis_id=diagnosis.id,
            statement='只测量当前范围',status='OPEN',source='RULE',round_index=1,created_at=now,updated_at=now))
        session.flush()
        session.add(DropInsightToolCallModel(id='health-call',diagnosis_id=diagnosis.id,
            hypothesis_id='health-hypothesis',tool_name='collect_sys_metrics',arguments_json={},
            policy_decision='ALLOW',policy_reason='isolated',status='COMPLETED' if imported else 'RUNNING',
            terminal_processing_status='EVIDENCE_IMPORTED' if imported else 'NONE',
            task_id='health-task',requested_by='test',created_at=now))
        if imported:
            e=evidence()
            session.add(DropInsightEvidenceModel(id=e['evidence_id'],diagnosis_id=diagnosis.id,
                hypothesis_id='health-hypothesis',role='NEUTRAL',envelope_json=e['envelope'],
                classification_json=e['classification'],created_at=now))
    return diagnosis.id


def inspect(factory,did):
    with factory() as session:
        row=session.get(DropInsightSessionModel,did)
        return {'status':row.status,'version':row.version,
            'events':session.query(DropInsightEventModel).filter_by(diagnosis_id=did,event_type='health_check.completed').count(),
            'reports':session.query(DropInsightReportModel).filter_by(diagnosis_id=did).count(),
            'phase':session.get(DropInsightToolCallModel,'health-call').terminal_processing_status}


def test_concurrent_completion_waits_for_parent_lock_and_commits_once(postgres_sessions):
    did=seed(postgres_sessions)
    holder=postgres_sessions()
    holder.execute(select(DropInsightSessionModel).where(DropInsightSessionModel.id==did).with_for_update()).scalar_one()
    barrier=Barrier(3)
    def finish():
        barrier.wait(timeout=5)
        return service._finish_health_check(did,'health-call')
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures=[executor.submit(finish) for _ in range(2)]
            barrier.wait(timeout=5)
            for f in futures:
                with pytest.raises(TimeoutError):f.result(timeout=.15)
            holder.commit()
            results=[f.result(timeout=6) for f in futures]
    finally:holder.close()
    assert results[0]==results[1] and results[0]['code']=='NORMAL_OBSERVED'
    state=inspect(postgres_sessions,did)
    assert state=={'status':'COMPLETED','version':2,'events':1,'reports':0,'phase':'REPORT_EFFECTS_DONE'}


def test_exception_rolls_back_event_terminal_status_and_call_phase(postgres_sessions,monkeypatch):
    did=seed(postgres_sessions);before=inspect(postgres_sessions,did)
    original=service._append_event
    def fail(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('simulated worker failure before transaction commit')
    with monkeypatch.context() as patch:
        patch.setattr(service,'_append_event',fail)
        with pytest.raises(RuntimeError,match='worker failure'):service._finish_health_check(did,'health-call')
    assert inspect(postgres_sessions,did)==before
    assert service._finish_health_check(did,'health-call')['code']=='NORMAL_OBSERVED'
    assert inspect(postgres_sessions,did)['events']==1


CHILD = '''
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from server.app.drop_insight import service
engine=create_engine(os.environ['HEALTH_TEST_URL'],connect_args={'options':'-csearch_path='+os.environ['HEALTH_TEST_SCHEMA']+' -cstatement_timeout=5000'})
service.new_session=sessionmaker(bind=engine,expire_on_commit=False,autoflush=False)
if os.environ['HEALTH_TEST_CRASH']=='before':
    append=service._append_event
    def fail(*args,**kwargs):
        append(*args,**kwargs)
        os._exit(73)
    service._append_event=fail
result=service._finish_health_check(os.environ['HEALTH_TEST_ID'],'health-call')
assert result['code']=='NORMAL_OBSERVED'
os._exit(73 if os.environ['HEALTH_TEST_CRASH']=='after' else 0)
'''


@pytest.mark.parametrize('crash', ['before','after'])
def test_process_death_and_fresh_process_retry_preserve_atomic_completion(postgres_sessions,crash):
    did=seed(postgres_sessions);engine=postgres_sessions.kw['bind']
    with engine.connect() as connection:schema=connection.exec_driver_sql('SELECT current_schema()').scalar_one()
    env=dict(os.environ,HEALTH_TEST_URL=engine.url.render_as_string(hide_password=False),
        HEALTH_TEST_SCHEMA=schema,HEALTH_TEST_ID=did,HEALTH_TEST_CRASH=crash)
    cwd=Path(__file__).resolve().parents[1]
    first=subprocess.run([sys.executable,'-c',CHILD],cwd=cwd,env=env,capture_output=True,timeout=30)
    assert first.returncode==73, 'Isolated worker did not reach its controlled interruption'
    state=inspect(postgres_sessions,did)
    assert state['events']==(0 if crash=='before' else 1)
    assert state['status']==('COLLECTING_EVIDENCE' if crash=='before' else 'COMPLETED')
    env['HEALTH_TEST_CRASH']='none'
    retry=subprocess.run([sys.executable,'-c',CHILD],cwd=cwd,env=env,capture_output=True,timeout=30)
    assert retry.returncode==0, 'Fresh isolated worker did not finish the saved check'
    assert inspect(postgres_sessions,did)=={'status':'COMPLETED','version':2,'events':1,'reports':0,'phase':'REPORT_EFFECTS_DONE'}


def test_cancellation_wins_against_a_waiting_completion(postgres_sessions):
    did=seed(postgres_sessions)
    holder=postgres_sessions()
    row=holder.execute(select(DropInsightSessionModel).where(DropInsightSessionModel.id==did).with_for_update()).scalar_one()
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending=executor.submit(service._finish_health_check,did,'health-call')
        try:
            with pytest.raises(TimeoutError):pending.result(timeout=.2)
            # Persist a winning terminal cancellation while the completion waits.
            row.status='CANCELLED';row.version+=1;holder.commit()
            assert pending.result(timeout=6) is None
        finally:holder.close()
    assert inspect(postgres_sessions,did)['events']==0
    assert inspect(postgres_sessions,did)['status']=='CANCELLED'


def test_running_sample_cannot_finish_and_import_requires_durable_phase(postgres_sessions):
    did=seed(postgres_sessions,status='RUNNING',imported=False)
    assert service._finish_health_check(did,'health-call') is None
    with postgres_sessions.begin() as session:session.get(TaskModel,'health-task').status='DONE'
    assert service._finish_health_check(did,'health-call') is None
    assert inspect(postgres_sessions,did)['events']==0
    # A failed execution is explicitly unknown, never a fabricated normal result.
    with postgres_sessions.begin() as session:session.get(TaskModel,'health-task').status='FAILED'
    assert service._finish_health_check(did,'health-call')['code']=='INSUFFICIENT_OBSERVABILITY'
    state=inspect(postgres_sessions,did)
    assert state['status']=='INSUFFICIENT_EVIDENCE' and state['events']==1 and state['reports']==0
