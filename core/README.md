# C++ collection plane

Build from the repository root:

```sh
docker build --target runtime -f core/Dockerfile -t drop-core-runtime:v2 .
```

The image contains `drop_server`, `drop_agent`, and the bounded CPU validation
workload. Override the entrypoint for Agent. Build runs CTest with warnings
treated as errors and a single compiler process to fit the target server.

Server uses `DATABASE_URL` (or `DATABASE_URL_FILE`), `TLS_CA_FILE`,
`TLS_CERT_FILE`, `TLS_KEY_FILE`, and optional `GRPC_LISTEN` (default
`0.0.0.0:50051`). Agent also uses `DROP_SERVER_ADDRESS`, `AGENT_ID`,
`AGENT_HOSTNAME`, `AGENT_IP`, and `WORK_DIR`. The CA is always required;
there is no insecure transport mode. Client certificates must have CN
`drop-api` or `drop-agent-{agent_id}` as appropriate.

Agent advertises `PERF_CPU` only after a real perf record probe succeeds.
`PROC_CPU` reads Linux thread CPU ticks and never claims to capture function
stacks. If the host kernel, seccomp policy or capabilities block perf, its
unavailable reason appears in resources and no perf data is fabricated.

`RUN_CONTROLLED_WORKLOAD` defaults to `false`; normal business Agents do not
launch a demo CPU load. Set it to `true` only on a demonstration node. With
default target policy, only that explicitly enabled workload is authorized.
An administrator may set `ALLOW_HOST_PROCESSES=true` and
`ALLOWED_TARGET_UIDS=10001,10002` on a trusted host to authorize actual business
processes. Each collection verifies UID, PID start time, PID namespace and
executable identity again, including while the runner is active.
Run perf-capable Agents with the minimum
host permissions needed for that host; `CAP_PERFMON` and appropriate kernel
policy are usually required. The default image user is UID 10001.

The PostgreSQL queue is authoritative. Delivered/running tasks refresh a
45-second execution lease through heartbeat; expired attempts become explicit
failures and require a new retry attempt. This avoids silently executing an
uncertain attempt twice. Local fsync journals retry uploaded result notification
after restart. A restart in the middle of collection reports `AGENT_RESTARTED`.
Cancellation returns on the next two-second heartbeat and terminates the
runner process group. RPC calls have five-second deadlines.

The Agent receives only a task-scoped presigned PUT URL. It has no S3 access
key. URLs and protobuf recovery journals remain in its private 0700 working
directory and never enter logs. A journal is removed only after Server
acknowledges its terminal result.

The optional `drop_rpc_probe` executable runs against an initialized control
plane. It requires `DATABASE_URL_FILE`, `DROP_SERVER_ADDRESS`, `TLS_CA_FILE`,
`TEST_API_CERT_FILE`, `TEST_API_KEY_FILE`, `TEST_AGENT_CERT_FILE`,
`TEST_AGENT_KEY_FILE`, and `AGENT_ID`. It verifies certificate-role isolation,
stale cancel/progress/result rejection, stale heartbeat lease protection and
current cancellation. It creates and deletes only its own clearly named test
Task/Attempt/User fixtures; run it through the private Compose network.

V2 collection uses `contracts/task-kinds.json` as its schema and capability
catalog. The minimal runtime includes perf and procfs collection. A separate
full tools image adds actual async-profiler 4.1, py-spy 0.4.1, memray 1.20.0,
OpenJDK 17, bpftrace, gperftools, jemalloc and LLVM BOLT 16:

```sh
# Standard reproducible bundle, with the tools stage cached independently.
docker build --target full-tools -f core/Dockerfile -t drop-core-full-tools:v2 .
# Or combine an already trusted tools base with a newly tested core runtime.
docker build -f core/Dockerfile.full --build-arg CORE_IMAGE=drop-core-runtime:v2 \
  --build-arg TOOLS_BASE=drop-core-full-tools-base:v2 -t drop-core-full-agent:v2 .
```

