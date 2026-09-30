from types import SimpleNamespace
import pytest

from integrations.agi_saber.query_extraction_cache import QueryExtractionCache, install_on


def result(name='leave'):
    return SimpleNamespace(entities=[SimpleNamespace(name=name)], relations=[])


def test_extraction_cache_is_bounded_expires_and_does_not_share_tenant_or_model():
    clock=[0]
    cache=QueryExtractionCache(limit=2,ttl=5,clock=lambda:clock[0])
    calls=[]
    def compute():
        calls.append(1);return result()
    first=cache.get(('alice','model-a'),'leave',compute)
    first.entities[0].name='mutated'
    assert cache.get(('alice','model-a'),'leave',compute).entities[0].name=='leave'
    assert len(calls)==1
    cache.get(('bob','model-a'),'leave',compute)
    cache.get(('alice','model-b'),'leave',compute)
    assert len(calls)==3 and len(cache.entries)==2
    cache.get(('alice','model-a'),'leave',compute)
    assert len(calls)==4
    clock[0]=5
    cache.get(('alice','model-a'),'leave',compute)
    assert len(calls)==5


def test_empty_or_failed_extraction_is_never_cached():
    cache=QueryExtractionCache()
    calls=[]
    def empty():
        calls.append(1);return SimpleNamespace(entities=[],relations=[])
    cache.get(('alice','model-a'),'leave',empty)
    cache.get(('alice','model-a'),'leave',empty)
    assert len(calls)==2 and not cache.entries
    with pytest.raises(RuntimeError):
        cache.get(('alice','model-a'),'leave',lambda:(_ for _ in ()).throw(RuntimeError('model offline')))
    assert not cache.entries


def test_restored_instances_reuse_query_extraction_but_always_read_current_graph():
    calls=[]
    graph={'leave':1}
    class Extractor:
        def extract(self,text):
            calls.append(text);return result(text)
    class Store:
        def __init__(self,cfg,user_id):
            self.user_id=user_id;self.extractor=Extractor()
        def search(self,text,top_k):
            extracted=self.extractor.extract(text)
            return graph[extracted.entities[0].name]
    install_on(Store,Extractor,QueryExtractionCache())
    cfg=SimpleNamespace(model='a')
    store=Store(cfg,'alice')
    assert store.search('leave',1)==1
    graph['leave']=2
    assert Store(cfg,'alice').search('leave',1)==2
    assert calls==['leave']
    # Document indexing has no search context and must never use query cache.
    store.extractor.extract('leave')
    assert calls==['leave','leave']
    assert Store(cfg,'bob').search('leave',1)==2
    cfg.model='b'
    assert store.search('leave',1)==2
    assert len(calls)==4


@pytest.mark.parametrize('response,expected_calls', [
    ('{"entities":[],"relations":[]}',1),
    ('```json\n{"entities":[],"relations":[]}\n```',1),
    ('invalid json',2),
    ('{}',2),
    ('{"entities":"wrong","relations":[]}',2),
    (RuntimeError('model offline'),2),
])
def test_valid_empty_json_is_reused_but_swallowed_failures_remain_misses(response,expected_calls):
    import json
    calls=[]
    def llm(*args):
        calls.append(1)
        if isinstance(response,Exception):raise response
        return response
    class Extractor:
        def __init__(self,llm_fn):self.llm_fn=llm_fn
        def extract(self,text):
            try:
                raw=self.llm_fn('system',text).strip()
                if raw.startswith('```json'):raw=raw[7:]
                if raw.endswith('```'):raw=raw[:-3]
                json.loads(raw.strip())
            except Exception:pass
            return SimpleNamespace(entities=[],relations=[])
    class Store:
        def __init__(self,cfg,user_id):
            self.user_id=user_id;self.extractor=Extractor(llm)
        def search(self,text):return self.extractor.extract(text)
    cache=QueryExtractionCache();install_on(Store,Extractor,cache)
    store=Store('config','alice')
    store.search('leave');store.search('leave')
    assert len(calls)==expected_calls
    # Even valid-empty query results cannot bypass document indexing calls.
    store.extractor.extract('leave')
    assert len(calls)==expected_calls+1
