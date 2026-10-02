from datetime import datetime, timezone

from server.app.agent_runtime.memory import project_investigation_memory, load_investigation_memory


def test_working_notebook_preserves_rejection_and_truncation():
    rows = [{'evidence_id': str(i), 'classification': {'decision': 'REJECT'},
             'envelope': {'source': {'artifact_id': 'artifact'},
                          'observation': {'text': 'x' * 2000}}} for i in range(10)]
    notebook = project_investigation_memory(
        {'report_id': 'report', 'verification': {'status': 'PARTIAL_WITHOUT_COUNTER'}}, rows)
    assert notebook['is_evidence'] is False
    assert notebook['omitted_evidence_count'] == 2
    assert notebook['verification']['status'] == 'PARTIAL_WITHOUT_COUNTER'
    assert all(r['classification']['decision'] == 'REJECT' and r['observation_truncated']
               for r in notebook['observations'])
    assert all(r['source']['artifact_id'] == 'artifact' for r in notebook['observations'])


def test_working_memory_reads_only_current_investigation(monkeypatch):
    from server.app.database import init_db, reset_engine, new_session
    from server.app.models import DropInsightSessionModel, DropInsightReportModel, DropInsightEvidenceModel
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///:memory:')
    reset_engine()
    init_db()
    now = datetime.now(timezone.utc)
    try:
        with new_session() as session:
            for name in ('current', 'other'):
                session.add(DropInsightSessionModel(id=name, query='CPU', created_by=name,
                    mode='AUTONOMOUS', status='COMPLETED', created_at=now, updated_at=now))
                session.flush()
                session.add(DropInsightReportModel(id='r-'+name, diagnosis_id=name,
                    conclusion='insufficient', confidence=0, created_at=now,
                    verification_json={'status': 'INSUFFICIENT_EVIDENCE'}))
                session.add(DropInsightEvidenceModel(id='e-'+name, diagnosis_id=name,
                    role='NEUTRAL', classification_json={'decision': 'REJECT'},
                    envelope_json={'observation': {'private': name}}, created_at=now))
            session.commit()
        memory = load_investigation_memory('current')
        assert memory['latest_report_id'] == 'r-current'
        assert [r['evidence_id'] for r in memory['observations']] == ['e-current']
        assert memory['status'] == 'READY'
    finally:
        reset_engine()