The tools installer maps x86_64/aarch64 async-profiler archives to their pinned
official SHA256. Package versions, executable hashes and profiler support
library hashes are recorded in `/opt/drop-tools/manifest.json`. Registry checks
these at startup and before every execution. External installations must supply
the catalog's `*_EXECUTABLE` and trusted `*_SHA256` or a trusted manifest; merely
finding an executable does not make the capability available. Use absolute
paths. Multicall aliases such as perf2bolt retain their configured argv[0].

Additional target policy is explicit:

| Environment | Meaning |
| --- | --- |
| `ALLOW_CONTAINERS=true` | Enable supported Docker/containerd resolution |
| `ALLOWED_CONTAINER_IDS` | Comma separated exact container IDs; no wildcard |
| `DOCKER_EXECUTABLE`, `DOCKER_SHA256` | Trusted Docker inspect client |
| `CTR_EXECUTABLE`, `CTR_SHA256` | Trusted containerd client |
| containerd namespace | The supported Kubernetes containerd namespace is `k8s.io` |
| `ALLOW_HOST_RESOURCES=true` | Permit node resource snapshots |
| `ALLOW_KERNEL_LOGS=true` | Attempt bounded, nonconsuming kernel log read with existing permissions |
| `NATIVE_CONTROL_ROOT` | App owned 0700 directory for cooperative profiler sockets |
| `DROP_SERVER_ADDRESSES` | Up to eight comma separated TLS host:port endpoints, in priority order |
| `METRICS_LISTEN` | IPv4 metrics address; defaults 127.0.0.1:9464 Server / :9465 Agent; `off` disables |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Optional HTTP OTLP collector base endpoint |
| `BUILD_COMMIT`, `AGENT_CONFIG_VERSION` | Registration build/configuration metadata |

The runtime must see the selected process namespace and procfs. Container
resolution additionally requires access to its explicitly chosen runtime
socket; this access is powerful and is never enabled by default. It resolves
the namespace-local PID through the inspected init process, verifies cgroup,
UID and start time, and rejects targets outside the exact ID allowlist. Go
pprof collection verifies that the chosen process owns the endpoint's listening
socket; its network namespace must also be reachable. No blanket namespace
entry or host PID mode is implied by the Agent image.

Native allocation profiling requires application cooperation. Link
`libdrop_native_profiler.so`, include `drop/native_profiler.h`, and call
`drop_native_profiler_start()` from the actual business process. The helper
authenticates the controller's Unix credentials, invokes real ProfilerStart /
ProfilerStop, heap-profiler APIs or jemalloc mallctl, and returns bounded native
binary output. `fixtures/native_workload.cpp` shows the toy integration. It
does not inject an allocator into arbitrary business processes.

Give each application PID namespace its own private control root when profiling
multiple containers: socket names use the application's namespace-local PID.
The Agent and application must share that root through the chosen mount; peer
credentials are verified against the pinned controller-visible target PID.

Each runner owns Validate → Prepare → Start → Poll → Stop → Collect → Cleanup.
Its managed context owns the worker and process group. Cancellation, target
identity change and deadlines stop execution; cleanup joins it and removes
known intermediates on success and failure. SCRIPT accepts only compiled
diagnostic templates, never arbitrary shell text. BPF tracing requires
CAP_BPF + CAP_PERFMON, kernel BTF, tracing metadata and a seccomp policy allowing
the bpf/perf syscalls. Python attachment additionally needs the applicable
ptrace permission. Java heap and memray files are genuine binary captures.

The resource snapshot reports actual target procfs samples, namespace-wide
network counters (explicitly not per-process traffic), main-thread context
switches, host context switches and interval collector CPU/RSS overhead.
Kernel logs are capped at 200 lines / 1024 characters per line / 64 KiB total;
policy and permission failures carry an explicit status rather than empty
success. No SYSLOG capability is automatically requested.

