import json
from datetime import datetime, timezone

import pytest

from server.app.drop_insight import business_observations as observations


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_DROP_BUSINESS_LOG_ROOT", str(tmp_path))
    return tmp_path, {"id":"memos", "business_observations":True, "observation_operations":["memo.records"]}


def record(**overrides):
    return dict(request_id="a"*32,service_id="memos",version="v0.30.0",operation="memo.records",method="POST",status=200,
                ended_at_unix=datetime.now(timezone.utc).timestamp(),duration_seconds=.12,bytes_sent=40,**overrides)


def write(source, rows):
    root, _ = source
    (root/'memos.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')


def test_observation_timing_is_not_business_success_or_process_identity(source):
    write(source,[record()])
    result=observations.recent_observations(source[1])
    row=result['items'][0]
    assert result['status']=='AVAILABLE'
    assert row['duration_ms']==120
    assert row['business_result']=='NOT_VERIFIED'
    assert row['process_identity']=='NOT_RECORDED'
    assert '历史请求' in observations.diagnosis_context(row)


@pytest.mark.parametrize('field,value',[('service_id','files'),('operation','steal.secrets'),('duration_seconds',float('nan')),('ended_at_unix',1e30),('request_id','../x'),('Authorization','secret')])
def test_reject_wrong_scope_nonfinite_future_and_sensitive_extra_fields(source,field,value):
    row=record();row[field]=value
    write(source,[row])
    result=observations.recent_observations(source[1])
    assert not result['items']
    assert result['invalid_records']==1


def test_conflicting_request_ids_fail_closed(source):
    first=record();second={**first,'status':500}
    write(source,[first,second])
    assert observations.recent_observations(source[1])['status']=='INVALID'


def test_partial_lines_and_old_records_are_not_presented(source):
    row=record();row['ended_at_unix']-=90000
    write(source,[row])
    with (source[0]/'memos.jsonl').open('a') as f:f.write(json.dumps(record()))
    assert not observations.recent_observations(source[1])['items']
    with pytest.raises(ValueError,match='过期'):
        observations.resolve_observation(source[1],'a'*32)


def test_bounded_tail_reads_complete_recent_lines(source,monkeypatch):
    monkeypatch.setattr(observations,'MAX_READ_BYTES',600)
    write(source,[{**record(),'request_id':f'{i:032x}'} for i in range(20)])
    result=observations.recent_observations(source[1])
    assert result['items'] and len(result['items'])<20
    assert result['items'][0]['request_id']==f'{19:032x}'


def test_missing_and_not_configured_are_distinct(source,monkeypatch):
    assert observations.recent_observations(source[1])['status']=='NO_DATA'
    monkeypatch.delenv('MINI_DROP_BUSINESS_LOG_ROOT')
    assert observations.recent_observations(source[1])['status']=='NOT_CONFIGURED'


def test_office_snapshot_keeps_request_stages_separate_from_process_evidence(tmp_path):
    path = tmp_path / 'office.json'
    now = datetime.now(timezone.utc).timestamp()
    row = dict(request_id='b'*32, service_id='agi-office-backend', operation='rag.question',
               method='POST', version='20260923T120000Z', status=200,
               started_at_unix=now-.3, ended_at_unix=now, duration_ms=300, pid=1234,
               stage_ms={'rewrite_ms': 50, 'retrieval_ms': 20, 'generation_ms': 210},
               retrieval_mode='local', result='COMPLETED')
    envelope = dict(schema_version='mini-drop.office-observations.v1',
                    service_id='agi-office-backend', pid=1234, records=[row])
    path.write_text(json.dumps(envelope), encoding='utf-8')
    result = observations._read_office_snapshot(path, {'id': 'agi-office-backend'}, limit=30)
    assert result['status'] == 'AVAILABLE'
    item = result['items'][0]
    assert item['stage_ms']['generation_ms'] == 210
    assert item['business_result'] == 'COMPLETED'
    assert item['process_identity'] == 'APPLICATION_REPORTED_NOT_BINDING'
    assert '不同时间窗' in observations.diagnosis_context(item)
    assert 'embedding_ms' not in item['stage_ms']


@pytest.mark.parametrize('mutation', [
    lambda row: row.update(pid=5678),
    lambda row: row['stage_ms'].update(secret_ms=1),
    lambda row: row['stage_ms'].update(generation_ms=float('nan')),
    lambda row: row.update(query='private text'),
])
def test_office_snapshot_rejects_untrusted_or_sensitive_records(tmp_path, mutation):
    path = tmp_path / 'office.json'
    now = datetime.now(timezone.utc).timestamp()
    row = dict(request_id='b'*32, service_id='agi-office-backend', operation='rag.question',
               method='POST', version='20260923T120000Z', status=200,
               started_at_unix=now-.3, ended_at_unix=now, duration_ms=300, pid=1234,
               stage_ms={'generation_ms': 210}, retrieval_mode='local', result='COMPLETED')
    mutation(row)
    path.write_text(json.dumps(dict(schema_version='mini-drop.office-observations.v1',
                                    service_id='agi-office-backend', pid=1234, records=[row])), encoding='utf-8')
    result = observations._read_office_snapshot(path, {'id': 'agi-office-backend'}, limit=30)
    assert result['items'] == []
    assert result['invalid_records'] == 1
