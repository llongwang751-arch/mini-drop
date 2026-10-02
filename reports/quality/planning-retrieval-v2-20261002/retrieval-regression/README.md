# Exposed retrieval regression

Label: `REGRESSION_ON_EXPOSED_V2_QUESTIONS_NOT_NEW_BLIND`. These same 24 questions were already exposed by the
original first measurement. This is a regression after correcting public
knowledge subject/capability admission, not a new blind evaluation.

Source: `a6a3626094b8edbb5b9a48bef317153035ccca16`. Release: `20261002T095001Z`.
Only BM25 and deployed HYBRID retrieval were exercised. Chat calls, Tasks,
fault injection and index creation were all zero. No questions, oracle,
grade thresholds or scorer source were changed.

The original first measurement is separately preserved in `../evaluation/`
with all failures and original source. This regression does not replace its
21/24 model results, BM25 5/8 no-answer false positives or HYBRID 1/8.
`independent-audit.json` records exact raw/source/corpus checks, local
BM25 replay, public HYBRID admission and rescore. Provider-dependent semantic
ranking was not repeated by the independent verifier. Retrieval token usage
and cost remain unknown. Knowledge matches do not prove current service
health or a causal root cause.

`source/` holds 254 exact Git blobs so the regression can be independently
reviewed after another production update.