`core/tests/rpc_isolated.sh` and `compat_isolated.py` create private temporary
PostgreSQL/PUT fixtures for real mTLS, fencing and N/N-1 collection checks.
`fixtures/collect_languages.py`, `collect_native.py`, `collect_advanced.py` and
`verify_targets.py` collect explicitly marked toy evidence. They never select
production business PIDs or mutate production volumes. See
`core/DEVELOPMENT.md` and `core/evidence/` for checks actually executed.


## Dedicated collector helper

`COLLECTOR_HELPER_SOCKET=/run/drop-helper/control.sock` selects the separate
collector helper. The mTLS Agent runs as UID/GID 10001 with all capabilities
dropped. It owns RPC, task journals, upload grants and PUT operations. The root
helper receives only bounded typed collection parameters over a Unix stream;
commands, paths, environment variables, upload URLs and credentials are not
accepted. Both sides verify `SO_PEERCRED`. The helper independently rechecks
its target UID/policy, PID start time, namespace, executable device/inode and
trusted profiler hashes. It accepts one active capture, four bounded IPC
connections, explicit per-child resource limits and token-scoped cancellation.

The helper runs as `0:10001` with PERFMON and SYS_PTRACE. DAC_READ_SEARCH is
needed to read the target's ordinary UID-10001:0600 memray output; it grants
read access, not ownership changes or writes. BPF is optional for trace runners.
No SYS_ADMIN, privileged mode, host PID namespace, Agent TLS key or object-store
credential is added to the helper. Without the read capability, helper-based
Python-memory collection is explicitly unavailable.

Private helper journals, profiler logs and intermediates remain root-owned
0700. Only a fixed RAW filename is passed to a separate shared receive mount,
`/run/drop-helper-incoming`, using root:GID-10001 sticky 1770 token directories.
A helper-attached Python-memory target must share this path in its mount namespace and belong to
that group. Other host/container targets need the equivalent explicit mount
and group policy; seeing their PID alone is insufficient. The helper freezes
RAW through O_NOFOLLOW/O_NONBLOCK, regular-file/owner/single-link and bounded-size
checks, stable device/inode/mtime/ctime checks and another target identity check,
then fsyncs its root-private copy. Target files never become helper state/log
paths. RAW and optional ELF stream to the Agent in 64KiB chunks with SHA checks.

A random 256-bit operation token and canonical request are durable before
sampling. Completed bytes survive helper/Agent restarts. Recovery uses `resume`
only, never a second collection. The Agent fsyncs every returned file and its
result journal before ACK; the helper persists an ACKED tombstone before removing
cached bytes. Repeated ACK and already-ACKED resume cannot trigger new sampling.
An interrupted capture reports a fixed failure instead of repeating uncertain
work. The default journal retention is 24 hours.

`COLLECTOR_HELPER_DISK_BYTES` defaults to 2GiB, covering the complete default
PYTHON_MEMORY/BOLT_PROFILE reservations and shared Java/native RAW storage. Private cache, scratch
and the shared receive mount must live on one filesystem with capacity at most
this limit; startup fails closed by default. The deployment prepares a dedicated
bounded filesystem. An explicit `COLLECTOR_HELPER_REQUIRE_BOUNDED_FS=false` is
reserved for isolated IPC fixtures and does not establish a physical disk bound.
Admission reserves worst-case concurrent files and free space; collection also
monitors the combined private/incoming bytes. A pre-existing target process does
not inherit the profiler client's RLIMIT_FSIZE. The dedicated filesystem is the
hard bound for cooperative writers; cancellation does not kill business targets.

`helper_test` covers typed requests, budget defaults and FIFO/hard-link journal
faults. `tests/helper_ipc_isolated.py` uses real Linux UIDs/Unix sockets with
explicitly imported deterministic profiler bytes for transport, cache, ACK,
cancellation and persistence faults. It is not native sampling evidence.
`DROP_HELPER_MODE=1 DROP_HELPER_SPOOL=<bounded-spool> python3
core/tests/upload_recovery_isolated.py <private-tls>` selects the actual
PERF_CPU/mTLS/PostgreSQL/PUT recovery fixture and delayed helper capability
registration. See `core/evidence/` for checks actually executed.


