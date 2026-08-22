# Mini-Drop Official 90-Run Strategy Evaluation

- Generated at: `2026-08-22T10:04:39.009708+00:00`
- Executions: `90 / 90`
- Unique execution IDs: `90`
- Completeness gate: `PASS`
- Oracle isolated from diagnosis: `True`

## Strategy comparison

| Strategy | Runs | Average score | Exact root cause | Evidence integrity | Unsupported claims | Required evidence coverage |
|---|---:|---:|---:|---:|---:|---:|
| CONSTRAINED_HYBRID | 30 | 90.0% | 90.0% | 100.0% | 0.0% | 70.0% |
| DECISION_TREE | 30 | 90.0% | 90.0% | 100.0% | 0.0% | 70.0% |
| EXPLORATORY | 30 | 90.0% | 90.0% | 100.0% | 0.0% | 70.0% |

## Method

Every execution starts a bounded real-fault Campaign through the same-origin Web API, captures baseline, incident and recovery snapshots, links Mini-Drop collection tasks, runs a hidden-Oracle comparison, and verifies cleanup. Scoring occurs only after the Campaign reaches a terminal state.

All strategies share the same faults and test cases. Equal scores mean the strategies converge on these cases; they are not copied by the runner. Each raw Campaign remains independently auditable.
