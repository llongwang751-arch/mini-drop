"""Cache successful query entity extraction; graph/database reads stay fresh."""
from collections import OrderedDict
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import hashlib
import json
import threading
import time

_SEARCH = ContextVar('office_graph_search_cache', default=None)
_VALID_EMPTY = ContextVar('office_valid_empty_extraction', default=None)
_INSTALLED = False


class QueryExtractionCache:
    def __init__(self, limit=128, ttl=300, clock=time.monotonic):
        self.limit, self.ttl, self.clock = limit, ttl, clock
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def get(self, scope, text, compute, *, confirmed_empty=lambda: False):
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
        # The original Extractor collapses valid empty and failed output.
        # Cache empty only when the model callback proved an explicit valid JSON.
        eligible = bool(entities) or (not relations and confirmed_empty())
        if eligible and len(entities) <= 64 and len(relations) <= 128:
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
    original_extractor_init = extractor_type.__init__

    @wraps(original_extractor_init)
    def extractor_initialized(self, *args, **kwargs):
        original_extractor_init(self, *args, **kwargs)
        llm = getattr(self, "llm_fn", None)
        if not callable(llm):
            return

        @wraps(llm)
        def validated(*args, **kwargs):
            raw = llm(*args, **kwargs)
            if _VALID_EMPTY.get() is not None and isinstance(raw, str):
                cleaned = raw.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                elif cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                try:
                    parsed = json.loads(cleaned.strip())
                except (ValueError, TypeError):
                    parsed = None
                # Only explicit, schema-valid empty model output is reusable.
                # Missing keys, malformed JSON and swallowed model failures
                # remain misses; indexing runs outside this context.
                if isinstance(parsed, dict) and parsed.get("entities") == [] and parsed.get("relations") == []:
                    _VALID_EMPTY.set(True)
            return raw

        self.llm_fn = validated

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
        confirmed = [False]

        def compute():
            token = _VALID_EMPTY.set(False)
            try:
                result = original_extract(self, text)
                confirmed[0] = _VALID_EMPTY.get() is True
                return result
            finally:
                _VALID_EMPTY.reset(token)

        return cache.get(active[1], text, compute, confirmed_empty=lambda: confirmed[0])

    graph_store.__init__, graph_store.search, extractor_type.extract = initialized, search, extract
    extractor_type.__init__ = extractor_initialized


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    from internal.graph.kgstore import KGStore
    from internal.graph.extractor import Extractor
    install_on(KGStore, Extractor, QueryExtractionCache())
    _INSTALLED = True
