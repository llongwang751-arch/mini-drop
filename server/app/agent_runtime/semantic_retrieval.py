"""Optional semantic index for curated public knowledge; never incident evidence.

Collections are immutable content-addressed snapshots. Readers compute the
expected snapshot from source files, so stale/deleted documents cannot survive
an index update. Index creation is an explicit offline operation.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests


class RetrievalUnavailable(RuntimeError):
    """Safe error code only: do not expose provider payloads or credentials."""


@dataclass(frozen=True)
class RetrievalSettings:
    api_key: str = field(default="", repr=False)
    base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "Qwen/Qwen3-Embedding-4B"
    rerank_model: str = "Qwen/Qwen3-Reranker-4B"
    dimensions: int = 1024
    timeout: float = 12.0

    @classmethod
    def from_env(cls):
        return cls(
            api_key=os.getenv("SILICONFLOW_API_KEY", ""),
            base_url=os.getenv("MINI_DROP_RETRIEVAL_BASE_URL", cls.base_url).rstrip("/"),
            embedding_model=os.getenv("MINI_DROP_EMBEDDING_MODEL", cls.embedding_model),
            rerank_model=os.getenv("MINI_DROP_RERANK_MODEL", cls.rerank_model),
            dimensions=int(os.getenv("MINI_DROP_EMBEDDING_DIMENSIONS", "1024")),
        )


class SemanticProvider:
    def __init__(self, settings: RetrievalSettings):
        self.settings = settings

    def _post(self, endpoint: str, payload: dict) -> dict:
        s = self.settings
        if not s.api_key:
            raise RetrievalUnavailable("PROVIDER_NOT_CONFIGURED")
        if not s.base_url.startswith("https://"):
            raise RetrievalUnavailable("PROVIDER_REQUIRES_TLS")
        # No automatic retry: the caller's deadline takes precedence; an
        # unavailable provider degrades to lexical retrieval immediately.
        try:
            with requests.post(
                s.base_url + endpoint, headers={"Authorization": "Bearer " + s.api_key},
                json=payload, timeout=(3, s.timeout), allow_redirects=False, stream=True,
            ) as response:
                if response.status_code != 200:
                    raise RetrievalUnavailable(f"PROVIDER_HTTP_{response.status_code}")
                body = bytearray()
                for block in response.iter_content(65536):
                    body.extend(block)
                    if len(body) > 8 * 1024 * 1024:
                        raise RetrievalUnavailable("PROVIDER_RESPONSE_TOO_LARGE")
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise ValueError()
                return result
        except RetrievalUnavailable:
            raise
        except Exception:
            raise RetrievalUnavailable("PROVIDER_REQUEST_FAILED") from None

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts or len(texts) > 32 or any(not t or len(t) > 8000 for t in texts):
            raise RetrievalUnavailable("EMBEDDING_INPUT_INVALID")
        s = self.settings
        result = self._post("/embeddings", {"model": s.embedding_model, "input": texts,
                                           "dimensions": s.dimensions, "encoding_format": "float"})
        try:
            rows = sorted(result["data"], key=lambda x: x["index"])
            if [r["index"] for r in rows] != list(range(len(texts))):
                raise ValueError()
            vectors = [r["embedding"] for r in rows]
            if any(len(v) != s.dimensions or any(isinstance(x, bool) or not math.isfinite(float(x)) for x in v)
                   or sum(float(x) ** 2 for x in v) <= 0 for v in vectors):
                raise ValueError()
            return [[float(x) for x in v] for v in vectors]
        except Exception:
            raise RetrievalUnavailable("EMBEDDING_RESPONSE_INVALID") from None

    def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        result = self._post("/rerank", {"model": self.settings.rerank_model, "query": query,
                                        "documents": documents, "top_n": len(documents),
                                        "return_documents": False})
        try:
            rows = [(r["index"], float(r["relevance_score"])) for r in result["results"]]
            if sorted(i for i, _ in rows) != list(range(len(documents))) or any(not math.isfinite(s) for _, s in rows):
                raise ValueError()
            return rows
        except Exception:
            raise RetrievalUnavailable("RERANK_RESPONSE_INVALID") from None


def corpus(root: Path) -> list[dict[str, Any]]:
    from .retrieval import _catalog_entries, _safe_document_path, _split_markdown, _metadata_text, _as_string_list
    root = root.resolve()
    chunks = []
    for entry in _catalog_entries(root):
        # This index intentionally supports curated PUBLIC repository knowledge
        # only. Private/tenant documents require a separate authorized importer.
        if entry.get("visibility", "PUBLIC") != "PUBLIC" or any(entry.get(k) for k in ("tenant_id", "acl", "principal_id")):
            continue
        path = _safe_document_path(root, entry.get("document", ""))
        if not path or path.stat().st_size > 128 * 1024 or not entry.get("knowledge_id"):
            continue
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        for index, body in enumerate(_split_markdown(raw.decode("utf-8"))):
            # Hard bound even when an individual Markdown paragraph is huge.
            for offset in range(0, len(body), 3000):
                text = body[offset:offset + 3000]
                identity = f"{entry['knowledge_id']}:{digest}:{index}:{offset}"
                chunks.append({"chunk_id": hashlib.sha256(identity.encode()).hexdigest(),
                               "knowledge_id": entry["knowledge_id"], "title": entry.get("title", ""),
                               "document": "knowledge/" + path.relative_to(root).as_posix(),
                               "content_hash": digest, "excerpt": text, "metadata_text": _metadata_text(entry),
                               "recall_anchors": sorted({str(value).strip() for value in
                                   [entry['knowledge_id'], *_as_string_list(entry.get('keywords')), *_as_string_list(entry.get('applies_to'))]
                                   if isinstance(value, str) and 2 <= len(value.strip()) <= 100}),
                               "required_evidence": entry.get("required_evidence", []),
                               "caveats": entry.get("caveats", [])})
    if len({c["chunk_id"] for c in chunks}) != len(chunks):
        raise RetrievalUnavailable("DUPLICATE_KNOWLEDGE_ID")
    return chunks


def snapshot_name(chunks: list[dict], settings: RetrievalSettings) -> str:
    contract = {"schema": 1, "model": settings.embedding_model, "dimensions": settings.dimensions,
                "chunks": sorted(chunks, key=lambda c: c["chunk_id"])}
    return "md-knowledge-" + hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]


def chroma_client():
    try:
        import chromadb
        from chromadb.config import Settings
        config = Settings(anonymized_telemetry=False)
        path = os.getenv("MINI_DROP_CHROMA_PATH")
        if path:
            return chromadb.PersistentClient(path=path, settings=config)
        from .chroma_http import bounded_http_client
        return bounded_http_client(os.getenv("MINI_DROP_CHROMA_HOST", "127.0.0.1"),
                                   int(os.getenv("MINI_DROP_CHROMA_PORT", "8000")),
                                   os.getenv("MINI_DROP_CHROMA_SSL", "false").lower() == "true")
    except Exception:
        raise RetrievalUnavailable("CHROMA_UNAVAILABLE") from None


def build_index(root: Path, provider: SemanticProvider, client=None) -> dict:
    chunks = corpus(root)
    if not chunks:
        raise RetrievalUnavailable("EMPTY_CORPUS")
    name = snapshot_name(chunks, provider.settings)
    client = client or chroma_client()
    collection = client.get_or_create_collection(name, metadata={"hnsw:space": "cosine", "ready": False}, embedding_function=None)
    if collection.metadata.get("ready") is True and collection.count() == len(chunks):
        return {"collection": name, "chunks": len(chunks), "reused": True}
    for offset in range(0, len(chunks), 16):
        batch = chunks[offset:offset + 16]
        vectors = provider.embed([c["metadata_text"][:3000] + "\n" + c["excerpt"] for c in batch])
        collection.upsert(ids=[c["chunk_id"] for c in batch], embeddings=vectors,
                          metadatas=[{"source_hash": c["content_hash"]} for c in batch])
    if collection.count() != len(chunks):
        raise RetrievalUnavailable("INDEX_COUNT_MISMATCH")
    # Readers never use a partially built collection. Old snapshots are retained.
    collection.modify(metadata={"ready": True})
    return {"collection": name, "chunks": len(chunks), "reused": False}


def entity_ranking(query: str, chunks: list[dict], lexical_scores: list[float]) -> list[int]:
    """Exact curated entity/identifier recall, separate from BM25 and embeddings.

    No generated aliases or incident history enter this public knowledge route.
    Latin token boundaries prevent e.g. 'go' matching 'mongodb'.
    """
    query = query.casefold()
    scored = []
    for i, chunk in enumerate(chunks):
        hits = []
        for anchor in chunk.get('recall_anchors', []):
            anchor = anchor.casefold()
            pattern = re.escape(anchor)
            if re.search(r'[a-z0-9]', anchor):
                pattern = r'(?<![a-z0-9_])' + pattern + r'(?![a-z0-9_])'
            if re.search(pattern, query):
                hits.append(anchor)
        if hits:
            scored.append((i, sum(min(len(h), 30) for h in hits)))
    return [i for i, _ in sorted(scored, key=lambda item:
            (-item[1], -lexical_scores[item[0]], chunks[item[0]]['chunk_id']))[:20]]


def _rrf(rankings):
    scores = {}
    for ranking in rankings:
        for rank, i in enumerate(ranking):
            scores[i] = scores.get(i, 0) + 1 / (60 + rank + 1)
    return scores


def search(query: str, root: Path, top_k: int = 3, *, provider=None, client=None) -> dict:
    from .retrieval import _bm25_scores, _tokenize
    chunks = corpus(root)
    top_k = max(1, min(top_k, 8))
    if not query.strip() or len(query) > 4000:
        raise RetrievalUnavailable("QUERY_INVALID")
    scores = _bm25_scores(_tokenize(query), [_tokenize(c["metadata_text"] + " " + c["excerpt"]) for c in chunks])
    lexical = sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: (-scores[i], chunks[i]["chunk_id"]))[:20]
    entities = entity_ranking(query, chunks, scores)
    ranking_scores = _rrf((lexical, entities))
    order = sorted(ranking_scores, key=lambda i: (-ranking_scores[i], chunks[i]['chunk_id']))[:30]
    score_kind = "RRF"
    backend = "BM25_ENTITY_RRF"
    rankings = {'BM25': lexical, 'ENTITY': entities}
    vector_available = False
    degraded = []
    provider = provider or SemanticProvider(RetrievalSettings.from_env())
    name = snapshot_name(chunks, provider.settings)
    try:
        collection = (client or chroma_client()).get_collection(name, embedding_function=None)
        if not collection.metadata.get("ready") or collection.count() != len(chunks):
            raise RetrievalUnavailable("INDEX_NOT_READY")
        vector = provider.embed([query])[0]
        result = collection.query(query_embeddings=[vector], n_results=min(20, len(chunks)), include=["distances"])
        by_id = {c["chunk_id"]: i for i, c in enumerate(chunks)}
        semantic = [by_id[cid] for cid, distance in zip(result["ids"][0], result["distances"][0])
                    if cid in by_id and math.isfinite(distance) and distance <= 0.65]
        rankings['DENSE'] = semantic
        vector_available = True
        rrf = _rrf((lexical, entities, semantic))
        order = sorted(rrf, key=lambda i: (-rrf[i], chunks[i]["chunk_id"]))[:30]
        ranking_scores, score_kind = rrf, "RRF"
        backend = "BM25_ENTITY_CHROMA_RRF"
    except Exception as exc:
        degraded.append(str(exc) if isinstance(exc, RetrievalUnavailable) else "INDEX_UNAVAILABLE")
    if order and os.getenv("MINI_DROP_RERANK_ENABLED", "true").lower() == "true" and vector_available:
        try:
            reranked = provider.rerank(query, [chunks[i]["excerpt"] for i in order])
            ranking_scores, score_kind = {order[i]: score for i, score in reranked}, "RERANK_RELEVANCE"
            order = [order[i] for i, score in sorted(reranked, key=lambda x: -x[1]) if score >= 0.1]
            backend += "_RERANK"
        except RetrievalUnavailable as exc:
            degraded.append(str(exc))
    results = []
    seen = set()
    for i in order:
        c = chunks[i]
        if c["knowledge_id"] in seen:
            continue
        seen.add(c["knowledge_id"])
        results.append({k: v for k, v in c.items() if k not in {"metadata_text", "recall_anchors"}})
        results[-1].update(query=query, score=round(ranking_scores[i], 6), score_kind=score_kind,
                           matched_terms=[], excerpt=c["excerpt"][:700],
                           recall_channels=[name for name, ranking in rankings.items() if i in ranking])
        if len(results) >= top_k:
            break
    return {"matches": results, "actual_backend": backend, "requested_backend": "HYBRID",
            "recall_channels": {name: {'available': name != 'DENSE' or vector_available,
                'candidate_count': len(rankings.get(name, [])),
                'chunk_ids': [chunks[i]['chunk_id'] for i in rankings.get(name, [])]}
                for name in ('BM25', 'ENTITY', 'DENSE')},
            "degraded_reasons": degraded, "index_version": name,
            "embedding_model": provider.settings.embedding_model, "dimensions": provider.settings.dimensions}


def read_chunk(root: Path, chunk_id: str) -> dict:
    for item in corpus(root):
        if item["chunk_id"] == chunk_id:
            return {k: v for k, v in item.items() if k != "metadata_text"}
    raise RetrievalUnavailable("CHUNK_NOT_CURRENT")
