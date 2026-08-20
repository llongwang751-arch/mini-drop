# Isolated OpenTelemetry Demo Fixtures

This directory defines the bounded OpenTelemetry Demo subset used by the live
`T1-CPU-001` and `T1-MEM-001` benchmark executions. It does not reuse the
upstream Compose project, container names, network name, collector host mounts,
or Docker socket mount.

## Preconditions

- The OTel source tree is pinned at
  `3684411da9a4dc3e77cddfef929a630d6f5af6c5`; cloud runs set
  `MINI_DROP_OTEL_SOURCE_ROOT=/opt/mini-drop-otel-3684411`.
- The selected flag fixture has been copied to a writable, per-run directory.
- `MINI_DROP_OTEL_SOURCE_ROOT`, `MINI_DROP_OTEL_SCENARIO`,
  `MINI_DROP_OTEL_EXPIRES_AT`, and `MINI_DROP_OTEL_FLAG_DIR` are set for that run.
- Every invocation supplies a unique Compose project name with `--project-name`.
- CPU and memory fixtures run separately or strictly serially.

The checked-in flag JSON files are templates. Do not mount them directly for a
formal run because flagd and the runner need an isolated writable copy.

## Static validation

From the repository root, set the three required variables and merge the common
file with exactly one case file:

```sh
export MINI_DROP_OTEL_SOURCE_ROOT=/opt/mini-drop-otel-3684411
export MINI_DROP_OTEL_SCENARIO=T1-CPU-001
export MINI_DROP_OTEL_EXPIRES_AT=2026-08-20T00:00:00Z
export MINI_DROP_OTEL_FLAG_DIR=/srv/mini-drop/otel-runs/cpu-smoke/flags
docker compose \
  -f deploy/otel-demo/compose.common.yaml \
  -f deploy/otel-demo/compose.t1-cpu-001.yaml \
  --project-name mini-drop-otel-cpu-smoke \
  config --quiet
```

Use `compose.t1-mem-001.yaml` and a different project name and flag directory
for the memory case.

## Runtime boundaries

- Ad is limited to 0.5 CPU and a 120-second maximum fault window.
- Email is limited to 100 MiB. The runner must abort the incident before 80%
  memory use becomes an OOM and must classify an OOM or restart as fixture
  failure.
- Only the target application port is published, using an automatically
  allocated port bound to `127.0.0.1`. Flagd and OTLP remain private.
- Cleanup always uses the same explicit `-f` files and project name. Do not use
  `--remove-orphans`, volume deletion, or Docker prune.
- CPU recovery resets `adHighCpu` to `off` and sends another Ad request so the
  service evaluates the new value.
- Memory recovery resets `emailMemoryLeak` to `off`, restarts only the isolated
  Email service, waits for health, and verifies that its host PID changed.

These files are suitable for local syntax validation. Final fault, perf, and
eBPF acceptance is performed on the dedicated native Linux cloud workers.
