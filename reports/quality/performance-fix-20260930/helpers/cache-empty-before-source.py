"""Cache successful query entity extraction; graph/database reads stay fresh."""
from collections import OrderedDict
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import hashlib
import threading
import time

_SEARCH = ContextVar('office_graph_search_cache', default=None)
_INSTALLED = False


class QueryExtractionCache:
    def __init__(self, limit=128, ttl=300, clock=time.monotonic):
        self.limit, self.ttl, self.clock = limit, ttl, clock
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def get(self, scope, text, compute):
        if not isinstance(text, str) or len(text) > 2048:
            return compute()
        key = (*scope, text)
        now = self.clock()
        with self.lock:
            for expired in [k for k, (until, _) in self.entries.items() if until <= now]:
                self.entries.pop(expired)
            found = self.entries.get(key)
            if found is not None:
                self.entries.move_to_end(key)
                return deepcopy(found[1])
        # Do not hold a process-wide lock across a remote model invocation.
        result = compute()
        entities, relations = getattr(result, 'entities', []), getattr(result, 'relations', [])
        # Empty results also represent model/parse failures in the original
        # Extractor. Do not turn a transient failure into five minutes of misses.
        if entities and len(entities) <= 64 and len(relations) <= 128:
            copied = deepcopy(result)
            with self.lock:
                self.entries[key] = (self.clock()+self.ttl, copied)
                self.entries.move_to_end(key)
                while len(self.entries) > self.limit:
                    self.entries.popitem(last=False)
        return result


def install_on(graph_store, extractor_type, cache):
    original_init = graph_store.__init__
    original_search = graph_store.search
    original_extract = extractor_type.extract

    @wraps(original_init)
    def initialized(self, cfg, *args, **kwargs):
        original_init(self, cfg, *args, **kwargs)
        self._query_cache_config = cfg

    @wraps(original_search)
    def search(self, *args, **kwargs):
        # New restored Agent/KG instances can reuse entries, while tenant and
        # changed provider/model/configuration cannot share extraction results.
        config = hashlib.sha256(repr(self._query_cache_config).encode()).digest()
        token = _SEARCH.set((self.extractor, (self.user_id, config)))
        try:
            return original_search(self, *args, **kwargs)
        finally:
            _SEARCH.reset(token)

    @wraps(original_extract)
    def extract(self, text):
        active = _SEARCH.get()
        if active is None or active[0] is not self:
            return original_extract(self, text)
        return cache.get(active[1], text, lambda: original_extract(self, text))

    graph_store.__init__, graph_store.search, extractor_type.extract = initialized, search, extract


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    from internal.graph.kgstore import KGStore
    from internal.graph.extractor import Extractor
    install_on(KGStore, Extractor, QueryExtractionCache())
    _INSTALLED = True
