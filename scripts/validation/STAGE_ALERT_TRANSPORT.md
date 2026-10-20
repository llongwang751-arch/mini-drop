# Stage alert transport acceptance

`stage_alert_transport.py` prepares a real, bounded integration check of
Alertmanager → maintenance's authenticated `/alerts` receiver → PostgreSQL
`audit_logs`. It injects one explicitly imported synthetic alert directly through
Alertmanager's API. It does not evaluate Prometheus expressions, cause service
failures or establish that all18 monitoring rules actually fired. Reports always
state `scope: synthetic_transport_only` and `rule_evaluation_verified: false`.

Actual stage execution passed on 2026-10-19 in 336.485 seconds: one firing and
one resolved audit row, ordered correctly. The public report is
`docs/evidence/upgrade-20261019/monitoring/stage-alert-transport.json`.
A separate actual `DropServiceDown` failure/recovery test passed; neither check
establishes that every monitoring rule has fired. Run this tool on the Linux Docker host only
after the root task has received the exclusive server queue and started the
isolated `dropfullstage` project. There are no new Python dependencies. Copy this
single script to the prepared validation directory, then run:

```bash
python3 /opt/drop-build/<validation-directory>/stage_alert_transport.py \
  --confirm-stage dropfullstage > /opt/drop-build/<validation-directory>/alert-transport.json
```

Use a new public proof output path. Yield and poll the running command; the
reviewed30-second `group_wait` and five-minute `group_interval` remain unchanged.
The combined polling deadline is480seconds, checked before and after each
bounded database query. Individual Docker commands have8-second timeouts;
HTTP uses5-second socket timeouts. A failed run may additionally spend bounded
time posting resolution for its own alert. Do not shorten route timing to make
the check finish faster.

The tool uses only `127.0.0.1:19093` and the local Unix Docker socket; it ignores
inherited Docker contexts. It discovers exactly one running Alertmanager and
PostgreSQL container with Compose project `dropfullstage` and the expected
service labels. Alertmanager's port9093 must bind only to127.0.0.1:19093;
PostgreSQL must have no published ports. Container IDs are rechecked before
each POST. Any container replacement or scope change is refused.

Before posting, the tool inspects Alertmanager's **loaded** configuration from
`/api/v2/status` without printing it. Its only receiver must be `drop-audit`, with
one internal webhook to `http://maintenance:9105/alerts`, `send_resolved: true`,
no alternate URL file/proxy and no other notification integrations. Redacted
webhook URLs are accepted only after independently reading the fixed configuration
from the verified stage container, verifying its actual startup config argument,
reloading the same bytes and observing a strictly newer successful-reload timestamp.
Missing URLs and unverified redaction are refused. No external person receives a
message. No gateway policy, maintenance code or alert route is changed.

A fresh128-bit random suffix creates
`ImportedStageTransportProbe_<32-lowercase-hex>`. Existing audit rows or an active
Alertmanager alert with this identity cause refusal before any POST. The caller
cannot select an existing alert, object ID, project, endpoint, SQL or credential.
The first alert starts now and expires15minutes later if the host process dies.
After observing a valid firing audit row, the tool posts the **same labels and
start time**, with `endsAt` set to now, then waits for a resolved audit row.
It requires the first firing audit ID to precede the first resolved audit ID.

Only fixed SQL runs inside the already verified stage PostgreSQL container, with
`BEGIN READ ONLY` and a three-second statement timeout. The validated generated
name is passed as a quoted psql variable. The database password is read from
`/run/secrets/database_password` inside that container; no credential enters a
host argument, public report or captured stderr. The SELECT returns only bounded
counts and first IDs for this identity, validating action/resource/request ID and
all four allowed labels. Audit rows are retained as evidence; no SQL mutation or
data-volume change occurs.

Stdout contains one safe JSON report: schema, project, synthetic identity,
firing/resolved counts, delivery order, elapsed time and fixed error codes.
Configuration, credential, HTTP body, cookie, URL and native exception text are
never printed. Command and HTTP output are size limited. On failure before a
successful resolution POST, best-effort cleanup posts resolution only for this
invocation's identity after repeating stage/receiver checks. It never deletes an
alert, audit row, container, volume or unrelated resource. If cleanup fails, the
original15-minute expiry remains in place.

The9 local tests use fake clocks, fake clients and controlled subprocess streams;
they start no Docker service, HTTP server or database. They cover receiver and
stage boundaries, same-identity firing/resolved delivery, duplicate refusal,
read-only SQL/name validation, malformed audit results,480-second timeout,
exception/CLI sanitization, bounded output and the fixed local Docker daemon.
The new test module initially failed before implementation; all9 now pass.
Python compilation, Ruff checks and formatting also pass. These results are
preparation evidence; the separate actual delivery report records the integration result.

The request shapes follow the pinned
[Alertmanager v0.34.1 API contract](https://github.com/prometheus/alertmanager/blob/v0.34.1/api/v2/openapi.yaml).
