# Engineering conventions

- Authoritative contracts: `contracts/drop.proto`, `contracts/task-kinds.json`, SQL migrations and `docs/CONTRACT.md`. Coordinate any cross-service change; preserve protobuf field numbers.
- Keep C++ collection, Go HTTP orchestration, Python analysis, and React rendering independent. Do not embed shared long-lived storage credentials in the Agent or browser.
- Persist task/attempt state and events atomically. Fence mutations by current attempt and Analyzer lease. Preserve explicit collection/analysis states.
- Use parameterized SQL, argv-based child execution, bounded inputs and outputs. Never log credentials, cookies, presigned URLs, or raw profiles.
- Add meaningful regression tests for changed lifecycle, concurrency, authorization, parser and recovery behavior. Keep fixtures explicitly marked as imported data.
- Format Go using gofmt and TypeScript/CSS using Prettier. Compile C++ with warnings as errors. Check build and relevant tests before deploying.
- Update `DEVELOPMENT_LOG.md` and `docs/DELIVERY_MATRIX.md` when scope, evidence, deployment or limitations change. Report only checks actually run.
- Deployment was authorized to 120.24.187.205; on 2026-10-20 the user explicitly changed the deployment target to 82.157.81.79 (ubuntu, provided beij.pem). Preserve data volumes and named release images on both hosts; never run `compose down -v` on production.
