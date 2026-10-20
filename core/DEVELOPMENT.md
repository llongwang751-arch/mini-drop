# Core development record

2026-10-16 (Asia/Shanghai)

1. Read the collection-plane, security, recovery and test sections of the source
   guide. Waited for the shared protobuf and SQL contract before implementing
   RPC/database code.
2. Added domain tests for argv construction, injection-shaped inputs, PID and
   sampling budgets, path traversal and terminal-state protection. Ran the
   independent domain test on the development machine: 17 checks passed.
3. Implemented argv-only fork/execve execution, fixed environment, process-group
   cancellation and deadlines, output/file/address-space/CPU limits, and private
   fsync recovery journals. Added Linux process-execution tests before deployment.
4. Implemented PostgreSQL-backed dispatch and attempts, certificate-bound Agent
   identity, separate API certificate access, heartbeat lease reconciliation,
   cancellation, idempotent result handling and transactional RAW/job handoff.
5. Implemented real perf record/script folding, verified capability probing,
   explicitly labelled procfs thread CPU accounting, and a controlled workload.
   Added parser fixtures for aggregation, frame order, delimiters, empty input,
   oversized lines and depth overflow.
6. Started isolated Docker build on the authorized server at
   `/opt/drop-build/core`, with one compiler process. Ubuntu 24.04 dependencies
   installed successfully. The first strict build found a GCC 13
   `ignored-attributes` warning in a FILE deleter; replaced it with an explicit
   deleter type and restarted the build. No production services changed.

The public contract remains owned by the main task. Compilation, CTest and
end-to-end acceptance results are recorded below when verified.

7. GCC 13 / Ubuntu 24.04 build completed successfully for Server, Agent,
   controlled workload and RPC probe with `-Wall -Wextra -Wpedantic -Werror`.
   CTest passed 3/3 suites (domain, Linux process execution, perf parser).
8. Applied the shared contract's attempt-scoped cancellation extension. Added
   current/stale/missing attempt regressions. All result/progress paths lock
   the Task before re-reading its current attempt, avoiding an old snapshot
   after concurrent retry. Old heartbeat attempts receive cancellation without
   updating the new attempt's lease.
9. Added an explicit RPC regression probe for real mTLS identity checks and
   stale-attempt operations against the deployed database/control plane.
   It is not counted as passed until executed against that environment.
10. Bounded each PUT to 20 seconds and renews the UPLOADING lease between
    retries, including restart recovery; no new sampling is started by recovery.
11. Corrected PROC_CPU frequency semantics: snapshots now occur at the requested
    1–10 Hz and accumulate nonnegative per-thread CPU deltas, including threads
    born during the interval. Added existing/new/reset-thread regression checks.
    Final core image `5c167462baf5` passed strict compilation and CTest 3/3
    (domain 20, process 5, sampling 9 checks).
12. Ran real mTLS RPC probe against deployed services twice. API/Agent identity
    isolation, stale cancellation/progress/result fencing, stale heartbeat lease
    rejection and current-attempt cancellation passed; scoped fixtures cleaned.
13. Proved real perf in a standalone controlled container: UID 10001 had zero
    effective capabilities despite cap-add, while UID 0 with only CAP_PERFMON
    and the original seccomp policy plus perf_event_open produced 43 real
    samples. No host sysctl, privileged mode or host PID namespace was used.
14. After explicit approval, deployed only Agent with compose.perf.yml and a
    dedicated nocopy root-owned 0700 volume. API task 8 completed actual 5-second
    perf collection and analysis: 189 samples, 22 nodes, benchmark/aggregate/
    transform functions present. Raw folded SHA256:
    200f6f0a193de435c344e90ee8fd78ef3dd338c102d978cd7aa1bbabc1b5f469.
15. Executed online PostgreSQL/S3 backup and isolated real database/object-store
    restore check: all 45 referenced artifact hashes and 45 object roundtrips
    passed; verification DB/bucket cleaned, production services stayed healthy.

## Full-scope collection work, 2026-10-16

The full-scope branch is not release-ready while the checks below are pending.
The production V1 release remains unchanged by these isolated checks.

- Added metadata-driven task kinds, independent Runner lifecycle management,
  explicit process/host/container policy, pinned PID/start-time/UID/namespace
  identity, tool executable and support-library hashes, typed binary RAW,
  auxiliary ELF, resource snapshots and bounded diagnostic scripts.
