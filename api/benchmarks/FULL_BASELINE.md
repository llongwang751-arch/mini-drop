# Full-platform baseline, 2026-10-16

The final minimal-index evidence is `million-task-minimal-index-windows-2026-10-16.json`. The earlier six-index exploration is preserved separately as `million-task-windows-2026-10-16.json`. This is a completed
native Windows benchmark with official portable PostgreSQL 17.11, Go 1.26.9,
one logical CPU and a shared 512 MiB Windows Job Object memory limit. PostgreSQL
and the real API HTTP router communicate over loopback. The fixture contains
1,000,000 explicitly synthetic tasks, 1,000 owners, 100 groups, 200 live task
shares and 1% soft deletion. No production records were seeded or modified.

The complete run took 45.324 seconds and peaked at 132,931,584 bytes of committed
job memory (126.77 MiB). The runner stopped and removed its own fresh cluster;
the JSON report and raw local logs remain. Package download metadata and its
observed archive digest are in `docs/evidence/portable-postgres-tool.json`.

## Actual query plans

Each cell is a single actual EXPLAIN ANALYZE execution, not a percentile. The
raw JSON retains planning/execution time, row counts, buffers, sorting and
index names. Before/after results include ordinary warm-cache differences;
their absolute timing is not a production capacity promise.

| Query | Schema 3 indexes, ms | After migration 004, ms | Selected index |
|---|---:|---:|---|
| Platform first page | 424.254 | 0.019 | Active tasks by created_at/id |
| Platform OFFSET 20,000 | 983.550 | 6.065 | Active tasks by created_at/id |
| Kind-filtered first page | 254.682 | 0.058 | Active tasks by created_at/id |
| Full owner/group/share authorization scope, first page | 248.377 | 1.167 | Active tasks by created_at/id |
| Owner first page | 0.150 | 0.070 | Existing owner index |
| Group first page | 0.337 | 0.504 | Existing group index |
| Full authorization scope, exact count | 246.712 | 228.447 | Task scan remains |
| Platform exact count | 211.515 | 178.545 | Sequential scan remains |

The final run applied the real `004_task_list_index.sql` twice, verified that
all one million tasks and 10,000 soft-deleted rows were preserved, and verified
schema version 4 and a valid/ready index. Its database grew from 416,915,983 to
448,274,959 bytes, an increase of 29.91 MiB. This is isolated migration evidence;
production application is tracked separately in the deployment log.

The earlier exploratory run added six candidate indexes and consumed an extra
81.01 MiB. Existing owner/group indexes were already fast and reverse share
indexes did not improve selected plans, so migration 004 contains only:

```sql
CREATE INDEX idx_tasks_active_created
    ON tasks(created_at DESC,id DESC)
    WHERE deleted_at IS NULL;
```

Exact totals
still scan qualifying Task rows and remain the main cost of the paginated HTTP
endpoint. Highly deep OFFSET pagination and arbitrary substring search should
be measured separately before expanding scale targets; this run does not
justify an unbounded JSONB or text GIN index.

## HTTP and streaming

| Real HTTP operation | Requests / concurrency | Errors | P95, ms |
|---|---:|---:|---:|
| Create queued Task, atomic attempt/event/outbox/audit | 16 / 1 | 0 | 5.498 |
| Create admission burst | 8 / 4 | 0 | 167.734 |
| List first page with exact million-row count | 40 / 4 | 0 | 1035.977 |
| List page 1,001 with exact count | 20 / 4 | 0 | 922.011 |

All 24 creates returned 201 and all 60 lists returned 200. The production router
has one admitted create body at a time; a more overlapping burst may therefore
return 429 by design. The actual status histogram is preserved, including zero
429s for this run. Completion status was set directly in the isolated fixture
between create requests to keep quota fencing from changing the measurement;
these tasks were never collected or analyzed.

Twenty independently authenticated streaming TCP responses opened in
26.275 ms, remained idle for 1.5 seconds, and all received one subsequently
committed TaskEvent from PostgreSQL. Delivery P95/P99 was 498.919 ms under the
endpoint's one-second polling policy. A garbage collection ran during stream connection setup, so the raw heap delta
is negative and is not used as a connection memory estimate. This run records
20 successful connections, not a maximum supported connection count.

The benchmark measures real authorization, PostgreSQL transactions, JSON
rendering, pagination and SSE. It excludes Linux/container behavior, TLS,
Nginx, Internet latency, Agent heartbeats, profiler overhead, Analyzer work and
object-store throughput. Those require their own evidence. This single small
fixture run is a baseline for regression and query-plan review, not a load-test
certification or a production QPS commitment.