## Configuration and structured logs

`drop_server`, `drop_agent` and `drop_collector_helper` accept a bounded flat JSON
configuration and explicit non-secret overrides:

```sh
drop_agent --config /etc/drop/agent.json --set AGENT_ID=worker-one --set METRICS_LISTEN=127.0.0.1:9465
drop_server --set DATABASE_URL_FILE=/run/secrets/database_url
```

Keys use the existing uppercase environment names. The precedence is CLI,
environment, JSON file, then the component's existing safe default. In each
individual tier, `KEY_FILE` takes precedence over `KEY`; an environment file
reference does not override a higher-priority CLI value. JSON values are strings,
booleans or integers. Unknown keys, duplicate overrides, nested objects, oversized
files/values and control characters are rejected. Files must be absolute,
protected regular files, never symlinks/FIFOs or group/world-writable files.
Existing root-owned read-only Docker secret mounts remain supported.

Database URLs and private credential values cannot be passed inline through CLI
or JSON; use an existing environment variable or an explicit `_FILE` reference.
Every successful service startup emits its resolved active defaults and explicit
settings after redaction, their source tiers, and a stable SHA256 of canonical
redacted values. Secret plaintext and secret-file contents never participate in
this hash. Credential-bearing URLs retain only their public scheme/host/port;
userinfo, path, query and fragments are hidden. Private-key references are also
hidden. This fingerprint records public configuration; it is not a secret-rotation
or private-file integrity fingerprint.

All service log records carry `time` (UTC RFC3339 milliseconds), `level`, `service`,
`version`, `request_id`, `task_id`, `attempt_id`, `agent_id`, `stage`, `duration_ms`,
`error_code` and `message`. Unknown context is null. Collection and upload logs
provide their actual Task/Attempt/request identity and elapsed time. Startup adds
only the sanitized configuration object. A shared serializer limits messages,
filters identifier fields and writes one complete JSON line under a mutex.
Profiler diagnostics, arbitrary argv, profile bytes and transport credentials are
not included in these service logs. The fixed public runner.log artifact remains
its separate, deliberately smaller contract.


Java CPU/allocation/lock and heap collection use the ordinary same-UID Agent,
not a root attach launcher. The target must have the Agent UID and expose the
explicit shared profiler library/receive mounts. CPU collection selects the real
async-profiler `ctimer` engine and reports that engine and the lack of kernel
perf-event stacks in metadata. Allocation and lock retain their requested events.
Native gperftools/jemalloc cooperation also uses the same-UID Agent/socket path.
The current native adapter requires schema2. Its socket and private profiler
scratch both use the application-owned0700 `NATIVE_CONTROL_ROOT`; CPU profiler
files, gperftools heap-dump suffix files and jemalloc dumps never use `/tmp`.
With the dedicated helper configured, the Agent defaults this directory to
`/run/drop-helper-incoming/native-control`, creates only that direct receive-root
child safely, and verifies ownership, permissions and the same bounded filesystem.
An external application must explicitly share/preconfigure the same spool-backed
control root and use the upgraded library. Both peers check the numeric filesystem
capacity requirement. IPC never accepts a scratch path. Old protocol adapters fail
closed and require an upgrade before these tasks can run.
These RAW files live in random Agent-owned 0700 task directories on the same
bounded receive filesystem. State, logs, grants and manifests stay in the Agent's
private journal. A path-free token marker resolves the same fixed RAW on restart;
fsync precedes result journaling, uploaded files survive interrupted PUTs, completed
files are removed after acknowledgement, and expired unreferenced owned directories
are swept without touching another Agent's prefix. No SETUID/SETGID/KILL or write
DAC capability is introduced to support Java attachment.

Boolean policy configuration accepts only literal `true` or `false`, including
environment and `_FILE` values; misspellings fail startup. In particular, an
invalid `COLLECTOR_HELPER_REQUIRE_BOUNDED_FS` cannot silently disable the quota.

