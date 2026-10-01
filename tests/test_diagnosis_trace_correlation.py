"""Correlation labels survive scope clarification without authorizing a process."""
import pytest

from server.app.database import init_db,reset_engine
from server.app.drop_insight import service
from server.app.drop_insight.schemas import CreateDiagnosisRequestV2,ClarifyDiagnosisRequest


@pytest.mark.parametrize('correlation',[{'trace_id':'a'*32},{'trace_id':'a'*32,'span_id':'b'*16}])
def test_trace_correlation_survives_clarification_and_does_not_create_binding(monkeypatch,correlation):
    monkeypatch.setenv('DATABASE_URL','sqlite:///:memory:');reset_engine();init_db()
    try:
        diagnosis=service.create_diagnosis(CreateDiagnosisRequestV2(query='知识查询重排慢',target={**correlation,'pid':123,'agent_id':'untrusted-hint'}))
        result=service.clarify_diagnosis(diagnosis.id,ClarifyDiagnosisRequest(target={'service':'knowledge-api'}))
        assert all(result['target'][key]==value for key,value in correlation.items())
        assert 'process_binding' not in result['target']
        assert 'pid' not in result['target'] and 'agent_id' not in result['target']
        assert result['status']=='NEEDS_CLARIFICATION'
    finally:reset_engine()
