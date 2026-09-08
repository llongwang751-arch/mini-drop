"""Deterministic, local retrieval for Mini-Drop diagnosis knowledge.

This module deliberately has no vector database or model dependency.  The
repository-owned ``knowledge/catalog.json`` and its Markdown documents are the
only sources.  Retrieved text is a planning prior: it may suggest what to
measure, but it is never admitted as incident Evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


RETRIEVER_VERSION = "knowledge-hybrid-lexical-v1"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_KNOWLEDGE_ROOT = _REPOSITORY_ROOT / "knowledge"
_ENGLISH_TOKEN = re.compile(r"[a-z0-9][a-z0-9.+]*", re.IGNORECASE)
_CJK_SEQUENCE = re.compile(r"[\u3400-\u9fff]+")
_HEADING = re.compile(r"^#{1,6}\s+")
_ENGLISH_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}
_CJK_STOPWORDS = {
    "不能",
    "什么",
    "今天",
    "以及",
    "可以",
    "如何",
    "如果",
    "异常",
    "当前",
    "应该",
    "性能",
    "故障",
    "服务",
    "检查",
    "用于",
    "用户",
    "目标",
    "系统",
    "诊断",
    "需要",
    "问题",
}


def _tokenize(text: str) -> list[str]:
    """Tokenize mixed Chinese/English text without a third-party segmenter."""

    normalized = str(text or "").casefold().replace("_", " ").replace("-", " ")
    tokens = [
        token
        for token in _ENGLISH_TOKEN.findall(normalized)
        if token not in _ENGLISH_STOPWORDS and len(token) > 1
    ]
    for sequence in _CJK_SEQUENCE.findall(normalized):
        if len(sequence) == 1:
            continue
        # Character n-grams provide deterministic recall for Chinese queries
        # without pretending that they are semantic embeddings.
        for width in (2, 3):
            if len(sequence) < width:
                continue
            tokens.extend(
                gram
                for gram in (
                    sequence[index : index + width]
                    for index in range(len(sequence) - width + 1)
                )
                if gram not in _CJK_STOPWORDS
            )
    return tokens


def _split_markdown(text: str, *, max_chars: int = 1200) -> list[str]:
    """Split Markdown on headings/paragraphs while preserving readable excerpts."""

    sections: list[str] = []
    current: list[str] = []
    for line in str(text or "").splitlines():
        if _HEADING.match(line.strip()) and current:
            sections.append("\n".join(current).strip())
            current = []
        current.append(line.rstrip())
    if current:
        sections.append("\n".join(current).strip())

    chunks: list[str] = []
    for section in sections:
        if len(section) <= max_chars:
            if section:
                chunks.append(section)
            continue
        buffer = ""
        for paragraph in re.split(r"\n\s*\n", section):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if buffer and len(buffer) + len(paragraph) + 2 > max_chars:
                chunks.append(buffer)
                buffer = ""
            if len(paragraph) <= max_chars:
                buffer = f"{buffer}\n\n{paragraph}".strip()
                continue
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.extend(
                paragraph[index : index + max_chars]
                for index in range(0, len(paragraph), max_chars)
            )
        if buffer:
            chunks.append(buffer)
    return chunks or ([str(text).strip()] if str(text).strip() else [])


def _safe_document_path(knowledge_root: Path, document: str) -> Path | None:
    candidate = (knowledge_root / str(document or "")).resolve()
    root = knowledge_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.suffix.casefold() != ".md" or not candidate.is_file():
        return None
    return candidate


def _catalog_entries(knowledge_root: Path) -> list[dict[str, Any]]:
    catalog_path = knowledge_root / "catalog.json"
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _metadata_text(item: dict[str, Any]) -> str:
    values: list[str] = [
        str(item.get("knowledge_id") or ""),
        str(item.get("title") or ""),
        str(item.get("summary") or ""),
    ]
    for field in ("keywords", "applies_to", "required_evidence", "caveats"):
        value = item.get(field)
        if isinstance(value, list):
            values.extend(str(entry) for entry in value)
    return " ".join(values)


def _bm25_scores(
    query_tokens: list[str],
    corpus_tokens: list[list[str]],
    *,
    k1: float = 1.35,
    b: float = 0.72,
) -> list[float]:
    if not query_tokens or not corpus_tokens:
        return [0.0 for _ in corpus_tokens]
    document_frequency: dict[str, int] = defaultdict(int)
    for tokens in corpus_tokens:
        for token in set(tokens):
            document_frequency[token] += 1
    average_length = sum(len(tokens) for tokens in corpus_tokens) / len(corpus_tokens)
    average_length = max(average_length, 1.0)
    document_count = len(corpus_tokens)
    query_terms = set(query_tokens)
    scores: list[float] = []
    for tokens in corpus_tokens:
        counts = Counter(tokens)
        length_normalizer = 1.0 - b + b * (len(tokens) / average_length)
        score = 0.0
        for term in query_terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            df = document_frequency[term]
            inverse_frequency = math.log(
                1.0 + (document_count - df + 0.5) / (df + 0.5)
            )
            score += inverse_frequency * (
                frequency * (k1 + 1.0)
                / (frequency + k1 * length_normalizer)
            )
        scores.append(score)
    return scores


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _bounded_excerpt(text: str, matched_terms: Iterable[str], limit: int = 700) -> str:
    compact = re.sub(r"\n{3,}", "\n\n", str(text or "").strip())
    if len(compact) <= limit:
        return compact
    lowered = compact.casefold()
    positions = [lowered.find(term.casefold()) for term in matched_terms]
    positions = [position for position in positions if position >= 0]
    center = min(positions) if positions else 0
    start = max(0, center - limit // 4)
    end = min(len(compact), start + limit)
    prefix = "…" if start else ""
    suffix = "…" if end < len(compact) else ""
    return f"{prefix}{compact[start:end].strip()}{suffix}"


def retrieve_knowledge(
    query: str,
    *,
    top_k: int = 3,
    knowledge_root: str | Path | None = None,
    min_score: float = 0.2,
) -> list[dict[str, Any]]:
    """Return ranked repository knowledge; an empty list is a valid no-match."""

    query = str(query or "").strip()
    if not query or top_k <= 0:
        return []
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []
    root = Path(knowledge_root).resolve() if knowledge_root else _DEFAULT_KNOWLEDGE_ROOT
    candidates: list[dict[str, Any]] = []
    corpus: list[list[str]] = []
    for item in _catalog_entries(root):
        knowledge_id = str(item.get("knowledge_id") or "").strip()
        title = str(item.get("title") or "").strip()
        document = str(item.get("document") or "").strip()
        if not knowledge_id or not title or not document:
            continue
        source_path = _safe_document_path(root, document)
        if source_path is None:
            continue
        try:
            raw_bytes = source_path.read_bytes()
            body = raw_bytes.decode("utf-8")
        except (OSError, UnicodeError):
            continue
        metadata = _metadata_text(item)
        metadata_tokens = _tokenize(metadata)
        for chunk_index, chunk in enumerate(_split_markdown(body)):
            tokens = [*metadata_tokens, *_tokenize(chunk)]
            candidates.append(
                {
                    "item": item,
                    "knowledge_id": knowledge_id,
                    "title": title,
                    "document": (
                        "knowledge/"
                        + source_path.relative_to(root).as_posix()
                    ),
                    "content_hash": hashlib.sha256(raw_bytes).hexdigest(),
                    "chunk_index": chunk_index,
                    "chunk": chunk,
                    "tokens": tokens,
                    "anchor_tokens": set(
                        _tokenize(
                            " ".join(
                                [
                                    knowledge_id,
                                    title,
                                    str(item.get("summary") or ""),
                                    " ".join(_as_string_list(item.get("keywords"))),
                                    " ".join(_as_string_list(item.get("applies_to"))),
                                ]
                            )
                        )
                    ),
                }
            )
            corpus.append(tokens)
    scores = _bm25_scores(query_tokens, corpus)
    query_set = set(query_tokens)
    best_by_knowledge_id: dict[str, dict[str, Any]] = {}
    for candidate, raw_score in zip(candidates, scores):
        matched = query_set.intersection(candidate["tokens"])
        anchor_matched = query_set.intersection(candidate["anchor_tokens"])
        if not matched or not anchor_matched:
            continue
        # Metadata anchors are intentional catalog curation, while BM25 keeps
        # document frequency and chunk length from dominating common terms.
        score = raw_score + 0.28 * len(anchor_matched)
        if score < min_score:
            continue
        matched_terms = sorted(matched, key=lambda value: (-len(value), value))[:16]
        item = candidate["item"]
        result = {
            "query": query,
            "knowledge_id": candidate["knowledge_id"],
            "title": candidate["title"],
            "document": candidate["document"],
            "content_hash": candidate["content_hash"],
            "score": round(score, 6),
            "matched_terms": matched_terms,
            "required_evidence": _as_string_list(item.get("required_evidence")),
            "caveats": _as_string_list(item.get("caveats")),
            "excerpt": _bounded_excerpt(candidate["chunk"], matched_terms),
        }
        existing = best_by_knowledge_id.get(candidate["knowledge_id"])
        if existing is None or result["score"] > existing["score"]:
            best_by_knowledge_id[candidate["knowledge_id"]] = result
    ranked = sorted(
        best_by_knowledge_id.values(),
        key=lambda item: (-float(item["score"]), str(item["knowledge_id"])),
    )
    return ranked[:top_k]


def build_retrieval_trace(
    query: str,
    *,
    top_k: int = 3,
    knowledge_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build the auditable planner trace and state the Evidence boundary."""

    query = str(query or "").strip()
    matches = retrieve_knowledge(
        query,
        top_k=top_k,
        knowledge_root=knowledge_root,
    )
    return {
        "query": query,
        "query_hash": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        "retriever": RETRIEVER_VERSION,
        "catalog": "knowledge/catalog.json",
        "top_k": top_k,
        "matched_count": len(matches),
        "matches": matches,
        "evidence_contract": {
            "is_evidence": False,
            "status": "KNOWLEDGE_PRIOR_ONLY",
            "message": "检索知识只指导下一步取证，不能作为本次故障 Evidence。",
        },
    }


__all__ = [
    "RETRIEVER_VERSION",
    "build_retrieval_trace",
    "retrieve_knowledge",
]
