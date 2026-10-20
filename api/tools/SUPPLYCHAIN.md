# Offline release verification

`supplychain.py` inventories exact Docker image archives using Syft, then signs
the six release image IDs and their CycloneDX SBOM hashes with a project-local
Ed25519 key. This is an offline verification bundle. It does not assert a
registry signature, GitHub Actions execution, Rekor entry, or native ARM test.

An optional independently built nginx platform image can accompany the six
applications. `sign --platform-records PATH_TO_NGINX_RECORD` accepts exactly one
record, and only the component `drop-nginx` with a real calendar date in the
strict release syntax `YYYYMMDD.n[-rcN]` (positive release/candidate numbers).
For this release the planned reference is `drop-nginx:20261018.1-rc1`; use its
actual frozen image ID and fresh adjacent SBOM record after the image exists.
This documentation does not claim that platform image has been built or signed.

The six application records remain mandatory and distinct: Core, Agent, API,
Analyzer, Web and Ops. A platform cannot replace an application slot, add a
seventh application, duplicate a platform entry or use another component's name.
Both application and platform records require an exact `sha256:` image ID,
64-lowercase-hex SBOM digest and a safe adjacent JSON basename. Missing, changed
or symlinked SBOMs are refused. Record files are bounded to64KiB.

The signed manifest stores applications in `images` and the optional platform
in `platform_images`. Verification authenticates both arrays, checks the same
module/release boundaries and rehashes all adjacent SBOM bytes. Existing valid
six-application manifests without `platform_images` remain supported as having
no platform images. Verification reports the application and platform counts
separately. Image IDs and inventory hashes are bound by the offline signature;
this tool does not query a registry to establish deployment or registry trust.

The checked tool version and official HTTPS checksum evidence are stored in
`docs/evidence/supplychain/tool-and-signature-validation.json`. The signing
public key is `docs/evidence/supplychain/release-signing.pub`; the private key
and its encryption password are only in the ignored `.local/supplychain/private`
directory, with inheritance removed and access limited to the current Windows
identity and SYSTEM. Never add that directory to an archive or repository.

Run the following steps once all six release images have frozen. Export and
catalog **one image at a time** to keep resource usage bounded. The same example
must be repeated for `drop-core`, `drop-agent`, `drop-analyzer`, `drop-web` and
`drop-ops`, using their final tags. An image change invalidates its old record.

```powershell
python api/tools/supplychain.py export --host root@120.24.187.205 --image drop-api:full-ready --output .local/supplychain/drop-api-amd64.tar
python api/tools/supplychain.py sbom --syft .local/supplychain/syft.exe --archive .local/supplychain/drop-api-amd64.tar --output docs/evidence/supplychain/drop-api.cdx.json
```

Syft uses one Go execution thread with a 384 MiB soft memory target. Image
environment values, command strings, and labels are deliberately absent from
export reports. Image archives stay outside version control. Each `.record.json`
binds the image ID, archive hash, SBOM hash and component count.

Resource budget for the final bundle:

| Step          | Budget and prerequisites                                                                                                                                                                                                     |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Export        | Existing frozen images and authenticated SSH; streams directly to the development drive without a second image archive on the server. Run one export at a time.                                                              |
| Catalog       | Syft 1.54.1, one Go execution thread, 384 MiB soft Go memory target. Allow at least 512 MiB free process headroom; it is not a hard RSS limit. Keep several GiB free for the largest image archive and temporary extraction. |
| Sign / verify | Python with cryptography; six application SBOMs/records and optionally one nginx platform SBOM/record; restricted local key directory. No registry account or outbound signing service is used.                              |

The stored API SBOM is historical and binds the earlier image
`sha256:6b5fe5d900c10cdd86de6c64f6cd4357caf757c0f962f87eb46020e5372b7ecc`.
Priority dispatch, attempt-state fixes and terminal LOG grants changed the Go
runtime after that snapshot. Rebuild the Linux binary from the final source,
record its new SHA-256, and build a complete runtime image containing all
authoritative migrations through 005 and the final task catalog. A migration-only
COPY overlay cannot include these runtime changes. Replace the archive and rerun
Syft against that actual image ID. The earlier binary SHA-256
`cd14af879c8cf8556dffce2b773d64a7a8a1a886593c5747469c41fc9319ba6d`
is retained only as historical evidence. The new release image is not yet built
or deployed.

After freezing the source commit and creating the release source archive:

