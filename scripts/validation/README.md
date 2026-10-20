# Stage S3 `Content-Encoding:gzip` validation

These are independent acceptance tools. They do not change API, Core, Analyzer,
Web source, dependency locks or frozen release artifacts. Actual stage execution
belongs to the root task after its isolated services are ready.

`stage_ce_gzip.py` creates one explicitly imported two-line collapsed task using
the real stage Web API. It waits for actual Analyzer SUCCESS and selects the
latest collected attempt's ready `RESULT/flamegraph.json.gz`. It obtains a new
authorized download grant to identify that object's internal key. The object
scope is checked against this newly created task and attempt before S3 access.
It never accepts a caller-provided task ID, bucket, object key or service URL.

The fixed endpoints are API `http://web:8080`, public Origin
`http://127.0.0.1:18080`, and S3 admin `storage-backend:9000` / `drop-artifacts`. API login
always sends that stage Origin, which must match the stage API's PUBLIC_URL.
`--confirm-stage dropfullstage` and a fresh32-hex `--run-id` are required. A task
creation replay (HTTP200) is refused; the tool requires a new HTTP201 import.
It refuses external retries, another task/owner, another bucket/origin/path,
oversized artifacts, expired results or a missing signed identity response
override. This guard complements the root task's isolated network and Secret
mount selection; it does not replace them.

The one permitted mutation is a conditional same-object MinIO copy with
`MetadataDirective.REPLACE`. It preserves original compressed bytes, content
type, all user metadata and supported representation/cache metadata while adding
the real **system header** `Content-Encoding:gzip`. A user field named
`content-type` remains prefixed user metadata. `Expires` is explicitly refused
because the pinned SDK cannot safely preserve it as a system field. Both exact
RAW downloads use `decode_content=False`, compare API size/SHA256, check gzip
magic and validate the imported tiny flamegraph. Stored size/SHA, user/system
metadata and encryption metadata must remain unchanged after copying. No
production or other fixture object is listed or modified. The task/object remain
available for the browser step; the tool does not delete the fixture.

## One-time stage management container

Use the existing reviewed Ops runtime dependency closure:
`minio==7.2.20` and `urllib3==2.8.0`. There is no new dependency or installation
step. The root task mounts this script read-only into a one-time, resource-limited
management container attached **only to dropfullstage's private control and
storage networks**, with these four stage Secret files:

The private storage connection resolves `storage-backend:9000`. The business
gateway `minio:9000` intentionally rejects administrative CopyObject and is not
the management endpoint. Do not change that gateway's policy or attach this
one-time administrator to any production network. The fixed endpoint/bucket
guard runs before creating HTTP/S3 clients or reading Secrets; it rejects the
business gateway, a public host, another API/Origin or another bucket.

| Default file                  | Purpose                |
| ----------------------------- | ---------------------- |
| `/run/secrets/admin_username` | Stage API login        |
| `/run/secrets/admin_password` | Stage API login        |
| `/run/secrets/s3_access_key`  | Stage S3 administrator |
| `/run/secrets/s3_secret_key`  | Stage S3 administrator |

The four `--*-file` arguments can select other explicitly mounted Secret file
paths. They accept references, never raw credential values. Do not mount
production Secrets, Docker's socket, a database volume or a host profile
directory. No public port is needed. The container command is:

```bash
python /validation/stage_ce_gzip.py \
  --confirm-stage dropfullstage \
  --run-id <new-32-hex-identity>
```

Save its stdout as the public seed proof. It contains only schema/status,
fixture identity, task/artifact/attempt IDs, size/SHA and metadata-test booleans.
Unexpected HTTP/SDK errors produce fixed public error codes with no traceback,
URL, DSN, response body, cookie, CSRF token or key. The script disables library
logging. Each HTTP request/read and the analysis wait are bounded.

## Real browser step on the development machine

Copy only the public successful seed JSON back to a local evidence path. Ensure
the existing stage forwarding reaches exactly `http://127.0.0.1:18080`.
The executor reads exactly `.local/stage_admin_username` and
`.local/stage_admin_password`, in memory, without logging or writing storage
state. It uses the already installed `@playwright/test` through
`createRequire('../../web/package.json')`; it does not add Web E2E source or a
dependency.

```powershell
node scripts/validation/browser_ce_gzip.mjs --proof .local/<public-seed-proof>.json
```

The real browser logs into the stage API, verifies the same current attempt and
artifact, and obtains a **fresh** authorized download grant. The grant must have
the signed `response-content-encoding=identity` override and the exact allowed
fixture path. Actual browser `fetch(...).arrayBuffer()` must retain gzip magic
and match the stored compressed size/SHA. The HTTP response must expose
`Content-Encoding:identity`. A normal navigation to the task must then show
`[data-flame-source="gzip-artifact"]`, proving that the application's real
profile Worker loaded the artifact rather than falling back to its inline tree.

Trace, video, HAR, screenshots, storage-state files and request/error observers
that record content are not enabled. Browser traffic is restricted to the fixed
stage Origin. Every `page.evaluate` catch returns a fixed code; outer errors
produce only a fixed stage-failure code, with no exception cause or Playwright
call log. Browser/context cleanup is silent. Success output contains only safe
IDs, size/SHA and verification booleans. It is independent of the existing full
Web E2E reports and makes no claim about profiles beyond this imported fixture.

## Local preparation evidence

The Python and Node boundary tests were written first and failed because their
new modules did not yet exist. The original six Python tests and two Node tests
passed after implementation. The urllib3 2.8.0 update now passes eight Python tests, including
old-SDK rejection before secret reads and acceptance of the patched SDK. The
unchanged browser tests were not rerun for this Python-only update. Tests cover
current-attempt/owned-object scope, production-origin and
bucket rejection, signed identity override, metadata namespace preservation,
exact encoded-byte verification and exception sanitization. `py_compile`, Node
syntax checking, Ruff and formatting pass. These local checks start no HTTP/S3
service or browser. Actual MinIO mutation, browser bytes and Worker rendering
remain pending until the root task executes both steps against stage.