- Added real fixture collectors for independent Go, Java, Python and native
  toy PIDs, plus host and Docker inner-PID target validation. These fixtures
  are explicitly marked as imported toy evidence, never production workloads.
- Preserved configured multicall executable aliases such as `perf2bolt` while
  hashing their resolved files; a regression exercises actual `argv[0]` mode
  selection and subsequent executable hash drift.
- Added namespace-qualified BPF predicates and real user-stack output for
  syscall/VFS/block I/O and allocation/mmap/page-fault traces. Real kernel
  capture remains an acceptance gate; merely installing bpftrace is not proof.
- Added actual namespace network counters, main-thread/host context switches,
  collector CPU/RSS and interval overhead. Kernel-log policy/permission failure
  is explicit. The fixture checker now asserts these fields and their scope.
- The local protobuf V1 compatibility check and shared-contract check passed:
  preserved V1 field numbers/RPC signatures, negative control rejected,
  18 task kinds and four gRPC services validated.
- The fresh strict Linux rebuild found misleading-indentation errors in two
  newly changed statements; corrected them and applied clang-format to C++
  sources. The replacement build was still running when SSH stopped responding.
  Its final Werror/CTest outcome is not yet counted as passed.
- Remaining release gates: fresh nine-suite CTest, actual BOLT/BPF capture,
  mTLS/fencing regression, both N/N-1 directions collecting PROC_CPU through a
  private PUT sink with database size/SHA verification, and final image/source
  fingerprints. The isolated scripts do not restart production services.
- Static concurrency review additionally found direct `Collect` could race
  across Server replicas even though Heartbeat dispatch already locked its
  Agent row. Dispatch now takes that row lock through both entry points. The
  isolated mTLS probe starts two Server replicas and uses a private PostgreSQL
  trigger delay to exercise the competing `Collect` calls deterministically.
  That new regression is pending execution while SSH is unavailable.
- The same review found BPF must compare the innermost namespace PID rather
  than its host-visible PID when the selected target is in a container. Process
  identity now pins both values from procfs `NSpid`; trace predicates use the
  namespace PID and emitted RAW retains the requested visible PID. Added a
  synthetic two-level procfs regression, malformed namespace-PID rejection,
  trace-template assertions and actual container-fixture metadata assertions.
  Execution remains pending while SSH is unavailable.


## Full-scope audit follow-up, 2026-10-17

- Added `TaskDesc.priority=32` without changing field 31. Server dispatch uses
  persisted priority descending, then creation time and ID. A zero RPC priority
  also represents an N-1 client omitting the new field; a nonzero mismatch is
  rejected. The isolated RPC fixture covers this compatibility and scheduling.
- PERF_CPU V2 RAW is now actual `perf_script` with comm/PID/TID, nanosecond
  monotonic timestamps, event, IP, symbol and DSO. Protocol 1 retains folded RAW.
  The catalog also admits native `perf_data` offline imports. A bounded native
  record scan preserves sample and loss evidence; compressed, malformed and
  truncated data cannot establish zero loss. Synthetic scanner fixtures cover
  both byte orders and loss/truncation/compression boundaries.
- Added bounded fixed-field `runner.log` LOG artifacts (JSONL, text/plain,
  16,384-byte grant). Public diagnostics omit profiler stderr, argv, URLs and
  RAW. Server accepts only the current attempt and matching fixed LOG grant,
  including terminal delivery within 120 seconds; identical manifests replay
  idempotently, conflicts and stale attempts fail. Terminal DB grants retain
  authorization metadata but strip the PUT URL.
- HTTP upload retries now classify only network transport errors, 429 and 5xx
  as transient, use bounded exponential jitter and numeric Retry-After, and
  persist per-artifact SHA markers and retry timing. Completed RAW survives
  an upload interruption or terminal failure within a bounded disk/TTL policy;
  recovery runs on a worker while heartbeat leases continue. Collection is
  never restarted by journal recovery. Default retention is 24h/1GiB, with
  bounded configuration and admission reserves.
- Added an isolated actual PROC_CPU/PUT/PG recovery fixture for 503→429→200,
  permanent 403, process restart with original RAW SHA, and late LOG recovery.
  The fixture is not yet counted as passing until executed. It removes its
  secret-bearing private journals after copying bounded non-secret evidence.
- SSH recovered after the earlier resource incident. The prior pre-follow-up
  Linux build did finish Werror and CTest 9/9; that does not validate these new
  changes. A new single-process build is now running at 0.5 CPU/768MiB with
  8MiB/s reads and 4MiB/s writes, while all fullstage services remain stopped.
  Fresh CTest now has ten suites, including the upload classification/boundary
  suite. Production services are not modified by this work.