Python memory capture uses the pinned GDB executable through the separate
`drop_gdb_ptrace` launcher, exposed only by the owned
`/opt/drop-tools/gdb-bin/gdb` alias. The launcher checks both GDB and its own
SHA256 against the protected tool manifest, then installs an inherited seccomp
filter before executing the already-verified GDB descriptor. The filter denies
`O_RDWR` file opens and `openat2`, making GDB use its existing ptrace memory
backend instead of writable proc-mem files. The memray parent and the running
application do not inherit this filter. No UID switching or additional capability
is needed. The alias and launcher must be present in GDB's `support_files`; an
older tools manifest disables this capability until the runtime is upgraded.
`TOOL_MANIFEST_FILE` must consistently describe the selected real GDB path.

Memray's `--duration` is asynchronous. The collector waits for that interval and
then inspects the pinned target's descriptors to wait for cooperative output and
compression handles to close, bounded by ten seconds and4096 descriptors. It
then freezes the original bytes with the existing inode/size/time and fsync
checks. Experimental LLDB captures that failed are retained as diagnostic
evidence and are not a validated deployment backend.

The r18 strict Linux build passes all16 CTest suites. Fixed proc-root/exe paths
also make all four actual PyMem symbols and target memory readable in the
cross-UID diagnostic. Complete512MiB GDB/memray collection still times out after
32.28s, with the target alive and no OOM. This backend remains under verification;
tool presence, symbol readability and imported policy tests do not establish
successful memory collection.

The launcher now also derives its one target PID from canonical `-p`/`--pid`
arguments, rejects duplicate/alternate PID forms and positional target overrides,
and checks the Runner's private numeric identity before and immediately before
exec (UID, start ticks, PID namespace and executable inode). It supplies only
fixed `/proc/PID/root` and `/proc/PID/exe` paths for GDB's sysroot, executable
and symbols, with target auto-loading disabled. This avoids requesting namespace
entry privileges. The added `DROP_GDB_TARGET_IDENTITY` is internal child metadata;
it is never accepted from helper IPC or exposed in service configuration. The
policy changes passed r16 strict tests; complete memory capture remains unverified.

Allocator tracing now passes the pinned target PID through bpftrace's `-p`, in
addition to its existing namespace predicate and output limits. This confines
multi-uprobes to the selected process rather than every process mapping that
libc inode. Its new regression failed against the previous implementation and
passed with the change in the full16-suite strict build. Actual512MiB allocator
collection still timed out at64.516s with the toy alive and no OOM; a minimal
selected-PID multi-uprobe independently recorded positive hits. These establish
the narrower attachment scope, not successful complete allocator collection.
The change does not remap the kernel TID embedded in bpftrace user stacks or
imply full namespace entry or symbol resolution.

The r17 diagnostic-only memray checkpoint found the debugger sleeping and the
toy ptrace-stopped after the script's library-access sentinel. Interrupting the
owned debugger did not preserve that toy: it exited. This is explicitly failed
recovery evidence. Follow-up diagnostics use a SHA-verified copy of the upstream
script with fixed call-stage markers and GDB's bounded-call/unwind settings;
it leaves the deployed script unchanged. A separate perf/raw stack comparison
uses actual RunnerBuilder output and changes only the stack format. These
diagnostic controls are not production capture results.

Both r18 controls have now executed. Perf and raw allocator stacks each emitted
172 allocations and172 frees with344 complete stack blocks, but neither process
exited before the bounded interrupt. The memray stage markers confirm access,
dlopen and injector-symbol loading completed; the call to `memray_spawn_client`
did not return. System libthread_db was active. Five-second GDB call settings
did not make this case terminate safely; killing the owned debugger again left
the toy exited. Neither full collection nor target-preserving cancellation has
passed. A diagnostic-only allocator stop gate is prepared to test a possible
multi-uprobe/drain issue in bpftrace0.20, without changing the deployed Runner.
