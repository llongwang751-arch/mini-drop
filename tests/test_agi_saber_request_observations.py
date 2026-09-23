import importlib.util
import json
from pathlib import Path
import asyncio


def test_office_store_is_bounded_and_content_free(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'integrations' / 'agi_saber' / 'request_observations.py'
    spec = importlib.util.spec_from_file_location('office_request_observations', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / 'observations.json'
    store = module.OfficeObservationStore(path, limit=2)
    for i in range(3):
        store.append({'request_id': f'{i:032x}', 'stage_ms': {'retrieval_ms': i}})
    payload = json.loads(path.read_text(encoding='utf-8'))
    assert payload['schema_version'] == 'mini-drop.office-observations.v1'
    assert [row['request_id'] for row in payload['records']] == [f'{i:032x}' for i in (1, 2)]
    assert 'query' not in path.read_text(encoding='utf-8')


def test_exercise_scope_is_request_local_and_only_marks_chat():
    source = Path(__file__).resolve().parents[1] / 'integrations' / 'agi_saber' / 'request_observations.py'
    spec = importlib.util.spec_from_file_location('office_exercise_scope', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seen = []

    async def inner(scope, receive, send):
        seen.append(module._EXERCISE.get())

    app = module.ExerciseScope(inner)

    async def invoke(path, phase):
        await app({'type': 'http', 'path': path,
                   'headers': [(b'x-mini-drop-exercise', phase.encode())]}, None, None)
        assert module._EXERCISE.get() == ''

    asyncio.run(invoke('/api/chat', 'fault'))
    asyncio.run(invoke('/api/chat', 'recovery'))
    asyncio.run(invoke('/api/status', 'fault'))
    asyncio.run(invoke('/api/chat', 'unknown'))
    assert seen == ['fault', 'recovery', '', '']


def test_retrieval_fault_is_bounded_to_one_call_per_window(monkeypatch):
    source = Path(__file__).resolve().parents[1] / 'integrations' / 'agi_saber' / 'request_observations.py'
    spec = importlib.util.spec_from_file_location('office_exercise_injection', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Search:
        def search_multi(self):
            return 'real-search'

    sleeps = []
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: sleeps.append(seconds))
    module._measure(Search, 'search_multi', 'search_total_ms')
    search = Search()
    window = module._Window()
    window.exercise_phase = 'fault'
    token = module._ACTIVE.set(window)
    try:
        assert search.search_multi() == 'real-search'
        assert search.search_multi() == 'real-search'
    finally:
        module._ACTIVE.reset(token)
    assert sleeps == [2.5]
    assert window.injected_delay_ms == 2500

    recovery = module._Window()
    recovery.exercise_phase = 'recovery'
    token = module._ACTIVE.set(recovery)
    try:
        assert search.search_multi() == 'real-search'
    finally:
        module._ACTIVE.reset(token)
    assert recovery.injected_delay_ms == 0


def test_upload_scope_records_bounded_stage_and_process_observation(monkeypatch):
    source = Path(__file__).resolve().parents[1] / 'integrations' / 'agi_saber' / 'request_observations.py'
    spec = importlib.util.spec_from_file_location('office_upload_observation', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, '_rss_mib', lambda: 42.0)
    monkeypatch.setattr(module, '_service_cgroup_sample', lambda: (75.0, 1000.0, 768.0))

    class Engine:
        def ingest(self, text):
            return 3

    class Embedder:
        def embed(self, text):
            raise RuntimeError('not configured')

    class VectorStore:
        def upsert(self, collection_name, data):
            return True

    module._measure_ingest(Engine, 'ingest', 'ingest_ms')
    module._measure_ingest(Embedder, 'embed', 'embedding_ms')
    module._measure_vector_upsert(VectorStore)
    records, sent = [], []

    class Store:
        def append(self, record):
            records.append(record)

    async def inner(scope, receive, send):
        assert module._INGEST.get() is not None
        assert Engine().ingest('long text') == 3
        try:
            Embedder().embed('chunk')
        except RuntimeError:
            pass
        assert VectorStore().upsert('rag_chunks', [{'id': 1}, {'id': 2}]) is True
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'{}'})

    app = module.ExerciseScope(inner).with_store(Store())
    async def capture(message):
        sent.append(message)
    asyncio.run(app({'type': 'http', 'path': '/api/upload'}, None, capture))
    assert module._INGEST.get() is None
    assert sent[0]['headers'][0][0] == b'x-mini-drop-request-id'
    assert len(records) == 1
    row = records[0]
    assert row['operation'] == 'rag.ingest' and row['result'] == 'COMPLETED'
    assert row['content_chars'] == 9 and row['chunk_count'] == 3
    assert row['embed_calls'] == row['embed_failures'] == 1
    assert row['rss_peak_mib'] == 42.0
    assert row['service_memory_peak_mib'] == 75.0
    assert row['service_memory_limit_mib'] == 768.0
    assert row['vector_indexed_count'] == 2
    assert row['stage_ms']['ingest_ms'] >= 0