- ARM64 BOLT package pins were checked against the official Noble LLVM index.
  The fixed LLVM19 installer preserves perf2bolt/llvm-bolt aliases and records
  the actual package version. ARM64 runtime/build validation remains unproven
  because the local Docker/WSL engine was unavailable; package presence is not
  evidence of a working ARM64 image.


## Dedicated UID helper and configuration follow-up, 2026-10-18

- Added an independent bounded typed Unix-socket collection service and Agent
  client, bidirectional SO_PEERCRED checks, independent target/tool/budget policy,
  token cancellation, durable READY replay, fsynced RAW/ELF transport and ACKED
  tombstones. Agent RPC/credentials/uploads remain under UID 10001 with no caps.
- Review caught and corrected first-start socket-group ownership, omitted
  executable-path comparison in a path-free request, default 1GiB admission
  rejecting 256MiB profilers, active-slot leakage on a journal write fault,
  shared mount namespace assumptions, cooperative RAW permissions, and shared
  state/log FIFO hazards. The root cache remains 0700; only a fixed RAW filename
  uses the independently shared receive directory. Helper default storage is
  2GiB and defaults to rejecting an unbounded/different backing filesystem.
- Real strict Linux build r3 passed 12/12 CTest in 0.62s. Its corresponding IPC
  regression passed seven groups using actual Linux UIDs, Unix credentials,
  binary streams, SHA checks, cold READY replay with the target gone, repeat ACK,
  cancellation/busy fencing, persistence-fault admission release and peer/SHA/FIFO
  refusals. The profiler shim is explicitly imported deterministic test data and
  is not evidence of kernel/JVM/Python collection. Evidence is
  `core/evidence/strict-helper-r3.log` and `core/evidence/helper-ipc-r3.json`.
- Subsequent configuration/logging and small IPC timeout/TTL changes are not
  covered by that r3 proof. Added a standalone C++ configuration engine for
  CLI > environment > flat JSON > existing defaults, protected FILE references,
  sanitized public configuration fingerprints and a uniform 12-field LogContext.
  The new config suite is pending a strict Linux build and execution.
- Extended the actual PostgreSQL/mTLS/PUT six-case recovery runner to use real
  PERF_CPU through the dedicated helper, with delayed helper startup, database
  capability publication, helper outage downgrade and subsequent re-registration.
  Its Python syntax check passed; the actual helper-mode runs remain pending the
  final binary and dedicated bounded filesystem. Root owns deployment evidence.
- Java attachment under distinct UIDs requires separate real validation. The
  pinned async-profiler 4.1 jattach implementation switches euid to the target;
  root helper has no SETUID capability. This cannot be covered by historical
  root/root language fixtures. No capability is silently added to mask a failed
  attachment. Arbitrary mount/net setns also remains outside the chosen minimum
  privileges; PID mapping and proc identity checks do not establish namespace entry.
- Full strict Linux r4 passed14/14 CTest in0.76s after the configuration/logging
  and initial same-UID cooperative Agent integration. Its log is retained as
  `core/evidence/strict-helper-r4.log`; it does not cover the following review fixes.
- Review found that a misspelled Agent bounded-filesystem boolean could disable
  the capacity check, and native adapter intermediate profiles still used target
  `/tmp`. Boolean values now reject unknown spellings across CLI/ENV/JSON/FILE.
  Native schema2 keeps scratch in its owned0700 preconfigured control directory,
  verifies the numerical filesystem capacity on both ends, rejects legacy peers,
  and leaves no arbitrary scratch path in IPC. Agent safely prepares the direct
  receive-root native-control child; external targets must use that same spool.
  Added a real Unix-socket native protocol refusal suite and control-root regression;
  the final strict build now contains15 suites. These new results remain pending.
- Extended recovery fixture configuration for actual capless UID10001 independent
  Java/native target containers, exact ctimer provenance, allocation/lock/HPROF,
  same bounded RAW mount, and original-file restart with the toy target stopped.
  Python syntax checks passed; no real language/helper result is claimed yet.

- Strict Linux r5/r6/r7/r8/r9/r10 each passed all15 CTest suites under
  0.5CPU/768MiB with warnings as errors. Actual r7 helper-mode recovery passed
  all6 PostgreSQL/mTLS/PUT cases with real perf and a distinct UID10001 Agent,
  512MiB/no-swap helper, bounded physical filesystem, cold helper capability
  publication and outage/restart downgrade. Earlier imported IPC shim evidence
  remains separate. The final source freeze will identify each applicable run.
