# Historical object encryption migration

`encrypt_existing.py` uses authenticated S3 operations against the backend and
does not read the backend filesystem. It defaults to dry-run. The existing API,
Agent and browser continue to use the storage gateway; the gateway does not gain
copy permissions for this operation.

The target is **SSE-S3 (`AES256`)**, backed by the retained MinIO master-key
secret. The exact MinIO release used by this project chooses `aws:kms` for
automatic encryption when a bucket has no encryption rule. Therefore storage
initialization and the private fixture explicitly configure and read back an
`AES256` bucket default. `aws:kms` is reported as a distinct algorithm and does
not satisfy this tool's SSE-S3 completion condition.

Source: [MinIO RELEASE.2025-07-23T15-54-02Z encryption rule application](https://github.com/minio/minio/blob/RELEASE.2025-07-23T15-54-02Z/internal/bucket/encryption/bucket-sse-config.go#L129).

## Scope and evidence

The tool enumerates **every current object** in the target bucket, including
objects whose database status is expired or quarantined, orphan objects, and
zero-byte directory markers. It does not load the bucket or artifact catalogue
into memory. Listing uses bounded signed ListObjectsV2 pages; object reads use
64 KiB chunks. Enabled or suspended bucket versioning is refused because a
same-key copy would leave older plaintext versions behind.

For a database-referenced object, a full plaintext GET must match the database
size and SHA256 before any write. For an unreferenced object, the first full GET
establishes a baseline size and SHA256. `baseline_only` counts those objects:
this proves that migration preserved their bytes, not their collection origin.
Objects already reporting `AES256` are still downloaded and verified on every
run. Database objects absent from S3 are counted separately as `database_missing`.

A plaintext or SSE-KMS object is copied to its own key using the verified source
ETag condition and `MetadataDirective.REPLACE`. Content-Type and all user
metadata are retained. Encryption comes from the bucket default, without client
SSE headers. A fresh GET must then match the source plaintext size/SHA256,
copied ETag, metadata and `AES256` response header before `encrypted` increases.
A network error, generic server failure, missing object, changed ETag, changed
content or missing encryption evidence prevents completion.

Object GETs explicitly disable urllib3 HTTP content decoding. A gzip object's
authoritative size/SHA cover its compressed stored bytes. Same-key replacement
preserves `Content-Encoding`, `Content-Language`, `Content-Disposition` and
`Cache-Control` separately from identically named user metadata. An `Expires`
system field is refused before mutation with `UNSUPPORTED_SYSTEM_METADATA`:
the pinned SDK would incorrectly normalize it into user metadata, and the
application gateway does not accept that field. This refusal is incomplete,
never a claim that metadata was preserved.

`scan_complete` means the bounded object and database scans ended normally.
`bucket_encryption_verified` is true only after a successful **apply** scan of
the whole bucket. `artifact_integrity_complete` additionally requires no missing
database objects. `complete` requires both; dry-run always reports false for
`complete`. The JSON output contains aggregate counts and fixed error codes,
never object names, plaintext, credentials or SDK exception messages.

## Operational sequence

Use a reviewed Ops image containing this source. Preserve the data backup and
the original master-key secret. The S3 GET backup exports plaintext: protect its
directory with mode 0700 and files with mode 0600, and use a protected backup
destination. This export must not contain credentials or the master-key secret.
Keep the master key separately in protected storage on another machine; a
second directory on the production server does not provide offsite protection.
Losing the master key makes existing ciphertext unreadable, including an
encrypted volume backup, so a local data/key directory copy alone is inadequate.
Stop the API, collection server, Agents,
Analyzers and periodic Ops reconciler before applying. Ensure queued/running/
retry collection and analysis work is completed or explicitly canceled through
the existing task lifecycle; merely stopping a running worker does not make
its database state terminal.

From the deployment directory, export the **complete existing deployment**
`COMPOSE_FILE` list, keeping its project name and release environment. The tool
overlay in this repository is `compose.tools.yml`; the production stack may
also include perf, eBPF, observability and HTTPS overlays. Append the one-shot
maintenance overlay to the deployed file list, preserving all existing merges:

```sh
: "${COMPOSE_FILE:?Export the complete current deployment Compose file list first}"
export COMPOSE_FILE="${COMPOSE_FILE}:compose.encryption-maintenance.yml"
docker compose --profile maintenance run --rm --no-deps encrypt-existing
docker compose --profile maintenance run --rm --no-deps encrypt-existing --apply --confirm-writers-stopped
```

The maintenance service alone joins both the control and storage networks. Its
S3 endpoint is `storage-backend:9000`. It uses the protected migration database
secret, runs as UID 10001 with 96 MiB / 0.25 CPU, no Linux capabilities, a read-only
root filesystem, and no published port. The apply path takes the same advisory
lock as the reconciler and SHARE locks on task/attempt/job/artifact tables,
checks for nonterminal collections or pending/running/retry analysis, checks
again before copying and at completion, and holds those locks until its
transaction ends. The explicit stopped-writer confirmation is also necessary:
a table lock cannot revoke a presigned PUT issued before maintenance.

Default limits are 10,000 bucket objects and database rows, 100 entries per page,
256 MiB per object and 2 GiB total streamed reads. The read budget includes the
post-copy verification. Override with `--max-objects`, `--page-size`,
`--max-object-bytes` and `--max-bytes` within the enforced upper bounds. A limit
failure reports incomplete. A rerun safely verifies already-encrypted objects;
increase the explicit scan/read budget when the previous budget cannot cover
the whole bucket. The tool does not silently claim success for only a prefix of
the bucket and does not delete or quarantine data.

## Actual verification and remaining evidence (2026-10-17)

The offline TDD started with the new migration module absent and the test import
failing. The implemented migration has **23 passing offline regressions**. The
existing reconciliation regressions add **4 passing tests**; the private SSE
probe now adds **11 passing tests**. There are no skips in these selected runs.

```sh
cd ops
python -m unittest test_reconcile test_encrypt_existing -v
cd ..
python -m unittest discover -s deploy/tests -p test_s3_encryption_probe.py -v
python -m ruff check ops/encrypt_existing.py ops/test_encrypt_existing.py deploy/tests/s3_encryption_probe.py deploy/tests/test_s3_encryption_probe.py --select E9,F
python -m ruff format --check ops/encrypt_existing.py ops/test_encrypt_existing.py deploy/tests/s3_encryption_probe.py deploy/tests/test_s3_encryption_probe.py
bash -n deploy/tests/test_s3_encryption.sh
```

The SDK header test executes the pinned MinIO SDK's actual copy implementation
with a mocked HTTP transport; the streaming and listing regressions also use
in-memory HTTP responses. These tests do **not** constitute a real MinIO test.
The previous private MinIO run failed to report `AES256` before the bucket rule
was added, and is not a pass.

The SQL half subsequently passed **8/8 actual PostgreSQL17.11 tests, zero skips**
in a new random-loopback Windows portable cluster using all five production
migrations. The PostgreSQL processes and test process shared a 512 MiB / one
logical CPU Windows Job; measured peak committed memory was 70,979,584 bytes,
test time 3.956 seconds and runner elapsed time 6.855 seconds. The cluster was
stopped and removed; no fixture cluster remained. S3 remained synthetic Fake
storage in this run. Evidence is `ops/evidence/encryption-postgres.json` and its
SHA256-linked `.log` (`2ac921734b037779fe1fe08e42ef3e688eb9abe96bc7823e3d1d5ca9249488cc`).

The real SQL cases verify `SET LOCAL` lifetime, four SHARE table locks, the named
server cursor and its scan limit, expired/quarantined evidence lookup, refusal
for each nonterminal collection and pending/running/retry analysis state,
mutual exclusion with the reconciler's advisory lock, blocking of subsequent
writes and release after transaction end, bounded three-second acquisition
failure when a writer already holds a table lock, and dry-run/apply/rerun
behavior with the real repository plus Fake S3.

```sh
# Windows, using the already cached official portable PostgreSQL17.11 binaries:
python ops/run_windows_encryption_tests.py
```

This runner reuses only the existing Windows Job class from the API benchmark
runner and does not modify API code. It starts no Windows service or Docker
daemon and never accesses the remote server. The migration source SHA256 in
the actual report is `a3c9b01f631c9afb1d2daef7811546bc264a43f5feff0b4822952c0ad79e93bc`.

The next remote test, only when the serialized validation queue is available,
requires the probe, shell script and migration module from the same reviewed
snapshot. It creates only its own disposable labeled volume/network/containers:

```sh
DROP_ENCRYPTION_TEST_IMAGE=drop-ops:full-ready bash deploy/tests/test_s3_encryption.sh
```

This fixture writes a 2 MiB plaintext object with automatic encryption disabled,
confirms plaintext HEAD/GET headers, restarts with automatic encryption enabled,
sets and reads back the bucket AES256 rule, performs the production same-key
migration primitive, and verifies both migrated and newly written objects. It
then restarts with different material under the same master-key identifier,
requires a working newly encrypted canary and specific S3 decryption errors for
both earlier objects while storage remains healthy, and finally restores the
original key and verifies both plaintext roundtrips. Until that full real test
and the production dry-run/apply reports pass, historical encryption is **pending**.

The first real run of this extended fixture took **16.340 seconds**. Legacy
plaintext GET/HEAD (`absent`), same-key migration GET/HEAD (`AES256`) and a new
automatically encrypted PUT/GET all succeeded with the same 2 MiB plaintext
SHA256 and size. The strict wrong-key whitelist then rejected the returned S3
error as unexpected; recovery was not executed. The overall result remains
**failed/incomplete**, retained in `ops/evidence/encryption-minio-v2-first.json`
and its linked log (SHA256
`7e0ce738f8002cc96be4f5590c6e0b80f4bbabfaac50e25f8257489ed63f6e9a`).
After the failure, actual Docker label queries found zero fixture containers,
networks and volumes. Production was not changed.

The exact release's [built-in KMS decryption](https://github.com/minio/minio/blob/RELEASE.2025-07-23T15-54-02Z/internal/kms/secret-key.go#L192)
returns a [specific ciphertext error](https://github.com/minio/minio/blob/RELEASE.2025-07-23T15-54-02Z/internal/kms/errors.go#L48),
which the [HTTP error adapter](https://github.com/minio/minio/blob/RELEASE.2025-07-23T15-54-02Z/cmd/api-errors.go#L2449)
passes through as a `kms:` API code. The initial probe omitted this prefix.
A new offline regression first reproduced the rejection and now passes after
adding only the specific decryption codes with their exact HTTP statuses.
Generic internal errors and network failures remain failures. This is a
source-backed explanation of the likely cause, not observation of the first
run's masked code. The probe also now emits only bounded code enums, status and
a boolean reason-match diagnostic for unexpected S3 errors. Its current SHA256
is `06b0a0deb08128b1ac7c0ee4cd1ec215c9bbc78cf0885b8f5f73d5c0edd14b93`;
the failed real-run report retains the earlier probe SHA256. A fresh real run
of this newer snapshot is still required.

## 2026-10-18 exact-byte and representation metadata regression

An actual urllib3 gzip response first reproduced transparent decompression in both Analyzer download and migration verification. Both now hash exact stored bytes. Migration now has 26 passing offline tests, and the SSE probe has 13; its real fixture adds a legacy gzip object and requires compressed size/SHA plus all four representation/cache fields to survive encryption and key recovery. These additional MinIO checks are still pending the unique remote queue.

The updated SQL half passed 8/8 real PostgreSQL17.11 tests, zero skips, in 3.955 seconds with a 70,242,304-byte peak Windows Job. The first rerun exposed a test race that closed a session advisory-lock holder and immediately checked reacquisition before backend disconnection finished. The fixture now holds the same transaction advisory lock as the actual reconciler and waits for COMMIT. The failed report/log are preserved as `encryption-postgres-encoded-before-lock-test-fix`; the current `encryption-postgres.json` reports the new migration source SHA. No application SQL behavior was weakened.


## 2026-10-18 complete three-object private SSE verification

`ops/evidence/encryption-minio-v3.json` and its SHA256-linked log now record a
complete real MinIO run, **20.141 seconds**, exit zero. A legacy 2 MiB object and
an independently compressed legacy object first had no encryption headers.
The production same-key conditional-copy primitive migrated both to AES256;
compressed size/SHA and Content-Encoding, Content-Language,
Content-Disposition and Cache-Control remained exact. A new automatic-encryption
2 MiB PUT also round-tripped with AES256. Different key material under the same
key identifier refused **all three** historical objects with the exact
`kms:InvalidCiphertextException`/400 decryption reason while a new-key canary
and storage readiness remained healthy. Restoring the original key recovered
all three plaintext byte sequences and the compressed object's metadata.
Actual label queries then found zero fixture containers, networks or volumes;
only the eight existing production containers remained running.

The earlier fixture only tested two historical refusals. A strengthened offline
assertion first failed `2 != 3` (`probe-three-before-fix.log`); the probe now also
requires the encoded object's refusal. All 13 probe regressions then passed,
and a fresh full real fixture passed with three refusals. The earlier two-object
run remains `encryption-minio-v3-two-refusals.log`, while the earlier strict-code
failure remains `encryption-minio-v2-first.json`.

`encryption-offline-v3.json` records the current 26 migration and 13 probe tests,
zero skips; `encryption-postgres.json` separately binds eight actual PostgreSQL
locking tests to the same migration module SHA. Ruff safety/format checks passed.
This completes the private validation gate, not production migration. Production
still requires stopped writers, dry-run/apply reports and separate protection of
the original master key before declaring historical encryption complete.
