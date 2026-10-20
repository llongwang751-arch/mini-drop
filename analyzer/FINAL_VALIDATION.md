# Analyzer serialized release validation

## Current 0.2.3 configuration and logging release (2026-10-18)

`evidence/release-v23-freeze.json` binds26 reviewed and installed Python files
to the immutable image
`sha256:55ae36b4e68dec8700ea722d30fc94e8eda665a756c038587f59279597e228ed`.
Both `drop-analyzer:v23-20261018-config` and the newly created
`drop-analyzer:20261018.1-rc2` name this same image. The runtime tree SHA256 is
`086561892d7fb844617c055e990ab0a1c2a5f5cace747a0a93f15e41239f583e`.

The actual serialized Linux gate passed101/101 tests, zero skips,59.168 seconds
with real private PostgreSQL17.11 and MinIO. It covers the prior92 lifecycle,
parser, resource and recovery regressions plus9 native configuration/logging
tests. Windows preflight separately ran101 tests with76 passes and25 explicit
environment skips; it is not substituted for the Linux result. The snapshot
log test first exposed omitted `S3_*` keys, and its red log is retained before
the corrected full run.

After the complete suite, the exact original Core two-thread toy captures were
reparsed: perf-script56920B/91 samples and native perf.data16032B/93 samples.
The verifier checked per-record manifests, PID/TID/comm, nanosecond times,
thread-root counts, native loss scan and output hashes. Its entire container
had512MiB memory, no additional swap, network none, exit0 and OOM=false. It
does not run uploaded application ELF. The inherited worker healthcheck is
irrelevant to this short-lived command override; the verifier assertions and
exit status determine its result.

The source-only runtime overlay retains the validated perf6.12.95 dependency
image; `evidence/Dockerfile.v23-runtime`, installed tool inventory, recipe/source
hashes and actual reports are frozen. Parser, pipeline and resource-budget
implementation did not change, so the prior0.2.2 whole-container10k/50k/100k
baseline below was not rerun or relabelled as a0.2.3 measurement. All disposable
containers were removed. Production's8 containers, volumes and older named
images were preserved. Stage/production rollout and historical-object migration
are separate steps coordinated by the root deployment task.

## Historical 0.2.2 baseline and reproduction

Prepared locally on 2026-10-17 and actually completed on 2026-10-18 after the
Core queue handoff. The immutable final candidate is recorded in
`evidence/release-v22-freeze.json`: 92/92 real PostgreSQL/S3 tests, zero skips;
actual Core perf-script/native perf.data cross-parse; and whole-container
512 MiB/no-swap 10k/50k/100k plus 100,001-node rejection all passed. The complete
private MinIO three-object encryption/key-recovery fixture also passed;
`ops/evidence/encryption-minio-v3.json` records that separate result.

The commands below document reproduction, not additional pending test work.
Production services, volumes and named release images were preserved. Production
deployment and historical-object encryption migration remain separate actions.

The first resumed local preflight on 2026-10-18 rechecked the then-unchanged
25-file runtime fingerprint, 91 test discoveries and all 20 collected fixture
sizes/hashes. A later actual urllib3 gzip regression reproduced transparent
HTTP decoding in RAW download. The fix now explicitly downloads exact stored
bytes; the current fingerprint below and 92-test target supersede that first
preflight. `evidence/integration-v22-encoded-offline.json` records the new local
54-test run (49 passed, five POSIX skips, no failures).
`evidence/preflight-v22-2026-10-18.json` records this separately from runtime
passes. The integration shell runner now rejects existing container/network
names before installing cleanup, uses random default names and ownership labels,
and removes only matching owned resources. Four fake-Docker process regressions
passed, including foreign ownership, collision refusal and failure-code
preservation. The runner uses an internal network, no container swap and a
read-only, capability-free test process; these runner changes do not change the
Analyzer runtime or the Linux test target.

## Reviewed source and local preflight

