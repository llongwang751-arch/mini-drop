# Bounded Core Server heartbeat baseline

This independent Go tool imports `drop/api/gen/dropv1` through a local module
replacement. It does not copy protobuf definitions or modify the API module.
It calls the actual Core `InitAgent.Init` and `HealthCheck.Do` methods against a
new private PostgreSQL17.11 database and an explicitly selected Core image.
This baseline is separate from API HTTP latency and Analyzer throughput.

The load consists of **synthetic logical Agents**, not business collectors.
Each Agent has its own ECDSA-P256 mTLS identity with CN
`drop-agent-<agent_id>` and matching DNS SAN. The Server has SAN `drop-server`.
The temporary CA expires after two hours; its private key is mounted only into
the certificate preparer/client, never the Server. All TLS material is generated
for this fixture. No existing deployment secret is read.

## Workload and interpretation

- Stages run serially with10,25 and50 Agent identities. Agent IDs are reused
  across stages, so the largest database population is50 Agents.
- Each stage performs actual registration and one warmup heartbeat per Agent.
  Their TLS/setup/SQL cost is excluded from measured requests.
- Each Agent then sends10 heartbeats on a fixed two-second cadence, matching
  the Server's current recommendation. At most8 RPCs are in flight; requests
  are not retried. Default totals are850 measured heartbeats plus85
  registrations and85 warmup heartbeats.
- Each measured stage lasts at least20 seconds, including the last cadence
  interval. Successful throughput uses this complete wall time. RPC latency
  starts when a worker begins its call; scheduler/worker queue delay is reported
  separately. Slow rounds can drift and increase both elapsed and queue time;
  the tool does not create an unbounded request backlog.
- P50/P95/P99 use nearest rank over successful RPCs only. Failures have separate
  counts and gRPC codes. No successful samples yields `null` percentiles.
- Collector capabilities, task IDs and events are empty. Resource JSON explicitly
  marks the payload as synthetic. Unset protobuf CPU/memory/load values become
  zero defaults; they are placeholders, not collected host measurements. Any
  returned task/cancellation fails the fixture. No task/profile is generated.
- The tool stops before increasing load if a stage fails. Each RPC has a
  five-second timeout and the entire client has a180-second deadline.

This small, paced run does not identify saturation, maximum capacity, production
availability or an SLA. Its finite percentiles apply only to its candidate,
resource limits, shared-host conditions and sample count.

## Local build and checks

From this directory, with the repository's pinned Go toolchain available:

```powershell
go test ./...
go vet ./...
go build -trimpath -o heartbeat-baseline.exe .
$env:GOOS = 'linux'
$env:GOARCH = 'amd64'
$env:CGO_ENABLED = '0'
go build -trimpath -o heartbeat-baseline .
```

The local tests cover percentile math, failure accounting, scheduler delay,
distinct certificate identities/SAN, existing-fixture overwrite refusal and
bounded load options. They do not start a substitute gRPC server. A successful
build/test is preparation, not a completed Linux performance measurement.

## Serialized Linux execution

The root deployment task owns the remote queue. Run this only after explicit
queue handoff, using the reviewed static Linux binary and a **new unused** report
directory. The runner requires root on the Docker host for temporary UID10001
fixture permissions. The selected image must contain the actual candidate
`/usr/local/bin/drop_server` and its catalog. PostgreSQL17.11-alpine and that
candidate must already be present; this runner does not pull or build images.

```bash
chmod 0755 scripts/benchmarks/heartbeat-baseline
bash scripts/benchmarks/run-isolated.sh \
  --binary scripts/benchmarks/heartbeat-baseline \
  --core-image drop-core:<reviewed-candidate> \
  --io-device /dev/<verified-host-block-device> \
  --output /opt/drop-build/<new-heartbeat-evidence-directory>
```

The runner resolves both images to immutable IDs before use, applies every
current SQL migration to a new disposable PostgreSQL data tmpfs, and waits for
the actual schema and private RPC listener. It creates an internal Docker
network, publishes no ports and mounts no production volume or credential.

| Container | Memory / combined memory+swap | CPU | Device read/write limit |
| --- | --- | --- | --- |
| PostgreSQL | 192 /192MiB | .30 | 4/2MiB/s |
| Candidate Server | 192 /192MiB | .45 | 2/1MiB/s |
| Client | 128 /128MiB | .25 | 2/1MiB/s |
| Concurrent total | 512MiB / no additional swap | 1 CPU | 8/4MiB/s |

The128MiB certificate preparer runs before these three containers, network
none. Server/client run as UID10001 with a read-only root, no capabilities and
no-new-privileges. PostgreSQL uses its standard isolated image initialization.
The client uses `GOMAXPROCS=1` and `GOMEMLIMIT=96MiB`. The report labels these
constants as **requested** limits; `resources.json` separately records actual
Docker resource inspection, published ports and OOM status without environment
or credential contents. Kernel, architecture, Go/Docker versions, image IDs,
visible logical CPUs and client cgroup limits accompany the workload result.
Host `/proc/meminfo` is explicitly labelled as host memory, not container RSS.

Outputs are `heartbeat.json`, `resources.json`, `core.log`, preparation/status
files and the client binary SHA256. They contain no private key, certificate
body, database DSN or gRPC error text. A failure preserves diagnostic reports.
Cleanup removes only this run's ownership-labelled containers/network and the
known files in its own `mktemp` directory; no recursive deletion is used. A
foreign resource is preserved. Production resources and older images are never
removed. Host-side report files remain for review.

## Current evidence

The first isolated Linux run completed on 2026-10-23 (UTC+8) using Go1.26.10,
the reviewed Core/Agent image and fresh PostgreSQL17.11. The10/25/50 identity
stages completed all850 measured heartbeats without error. Actual image IDs,
limits, finite percentiles and queue delays are in
[the execution receipt](../../docs/evidence/jd-quality-20261023/core-heartbeat.json).
This closes the baseline execution gate; maximum capacity, business collection
load and long-term availability remain outside its scope.
