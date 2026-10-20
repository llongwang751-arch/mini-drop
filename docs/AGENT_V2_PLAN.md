# Agent V2 implementation work record

User scope: product comparison, adaptive ReAct tools, natural-language collector
planning, automatic follow-up diagnosis/comparison, source lines and sandboxed
patch generation plus functional/performance validation. User explicitly selected
the real Python CRM at E:/crm/AiNativeCrm-python as the integrated application.

Source contract: CRM AGENTS.md read; do not read real .env, alter original TS
CRM, reuse production data or release a production CRM. Use an isolated snapshot
and explicit test data. Existing CRM remains a migration project; profiling a
business function/endpoint does not establish full parity or production readiness.

Implementation sequence (completed; see docs/evidence/agent-v2-20261020):

1. Schema007: durable investigation and automatic verification records.
2. Capability/target-aware model collection plans and review/confirmation.
3. Automatic candidate diagnosis, deterministic comparable-profile differences,
   model reflection and scoped verdict; repeated sampling alone is not causality.
4. Isolated Python CRM snapshot, real source-SHA/cProfile line mapping, source
   tools and model patches. Bounded networkless unprivileged test containers;
   preserve tests/harness, compare correctness and repeated A/B performance.
5. UI, lifecycle/auth/parser/recovery regressions, fresh PostgreSQL and actual
   DeepSeek, CRM regression, real server sampling/repair, deploy named release.
6. Update research, development log, delivery matrix and evidence honestly.

Primary product references read 2026-10-20:
- https://docs.datadoghq.com/profiler/automated_analysis/
- https://docs.datadoghq.com/bits_ai/bits_code/
- https://grafana.com/docs/grafana-cloud/platform/grafana-assistant/guides/querying/
- https://www.dynatrace.com/hub/detail/agentic-incident-analysis/
- https://cloud.tencent.cn/document/product/248/88467

Release 20261020.7 is active at https://82.157.81.79 (API7, Web6, schema8).
Real investigation4/task11+12/diagnosis9+10/source-repair4 completed, 4.3343x
controlled CPU median; strict Python type regressions and no-op negative gate
were actually run. Earlier unsuccessful and invalidated attempts are retained.
Preserve data
volumes, old named images, current code and all previous evidence. No subagents
are authorized by the user or AGENTS. No hidden chain-of-thought should be stored;
tool decisions, observations and concise user-facing rationales are sufficient.
