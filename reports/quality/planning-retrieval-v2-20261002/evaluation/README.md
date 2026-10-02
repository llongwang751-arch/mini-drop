# Planning / retrieval v2 first measurement

These are the original immutable first measurements against deployed source
`df0d3ef00a945e7d909f73868819799b2b7cc7f7` (release `20261002T085733Z`).

The 24 chat attempts were one-shot (21 HTTP 200, 3 read timeouts, no retries).
All 21 returned outputs satisfy the public production parser. This is an
author-frozen synthetic JSON projection; it does not execute the Agent loop,
Tasks, faults, health measurements or causal Evidence Gate. The first scores
include timeout failures and are not replaced by later exposed regressions.

BM25: Recall@3 0.96875, MRR@3 0.875, no-answer false positives 5/8.
Deployed HYBRID: Recall@3 0.90625, MRR@3 0.9375, no-answer false positives 1/8.
HYBRID used 21 reranked and 3 empty non-reranked traces, with no degraded reasons.
Its independent review verifies original raw hashes, deployed source, exact
raw document and chunk identities, public admission and scoring; it does not
repeat provider-dependent semantic ranking. The frozen LF_TEXT corpus permits
LF/CRLF normalization; the deployed raw origin was additionally proved by a
read-only receipt and exact SHA/chunk reconstruction. The initial reviewer's
incorrect Git-LF/raw equality assumption is preserved as a failed audit receipt.

The model's available usage totals 90,583 tokens across 21 successful responses.
Three timeout usages and all provider costs are unknown. Retrieval embedding
and reranker usage/cost are also unknown. Public knowledge is not evidence.

`original-source/` contains 254 exact Git blobs for independent historical
verification after production changes. Run its own evaluator from that source
context, using the archived first report; do not claim future runtime AST
equivalence. `freeze/` is the unchanged 24-question frozen suite and oracle.
Any later run on these exposed questions must be labeled regression, not a
new blind evaluation. No third-party independent authorship is claimed.