The current runtime has 25 Python files with canonical source-tree SHA256:

`36f726fdf62ee5262160ed8ea95fe8b959f61ba0e5274e02ddc44e60b92db8d9`

Local `unittest` discovery actually found **92 unique tests in 12 modules**:

| Module | Count |
| --- | ---: |
| test_disk | 10 |
| test_folded | 10 |
| test_hprof_native | 6 |
| test_isolation_registry | 9 |
| test_perf | 8 |
| test_pprof_events | 7 |
| test_progress | 3 |
| test_real_profiles | 9 |
| test_repository_integration | 16 |
| test_result | 4 |
| test_telemetry | 2 |
| test_worker | 8 |

The local discovery count is not a new 92-test pass. The current local execution
report remains 54 selected tests: 49 passed, five Windows/POSIX skips. The Linux
target is 92 passed, zero skipped. Do not suppress missing tools or fixtures to
obtain a green run.

The final source bundle contains only `analyzer/` source, tests, scripts and
pinned requirements, the authoritative `db/migrations/` and
`contracts/task-kinds.json`, plus the explicitly identified Core toy fixtures.
Exclude `.local`, `.claude`, deployment secrets, logs unrelated to this run and
all production profiles. Transfer into a new validation directory. Source
directories must be readable by UID 10001 (0755 directories / 0644 code); only
the new evidence directory is writable by that UID.

Use `/workspace/analyzer` as container working directory. Otherwise Python may
import `/app/drop_analyzer` from an older image before the reviewed bind mount.
`isolate.py` independently pins its child PYTHONPATH to its own reviewed module
directory and runs the child in its private job directory. The final image must
also pass `scripts/verify_release.py`, proving its installed `/app` runtime
matches the same 25-file tree.

## 1. Runtime image and source identity

The first 0.2.2 candidate `sha256:14dd14e396ad761e69576301504d12facdd5d73db4152e9840382efc78b51447`
actually passed all 92 PostgreSQL/S3 tests, zero skips, in 57.822 seconds.
The following actual Core perf.data cross-parse exposed a dependency gap:
Debian perf 6.1.190 rejected the Collector's newer `perf_event_attr` ABI with
`file uses a more recent and unsupported ABI (8 bytes extra)`. Both the normal
and 512 MiB address-space-limited direct decoder reproduced it. Preserve that
failure; a green unit run does not substitute for this native-tool check.

The Dockerfile now pins `linux-perf=6.12.95-1~bpo12+1` from the authenticated
Debian Bookworm Backports index. The official package supports amd64 and arm64
and the existing Bookworm libc. This changes the image dependency, not the
25-file Python runtime fingerprint. The new candidate is
`sha256:326427a3996a4b2b383e4ee25d9218e6cb728bb72a49066e5836d0672ff01e31`;
that image subsequently passed all 92 tests in 58.459 seconds, zero skips, and
both native-capture gates. Its installed 25-file source matched the reviewed
source exactly before the final freeze.

Package provenance: <https://packages.debian.org/bookworm-backports/linux-perf>.

Choose a new candidate tag, verify it is unused, and retain all previous named
images. Build only this image while Core has no running build. The cached
0.2.1 candidate may be the base to avoid another large JDK/BOLT base transfer;
the reviewed Dockerfile adds `linux-perf` and copies the new runtime. The
requirements lock remains hash-checked. A legacy build can be bounded with
`DOCKER_BUILDKIT=0`, `--memory=768m`, `--memory-swap=768m`,
`--cpu-period=100000` and `--cpu-quota=100000`. The local Docker CLI help actually
confirmed these legacy flags; no local Docker build or daemon startup was run.

Record the immutable image ID, architecture, source-tree SHA and actual versions
of Python, perf, JFR, BOLT, memray and the runtime dependency check. The amd64
image must have JFR, `llvm-bolt-16` or `llvm-bolt`, `perf` and OTEL SDK importable.
This remote validation is x86_64 evidence; ARM64 execution/build remains a
separate supply-chain check and must not be inferred from it.