- Real narrow allocator globs on bpftrace0.20 selected exactly malloc/free and
  the minimal multi-uprobe diagnostic produced positive kernel counts without
  writable tracefs or new capabilities. The complete allocator Runner has not
  yet passed its final test; partial event output does not count as success.
- Actual GDB14 memray attachment across target UID10001 failed opening proc mem
  for writing despite SYS_PTRACE/DAC_READ_SEARCH. The official memray1.20 LLDB
  backend is being verified with LLVM16.0.6, pinned Ubuntu package versions
  1:16.0.6-23ubuntu4. Catalog tool requirements now select the trusted lldb alias;
  input format, analysis pipeline and protobuf are unchanged. r10 strict tests
  passed15/15, but its two512MiB captures failed and are not release evidence.

- Linux r11/r12 strict builds passed15/15. The real allocator capture timed out
  after its bounded grace with no helper OOM events; every trace emission now
  checks and increments the event budget, including frees. Partial RAW is never
  promoted when the profiler fails. Real LLDB captures also failed after the
  target proc entry disappeared; those tests used the older96MiB business toy
  and do not establish an OOM or abort cause. The next fixture records the actual
  target OOM flag/exit and uses192MiB for Python, with helper still512MiB/no-swap.
- To retain the existing GDB catalog without adding UID or DAC write privilege,
  added a standalone non-setuid C++ GDB launcher. It verifies manifest/binary/self
  bytes and uses launcher-only seccomp to reject O_RDWR open/openat and all
  openat2, with architecture/x32 checks. Official GDB linux-nat source selects
  its ptrace backend when its own proc-mem writable probe fails. An imported
  deterministic child tests real kernel filtering, parent isolation and invalid
  SHA/symlink/hardlink/FIFO refusals; it is not GDB collection evidence. This new
  16th CTest and actual GDB capture remain pending Linux execution.
- Replaced fixed300ms memray completion delay with bounded target-descriptor
  closure checks. Added a real held-writer/deadline/cancellation/FIFO regression.
  Advanced actual-capture fixture additionally fully reads new memray binaries
  with FileReader, checks SHA stability and verifies target CPU progresses after
  attach. These new source changes are pending the next strict build.

- Linux r14 Release/Werror passed all16 CTest suites in1.38s, including the
  launcher kernel-filter and cooperative writer-close regressions. The actual
  512MiB helper memray capture still failed in Python symbol detection; its
  separate UID10001/192MiB target remained running with no OOM. This is failed
  collection evidence, not a completed Python-memory feature. The actual cached
  GDB identifies as Ubuntu15.1 (the older GDB14 label above was inaccurate).
  Catalog bytes were restored exactly to the existing API rc3 SHA256
  c4a8757ea8628c2756df736221816ace667dbb92c7aed30d15571b08f13da243.
- The RelWithDebInfo experiment exceeded the isolated768MiB compiler limit;
  the strict recipe remains Release, matching the build that passed. A bounded
  optional read-only GDB toy diagnostic checks symbol readability and post-exec
  UID/capabilities while dropping stack frames, source text and pointer values.

- Actual r15 read-only diagnostic compared launcher, original GDB and Runner
  limits. All retained root UID and the expected four capabilities; each failed
  to load `target:/usr/bin/python3.12` with I/O error before any Python symbols
  existed. The separate business toy survived and was not OOM-killed. Official
  GDB15.1 fileio uses the target mount namespace for `target:` paths, so the next
  local change uses fixed proc-root/exe paths, disables target auto-load and
  revalidates the original numeric identity in the launcher. PID syntax,
  duplicates, identity mismatch and fixed path generation have new regressions;
  the pending source is not claimed to have passed Linux or real collection.

- Linux r16 Release/Werror passed all16 CTest suites in2.93s after the proc-root
  change and target/PID regressions. An actual read-only control with the older
  binary proved that fixed proc paths make all four PyMem symbols and target
  memory readable, while all default `target:` variants fail; mount namespaces
  differ and the target namespace itself is readable. Complete memray collection
  still timed out at32.28s, and the complete allocator trace still timed out
  at64.448s. Both512MiB helpers had no OOM events and both business toys remained
  running without OOM. Neither timeout is successful collection evidence.

