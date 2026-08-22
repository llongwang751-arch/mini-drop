# Same-input diagnostic bundle

## Incident

- Case: `RW-OTELPY-4224`
- Symptom: repeated creation and shutdown of OpenTelemetry Python metric readers/exporters leaves objects retained after forced garbage collection.
- Base revision: `679297f5ebd37510b6c9e086fc27837935d57e81`
- Fix revision: `84c6b0a419226328b6884b43a61cfd7a8fa3b3bb`

## Immutable observations

The same neutral harness was executed three times on each revision. Each run created and shut down 250 `MeterProvider`, `PeriodicExportingMetricReader`, and exporter instances, removed strong references, then forced garbage collection.

### Base revision

- Run 1: retained exporters=250, readers=250, providers=0, all_collected=false
- Run 2: retained exporters=250, readers=250, providers=0, all_collected=false
- Run 3: retained exporters=250, readers=250, providers=0, all_collected=false
- Evidence SHA-256: `a8d0d4ad9dc0c329acc31cba2702e8ecc93c2dcfd7a6bc93f9d80dcfe5870037`

### Fixed revision

- Run 1: retained exporters=0, readers=0, providers=0, all_collected=true
- Run 2: retained exporters=0, readers=0, providers=0, all_collected=true
- Run 3: retained exporters=0, readers=0, providers=0, all_collected=true
- Evidence SHA-256: `fa31f0d99b7105d2764fca6846bc158c1c9085f18cd756fa1ffbac90e9ed8fdc`

## Task

Diagnose the most specific root cause that is justified by these observations. Do not invent source lines or mechanisms that are absent from the evidence. Return a concise JSON object with these keys:

- `verdict`: `CONFIRMED`, `PARTIAL`, or `INSUFFICIENT_EVIDENCE`
- `root_cause`
- `evidence_refs`: an array containing only evidence identifiers present above
- `confidence`: number from 0 to 1
- `boundary`: what this evidence still cannot prove
- `next_verification`

