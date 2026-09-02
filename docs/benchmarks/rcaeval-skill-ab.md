# RCAEval RE1 Route Skill A/B

## Purpose

This benchmark measures whether a validated Mini-Drop Route Skill improves root-cause service ranking on external telemetry. It is separate from the generated retrieval-contract benchmark and from a live Linux Collector campaign.

Source: [RCAEval](https://github.com/phamquiluan/RCAEval), RE1 metric-only cases.

## Dataset And Isolation

- Downloaded scope: 375 RE1 cases across Online Boutique, Sock Shop, and Train Ticket.
- Train: repetitions 1-3, 223 valid cases.
- Blind test: repetitions 4-5, 150 cases.
- Excluded: 2 training cases with no usable pre-injection window.
- The upstream revision and index SHA-256 are recorded in `report.json`.
- The predictor receives an opaque case ID and pre/post telemetry signature only.
- Source case names, root-cause service, and fault labels stay in the private evaluator until both predictions are frozen.

The directory names in the upstream dataset contain labels. The adapter reads a file into an anonymous `TelemetrySignature` before invoking either predictor, and the signature has no path or oracle field. Unit tests enforce that boundary.

## Compared Arms

No-Skill uses a robust pre/post anomaly score and gives equal weight to metric families that actually exist in the current system.

Skill-enabled first applies an exact environment filter. Route Skills are fitted on repetitions 1-2 and admitted on repetition 3. A Skill stores metric-family priorities and applicability, not a root-cause service. The selected route is blended with the no-Skill evidence ranking; unvalidated routes fall back to no-Skill.

## Frozen Result

| Metric | No-Skill | Skill | Delta |
|---|---:|---:|---:|
| Root service Top-1 | 64.00% (96/150) | 66.00% (99/150) | +2.00 pp |
| Top-3 | 74.67% | 74.67% | 0.00 pp |
| Top-5 | 79.33% | 79.33% | 0.00 pp |
| MRR | 0.7225 | 0.7337 | +0.0111 |

- Paired Top-1 transitions: 3 improved, 0 regressed, 147 unchanged.
- Skill activation: 11/150 cases, or 7.33% coverage.
- Exact paired binomial test: p = 0.25.
- The 95% Wilson intervals overlap, so the overall uplift is not statistically significant.

This supports a narrow claim: the train-only admission gate found a small subset where route reuse improved ranking without observed Top-1 negative transfer. It does not support a claim that Skill generally raises production RCA accuracy by a fixed percentage.

## What The Older 40% Number Means

The repository's generated 15-case contract fixture reports 6/15 (40%) for a static route prior and 15/15 (100%) after Skill. That is a route-selection and lifecycle contract test, not root-cause accuracy on real telemetry. It must not be merged with the RCAEval 64% to 66% result.

## Reproduce

```powershell
python -m pip install -e ".[benchmark]"
python scripts/run_rcaeval_skill_ab.py --download
```

Outputs:

- `artifacts/rcaeval-skill-ab/report.json`: complete machine-readable report and anonymous case outcomes.
- `artifacts/rcaeval-skill-ab/report.md`: concise result card.
- `artifacts/external/rcaeval/`: ignored local copy of the public dataset subset.

## Remaining Validation

Long-running stability testing means repeatedly running the real scheduling, checkpoint, lease, analysis, and Skill selection path for hours while monitoring failures, backlog, latency, and memory growth. It validates operational reliability, not diagnosis accuracy.

Online A/B calibration means assigning comparable real incidents to sticky No-Skill and Skill arms, then comparing accepted root-cause accuracy, time to sufficient evidence, unsafe or unnecessary probes, and human override rate. It has not been performed here. More systems, split seeds, RE2 logs/traces, and production-like incidents are still required before changing the production Skill gate.