```powershell
python api/tools/supplychain.py sign --private-directory .local/supplychain/private --release RELEASE_TAG --source-commit EXACT_COMMIT_SHA --source-archive PATH_TO_RELEASE_SOURCE_ARCHIVE --records docs/evidence/supplychain/drop-core.cdx.record.json docs/evidence/supplychain/drop-agent.cdx.record.json docs/evidence/supplychain/drop-api.cdx.record.json docs/evidence/supplychain/drop-analyzer.cdx.record.json docs/evidence/supplychain/drop-web.cdx.record.json docs/evidence/supplychain/drop-ops.cdx.record.json --output docs/evidence/supplychain/release-manifest.json
python api/tools/supplychain.py verify --manifest docs/evidence/supplychain/release-manifest.json --signature docs/evidence/supplychain/release-manifest.sig --public-key docs/evidence/supplychain/release-signing.pub --expected-key-sha256 fe2b4d7123f33ed95a3f48533459b2022362bcc7c9de83825cee9101eb487d5f
```

The expected public-key fingerprint must be pinned independently when the
bundle is moved to another machine. Merely trusting a replacement key shipped
with a replacement manifest defeats verification.

Append `--platform-records docs/evidence/supplychain/drop-nginx.cdx.record.json`
to the `sign` command when the independently frozen platform image is included.
Do not add that record to the six `--records` arguments. `verify` reads the
signed platform array automatically and requires its adjacent SBOM.

The15 offline regressions retain the original eight signature/path/ACL tests
and add real Ed25519 signing with and without the optional platform, manifest
and platform-SBOM tamper rejection, strict platform calendar/version checks,
unknown/duplicate/module-impersonation rejection and six-application enforcement.
All keys and inventories in these tests are disposable, explicitly imported
fixtures. Tests do not read the deployment signing key or sign a final release.

## Disposable performance baseline

`tools/cmd/platform-benchmark` refuses any database name except
`drop_perf_fixture`, and refuses an existing task table. It runs the actual HTTP
router against a fresh PostgreSQL fixture: one million explicitly synthetic
tasks, 1,000 owners, 100 groups, shares, soft deletion, real EXPLAIN ANALYZE with
buffers, first/deep-page HTTP requests, bounded concurrent creates, and 20 live
SSE connections receiving a committed database event. The real minimal index migration is applied twice only in the fresh fixture
for its replay/data-preservation checks; production migration is a separate
deployment step.

Compile this static executable for the runner's Linux architecture, place it at
the fixture root beside `db/migrations` and `contracts/task-kinds.json`, then run
`run_platform_benchmark.py --fixtures FIXTURE_ROOT --output REPORT_PATH`.

The runner creates its own random container and volume with `network=none`,
256 MiB memory, 384 MiB memory plus swap, 0.5 CPU, and 96 PIDs. On Linux it
refuses elevated host I/O pressure, identifies the fixture block device, and
caps reads to 4 MiB/s and writes to 2 MiB/s. It deletes only its own resources.
Run this **after** collection/analyzer validation, never concurrently with
resource-intensive work on the small production host. The fixture combines
the API process and PostgreSQL under one resource limit; timing describes that
explicit environment, not production capacity or real profiler throughput.

Partial report checkpoints are written after seeding and each query so a
stopped fixture can still yield the work actually completed. No successful
million-row performance run is asserted until a report exists with
`passed: true`.

For this development machine Docker Desktop repeatedly lost its engine pipe,
so the completed run uses the fallback `run_windows_benchmark.py`. It runs
official EDB portable PostgreSQL 17.11 binaries and the same Go harness directly
on Windows, using a fresh cluster and random loopback port. It registers no
Windows service and puts PostgreSQL and the API in one private Windows Job
Object with a 512 MiB committed-memory limit and one logical CPU. The runner
stops its own processes and verifies absolute containment below `.local` before
removing its cluster. Raw reports and local logs remain outside that cluster.

The actual completed report is
`api/benchmarks/million-task-minimal-index-windows-2026-10-16.json`. It includes the full
PostgreSQL plans, actual HTTP status counts, and committed-event SSE latency.
This is Windows evidence; it does not qualify Linux or native ARM runtime
performance. The Linux attempt produced no successful performance report. Its pending SSH
client was cancelled during server I/O contention; the last available check
showed no active container with this benchmark's private name prefix. No
completed Linux performance run is asserted.
