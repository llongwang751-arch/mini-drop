# Storage maintenance and migration

`maintenance.py` owns bounded expiry, missing/hash reconciliation, orphan and
multipart cleanup, metrics, and the authenticated internal alert audit receiver.
`reconcile.py` holds the storage-independent state policy. The PostgreSQL
advisory lock serializes writers with `encrypt_existing.py`; the encryption
migrator additionally requires quiescent application writers and verifies exact
stored bytes and metadata before and after each conditional copy.

Install the reviewed Linux dependency closure with
`pip install --require-hashes -r ops/requirements.lock`. The three direct
requirements are listed separately in `requirements.txt`. Hash-checked wheels
and active Linux dependency markers have been verified for CPython3.12 on amd64
and arm64; this is not evidence of native ARM execution. Public `Dockerfile`
and `Dockerfile.runtime` both build from the repository using official PyPI and
`--require-hashes`; neither needs an untracked wheels directory. Validated
release archives use a narrower offline wheel lock with identical package
versions and matching artifact hashes. Python3.12.15/Trixie uses pure
`psycopg==3.3.6` with system `libpq5` and `PSYCOPG_IMPL=python` rather than a
bundled binary/libssl. See [build inputs and evidence boundaries](../analyzer/BUILD_INPUTS.md).

`OPS_TEST_IMAGE=<candidate> bash ops/run-isolated-integration.sh` creates only
fresh labelled PostgreSQL/S3/runner resources on an internal network, with no
published ports, host business data or container swap. It refuses collisions
before installing cleanup and only removes its own labelled resources. Its
explicit suite includes policy tests and an actual isolated PostgreSQL/S3
round-trip; fake storage tests are not relabelled as production verification.
The separate encryption PostgreSQL and actual legacy/AES/wrong-key fixture
evidence is described in [ENCRYPTION_MIGRATION.md](ENCRYPTION_MIGRATION.md).


## Native configuration and bounded logs (0.2.3)

`python maintenance.py --config /path/config.json --set MAINTENANCE_INTERVAL_SECONDS=300`
and `python encrypt_existing.py --config /path/config.json` share the standalone
`ops_runtime.py` resolver. CLI wins over environment, then JSON, then validated
defaults; `OPS_CONFIG_FILE` is the shared default JSON path. Secrets remain
`DATABASE_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, and (maintenance only)
`ALERT_WEBHOOK_TOKEN`, supplied by environment or known `_FILE` references.
Raw secrets are rejected in JSON and CLI. Unknown/duplicate keys, files over
64KiB, control characters and invalid bounded values are rejected without
printing their values. Environment `_FILE` wins over environment raw values;
environment wins over JSON `_FILE` references.

Both commands allow non-secret `S3_ENDPOINT` and `S3_BUCKET`. Maintenance also
allows `MAINTENANCE_INTERVAL_SECONDS`, `MAINTENANCE_METRICS_HOST`,
`MAINTENANCE_METRICS_PORT`, and `MONITORING_STATUS_FILE`. Encryption allows
`ENCRYPTION_MAX_OBJECTS`, `ENCRYPTION_MAX_BYTES`, `ENCRYPTION_MAX_OBJECT_BYTES`,
and `ENCRYPTION_PAGE_SIZE`; the existing `--max-objects`, `--max-bytes`,
`--max-object-bytes` and `--page-size` flags retain their explicit CLI priority.
Apply can only be enabled by the existing `--apply --confirm-writers-stopped`
flags, and the database/stopped-writer fences remain unchanged.

Maintenance binds its configured metrics port and reads its resolved interval,
S3/database settings, alert token and monitoring-status path. The silent health
entry is `python ops_runtime.py --health [--config ...] [--set ...]`; it uses the
same listener settings and a bounded HTTP health read without environment
proxies. If the daemon uses CLI-only listener overrides, mirror them in its
health command or move them to the shared file/environment. The default health
listener remains9105. The main Dockerfile uses this health entry; runtime release
contexts must copy `ops_runtime.py` and use the same health command.

Configuration summaries and operational logs go to stderr as bounded JSON with
`time/level/service/version/request_id/task_id/attempt_id/agent_id/stage/duration_ms/error_code/message`.
Unrelated IDs are empty. Secret values/source paths are excluded from the hash;
validated non-secret endpoints affect the hash but are redacted in the visible
summary. Exception messages, response bodies, profile bytes and signed URLs do
not enter log records. The encryption tool preserves its single JSON report on
stdout, independently of its log stream. These configuration/CLI regressions are
local evidence; renewed Linux PostgreSQL/S3 integration is a separate gate.