- Prepared a narrow allocator PID-attach regression and change: official
  bpftrace0.20 `uprobe_multi` receives pid0 without `-p` (global inode attach),
  while the explicit process option confines attachment. This is not yet a
  passing fix or a stack-namespace remap. A diagnostic-only memray checkpoint
  records fixed script stages and signals only its verified UID0 debugger child;
  the business PID receives no signal. Python syntax checks pass, and actual
  execution awaits the next exclusive resource window.
  Source: https://raw.githubusercontent.com/bpftrace/bpftrace/v0.20.0/src/attached_probe.cpp

- Linux r17 first ran the new allocator target-scope assertion against the old
  implementation and failed as expected (CTest exit8). After adding `-p`, the
  full Release/Werror16-suite run passed in2.65s. Complete allocator collection
  nevertheless timed out at64.516s; helper OOM counters stayed zero and the
  separate UID10001 toy remained running. The minimal selected-PID multi-uprobe
  returned0 with positive hits. These outcomes do not prove complete capture.
- The r17 diagnostic-only GDB checkpoint found the debugger sleeping with11 CPU
  ticks and the toy ptrace-stopped after the library-access sentinel. TERM did
  not finish within3s; the fixture killed only its revalidated UID0 debugger.
  The toy then exited. No business PID was signalled, but this still fails the
  required target-preservation guarantee and is not cancellation success.
- Prepared two narrow follow-up controls without changing the production
  protocol or catalog. The first invokes the actual RunnerBuilder test probe
  and changes only its two `ustack(perf,16)` expressions to raw format. The
  second SHA-checks the installed upstream memray script/module, preserves all
  original GDB commands and adds fixed stage markers plus diagnostic-only
  direct-call-timeout5/unwind-on-timeout. Official GDB15.1 source contains both
  options; trusted `$sdir` library loading is independent of inferior auto-load
  settings, so no unsupported thread-library diagnosis is asserted. Six portable
  instrumentation tests and Python syntax checks pass; Linux execution remains
  pending the next exclusive resource window.

- Linux r18 Release/Werror passed all16 CTest suites in3.65s, plus six actual
  Linux instrumentation tests. The resource gate measured2,375,929,856 available
  memory bytes and5,850,656,768 free root-disk bytes. Both512MiB diagnostic helpers
  had zero OOM events. Private container cleanup left only the original eight
  production containers; post-run root-disk free space was5,857,406,976 bytes.
- The real RunnerBuilder perf/raw allocator comparison produced172 alloc and172
  free records with344 complete stack blocks in each variant. Both still needed
  the20s/TERM2s/KILL bound; helper CPU increased only about0.18/0.13 seconds.
  This rules out zero capture and does not support a perf-only symbol-resolution
  explanation. Identity survived, but the IO-bound toy's one-second CPU delta
  was zero, so target progress is not inferred from that weak measurement.
- Exact memray stages in r18 reached and returned from PendingCall, access,
  breakpoint handling, dlopen, dlerror and injector-symbol loading. It then
  reached SPAWN_CLIENT_BEGIN without its END. System libthread_db was active.
  The diagnostic-only5s direct-call timeout/unwind settings did not terminate
  the call; INT3s/KILL of the owned debugger again left the toy exited. This is
  failed safe-cancellation evidence. Official memray1.20 inject.cpp shows the
  synchronous call is pthread_create; network connection/recv/GIL run in the
  new thread afterward, so loopback failure is not asserted as this hang's cause.
- Prepared a diagnostic-only allocator stop gate after reviewing official
  bpftrace0.20 multi-uprobe link destruction and output-drain code. All three
  allocator predicates require an unset gate; both interval exits set it first,
  and END keeps it set. Five portable positive/negative transformation tests
  pass. New process checkpoints retain bounded thread state/wchan, syscall
  number, signal masks and IO counters only, plus an actual pre-attach network
  namespace comparison. These controls remain unexecuted on Linux; production
  Runner source and RAW schema are unchanged.
- Audited final RPC, N-1, recovery and IPC fixtures. Replaced stale-PID cleanup
  with random nonce/owner labels and owner checks before container/network
  removal. Added fresh RPC/N-1 evidence directories, bounded Docker/RAW reads
  and binary/catalog/image fingerprints where previously absent. Thirteen
  portable tests execute actual cleanup statements against explicit synthetic
  metadata, checking owned/foreign/missing-owner behavior and actual Bash
  syntax; all pass under native Git Bash. These tests neither start local Docker
  nor establish new RPC, compatibility, IPC or language collection results.
