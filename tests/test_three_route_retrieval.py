import json

from server.app.agent_runtime.context import bounded_tail
from server.app.agent_runtime import semantic_retrieval as retrieval
from tests.test_sre_agent_upgrade import Client, Provider


def make_knowledge(tmp_path):
    (tmp_path / 'one.md').write_text('# PostgreSQL\nInspect lock waits and transaction duration.', encoding='utf-8')
    (tmp_path / 'catalog.json').write_text(json.dumps([
        {'knowledge_id': 'db.locks', 'title': 'Database waits', 'document': 'one.md',
         'keywords': ['pg_stat_activity', '数据库锁等待']},
    ]), encoding='utf-8')
    return tmp_path


def test_exact_entity_recall_has_token_boundaries():
    chunks = [{'chunk_id': 'one', 'recall_anchors': ['go']},
              {'chunk_id': 'two', 'recall_anchors': ['pg_stat_activity']}]
    assert retrieval.entity_ranking('mongodb', chunks, [0, 0]) == []
    assert retrieval.entity_ranking('Inspect PG_STAT_ACTIVITY now', chunks, [0, 0]) == [1]
    assert retrieval.entity_ranking('xpg_stat_activity_suffix', chunks, [0, 0]) == []


def test_three_routes_are_independently_auditable(tmp_path):
    root = make_knowledge(tmp_path)
    client, provider = Client(), Provider()
    retrieval.build_index(root, provider, client)
    trace = retrieval.search('pg_stat_activity', root, provider=provider, client=client)
    assert trace['actual_backend'] == 'BM25_ENTITY_CHROMA_RRF_RERANK'
    assert set(trace['recall_channels']) == {'BM25', 'ENTITY', 'DENSE'}
    assert all(c['available'] and c['candidate_count'] == 1 for c in trace['recall_channels'].values())
    assert trace['matches'][0]['recall_channels'] == ['BM25', 'ENTITY', 'DENSE']


def test_vector_failure_keeps_two_local_routes_and_marks_degradation(tmp_path):
    root = make_knowledge(tmp_path)
    trace = retrieval.search('pg_stat_activity', root, provider=Provider(), client=Client())
    assert trace['actual_backend'] == 'BM25_ENTITY_RRF'
    assert trace['degraded_reasons'] == ['INDEX_UNAVAILABLE']
    assert not trace['recall_channels']['DENSE']['available']
    assert trace['matches'][0]['knowledge_id'] == 'db.locks'


def test_changed_entity_contract_requires_new_snapshot(tmp_path):
    root = make_knowledge(tmp_path)
    before = retrieval.snapshot_name(retrieval.corpus(root), Provider.settings)
    catalog = root / 'catalog.json'
    rows = json.loads(catalog.read_text())
    rows[0]['keywords'].append('blocking_session')
    catalog.write_text(json.dumps(rows), encoding='utf-8')
    assert before != retrieval.snapshot_name(retrieval.corpus(root), Provider.settings)


def test_zero_context_budget_does_not_leak_whole_history():
    assert bounded_tail([{'secret': 'old message'}], 0) == ()
    assert bounded_tail([1, 2], -1) == ()
    assert bounded_tail([1, 2], 1) == (2,)