```sh
# IMAGE is the new reviewed candidate; WORKSPACE is this run's new snapshot.
docker run --rm --network none --memory 128m --memory-swap 128m --cpus .25 \
  --cap-drop ALL --read-only --security-opt no-new-privileges \
  --mount "type=bind,src=$WORKSPACE,dst=/workspace,readonly" \
  --entrypoint python "$IMAGE" /workspace/analyzer/scripts/verify_release.py
```

If the image requires a package build, run it first and alone. If the full image
is deferred, earlier parser evidence may use a reviewed source bind over the
old frozen image, but this is not final image validation and native perf.data
cannot pass without the actual trusted perf tool.

## 2. All 92 tests with real PostgreSQL and S3

The existing `scripts/run-isolated-integration.sh` starts a disposable PostgreSQL
17.11 at 384 MiB / 0.5 CPU, a disposable MinIO at 256 MiB / 0.5 CPU and one test
runner at 768 MiB / one CPU. Its PostgreSQL/S3 data live only in fresh tmpfs; no
application database or production volume is mounted. Use a fresh random
`ANALYZER_TEST_PREFIX` and first ensure its exact names do not already exist.
The runner uses the reviewed source bind and `/workspace/analyzer` cwd. Keep
the new image's default non-root UID 10001.

```sh
ANALYZER_TEST_IMAGE="$IMAGE" ANALYZER_TEST_PREFIX="$UNIQUE_TEST_PREFIX" \
  sh analyzer/scripts/run-isolated-integration.sh > "$TEST_LOG" 2>&1
python analyzer/scripts/unittest_report.py "$TEST_LOG" "$TEST_REPORT" --image "$IMAGE_ID"
```

The report must contain `tests_run=92`, `passed=92`, `skipped=0`, `failed=0` and
`success=true`, with the actual log SHA. Confirm the test resources have been
removed before the next phase. This stage exercises real PG/S3 lifecycle and
worker SIGKILL recovery, POSIX symlink/FIFO checks, process-group/disk budgets,
HPROF checkpoint recovery and events, real JFR/memray/BOLT fixtures and OTLP.
Verify `tests/fixtures/collected/manifest.json` and all its fixture hashes before
running. A failure is preserved and fixed, followed by the appropriate full
rerun; old 63/70-test reports cannot satisfy this gate.

## 3. Actual Core perf-script and native perf.data cross-parse

Core has identified two **independent** kernel captures of its own two-thread
toy, with real `fixture-alpha` and `fixture-beta` thread names. The current
upstream location is:

`/opt/drop-build/core-v2/core/evidence/perf-collected/{perf_script,perf_data}`

Copy only each manifest and its explicitly named local RAW to a read-only
`runner-perf/` directory. The known RAW sizes reported by Core are 56,920 bytes
for script and 16,032 bytes for native data; validate the current manifests,
hashes and sizes rather than assuming these stay unchanged if Core recollects.
Both manifests require a positive native `metadata.perf.sample_count` and
explicit target PID, available at `metadata.target_pid` or
`metadata.perf.filters.target_pid`. Core's native fixture currently has 93
PERF_RECORD_SAMPLE records, PID 11 and a CLOCK_MONOTONIC provenance field.

`scripts/verify_perf_runner.py` checks each capture against its own manifest,
performs an actual isolated analysis and four-artifact encoding, checks sample
totals against native counts, real PID/TID/comm groups and `root_name`, decimal
nanosecond timestamps, per-thread weights and DSO/address labels. It never
requires equal sample totals across the two independent records.

The script import deliberately does not trust an imported JSON manifest to
claim zero loss. Native data is independently scanned for sample/loss records,
and the installed trusted system perf executable converts it with fixed argv,
an empty private symfs and disabled debug-info fetch. The validation container
has no network; the verifier checks that its only interface is loopback. An
unresolved symbol remains an address. No uploaded ELF is run.

