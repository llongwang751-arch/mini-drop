# Controlled live demo workloads

These are explicitly synthetic targets that execute real workloads and emit no synthetic profiler output. They reuse reviewed fixtures under core/fixtures. No CRM data, credentials or public ports are configured. They join the existing trusted sampling PID/network namespace as UID10001; they are not a sandbox for untrusted business code.

Build from the repository root with `docker build -t drop-demo-workloads:20261020.1 -f integrations/demo-workloads/Dockerfile .` on the authorized host, where the preserved `drop-agent:20261020.1` base is available. This exact tag has already been deployed; use a new tag for changed inputs. Go and Java compile normally; C++ compiles with warnings as errors. The shared base contains the same runtime/profiler libraries used by the collector.

Merge deploy/compose.demo.yml after the existing Tencent overlays and enable the demo profile. The shared helper_incoming volume must already exist on the bounded helper filesystem. Before starting demo-io, create its `demo-io-data` directory there, owner10001:10001, mode0700; it holds one bounded4KiB file. Do not substitute an unrestricted host directory. Each workload has explicit CPU/memory/PID limits and read-only rootfs. Java/native/memray share the bounded receive filesystem for the existing cooperative profiling protocol. No Docker socket is mounted.

Current deployment enables COMPOSE_PROFILES=demo. When recreating the Agent, recreate its dependent demo targets together so their PID/network namespaces follow the new Agent. Discover fresh PID/UID/start-time through Agent resources; never reuse recorded PIDs across restarts.

The dedicated `demo-bolt` target adds a deterministic integer CPU workload. Build a new `drop-demo-workloads:20261020.2` tag with `Dockerfile.bolt` to reuse the preserved language image and compile only this small target; set `DEMO_RELEASE_TAG` for a later tag. Its ELF retains symbols, relocations and a build-id. `--once` supplies a fixed functional comparison input. See [BOLT acceptance and constraints](../../docs/BOLT_DEMO.md) before interpreting generated artifacts or speed measurements.

See [current acceptance](../../docs/DEMO_SCENARIOS.md) for real task links and limits. Automatic source repair requires an independently enrolled source snapshot and regression harness.
