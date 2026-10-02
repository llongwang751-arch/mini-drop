"""Evaluate the curated development set; never a held-out incident benchmark."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.app.agent_runtime.retrieval import retrieve_knowledge
from server.app.agent_runtime.semantic_retrieval import corpus, search


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['lexical', 'hybrid'], default='lexical')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Existing evidence must not be overwritten')
    source = ROOT / 'benchmarks/retrieval/sre_queries.json'
    suite = json.loads(source.read_text(encoding='utf-8'))
    root = ROOT / 'knowledge'
    chunks = corpus(root)
    results = []
    for case in suite['cases']:
        started = time.monotonic()
        if args.backend == 'hybrid':
            trace = search(case['query'], root)
            matches, backend = trace['matches'], trace['actual_backend']
            degraded = trace['degraded_reasons']
        else:
            matches, backend = retrieve_knowledge(case['query'], top_k=3), 'BM25'
            degraded = []
        ids = list(dict.fromkeys(m['knowledge_id'] for m in matches))[:3]
        relevant = set(case['relevant_ids'])
        ranks = [i + 1 for i, kid in enumerate(ids) if kid in relevant]
        results.append({**case, 'matched_ids': ids, 'actual_backend': backend,
                        'seconds': round(time.monotonic() - started, 3), 'degraded_reasons': degraded,
                        'recall_at_3': len(relevant.intersection(ids)) / len(relevant) if relevant else None,
                        'reciprocal_rank': 1 / min(ranks) if ranks else 0,
                        'false_positive': not relevant and bool(ids)})
    positives = [r for r in results if r['relevant_ids']]
    negatives = [r for r in results if not r['relevant_ids']]
    report = {'kind': suite['kind'], 'not_held_out': True,
              'suite_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'corpus_sha256': hashlib.sha256(json.dumps(chunks, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
              'documents': len({c['document'] for c in chunks}), 'chunks': len(chunks),
              'recall_at_3': sum(r['recall_at_3'] for r in positives) / len(positives),
              'mrr_at_3': sum(r['reciprocal_rank'] for r in positives) / len(positives),
              'no_answer_false_positive_rate': sum(r['false_positive'] for r in negatives) / len(negatives),
              'hybrid_backend_confirmed': args.backend == 'hybrid' and all(
                  not r['degraded_reasons'] and (r['actual_backend'] == 'BM25_ENTITY_CHROMA_RRF_RERANK' or
                  (not r['matched_ids'] and r['actual_backend'] == 'BM25_ENTITY_CHROMA_RRF')) for r in results),
              'cases': results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'cases'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