```sh
docker run --rm --network none --memory 512m --memory-swap 512m --cpus 1 \
  --pids-limit 64 --cap-drop ALL --read-only --security-opt no-new-privileges \
  --tmpfs /tmp:rw,size=256m,mode=1777 \
  --mount "type=bind,src=$WORKSPACE,dst=/workspace,readonly" \
  --mount "type=bind,src=$EVIDENCE,dst=/evidence" \
  --workdir /workspace/analyzer --entrypoint python "$IMAGE" \
  scripts/verify_perf_runner.py /workspace/runner-perf \
  --expected-source 36f726fdf62ee5262160ed8ea95fe8b959f61ba0e5274e02ddc44e60b92db8d9 \
  --output /evidence/perf-runner-v22.json
```

Core's latest BPF/BOLT/RESOURCE fixtures may then be copied by explicit manifest
names into a separate directory and read-only cross-parsed by the existing
generic Runner verifier. That older helper's historical 13/13 report does not
prove the new perf/thread or new Core resource fields.

## 4. Whole-container 512 MiB 10k / 50k / 100k baseline

Run this only after PostgreSQL/S3 from phase 2 are removed. Bound **parent and
child together** to 512 MiB, one CPU and no swap; the child also has a 512 MiB
address-space limit, 90 CPU seconds, 120 wall seconds, 32 MiB file/output cap,
192 MiB total job-disk budget and 16 MiB filesystem reserve. The tmpfs is 256 MiB.
This is imported synthetic star topology, not a measured business capture or a
deep-tree worst-case guarantee.

```sh
docker run --rm --network none --memory 512m --memory-swap 512m --cpus 1 \
  --pids-limit 64 --cap-drop ALL --read-only --security-opt no-new-privileges \
  --tmpfs /tmp:rw,size=256m,mode=1777 \
  --mount "type=bind,src=$WORKSPACE,dst=/workspace,readonly" \
  --workdir /workspace/analyzer --entrypoint python "$IMAGE" \
  scripts/benchmark_flamegraph.py > "$BENCHMARK_REPORT"
```

Require all 10,000 / 50,000 / 100,000 node imports (including virtual root) to
produce the public result and four artifact size/hash records, and require
100,001 to fail with `INPUT_LIMIT_EXCEEDED`. Capture the image ID, source SHA,
cgroup `memory.max=536870912`, `memory.swap.max=0`, `memory.peak`, `memory.events`
and `cpu.max`, actual elapsed time and parent/child RSS. Preserve a nonzero
exit/OOM report and never recast it as an accepted boundary.

## 5. Final image freeze and complete SSE fixture

After all Analyzer gates pass, repeat the installed-versus-reviewed source
comparison only if image/source changed, and save the immutable image ID,
25-file source tree, pinned requirements, Dockerfile/script hashes and the
actual reports as a single release record. Tag a new final release without
deleting or repointing old named releases. Deployment remains root's action.

Last, execute the newer private SSE fixture using its three-file snapshot:
`deploy/tests/test_s3_encryption.sh`, `deploy/tests/s3_encryption_probe.py` and
`ops/encrypt_existing.py`. The current probe includes the exact release's
`kms:InvalidCiphertextException`/400 and `kms:DecryptionFailed`/500 mappings and
requires a specific decryption reason. It reports safe code/status/reason-match
for **each** rejected object. Generic 500, auth errors, missing objects, failed
health/canary or network failures remain failures. The full result must prove
plaintext legacy migration, automatic AES256 writes, three old-object refusals
with a functioning new-key canary, and original-key recovery, followed by zero
labeled fixture resources. Preserve the earlier failed run and its old source
SHA separately. See `ops/ENCRYPTION_MIGRATION.md`; no production migration or
encryption-complete claim follows from this private fixture alone.
