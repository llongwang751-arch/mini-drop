# Python CRM performance integration

Source: a separately provided, user-owned Python CRM repository.
This is an isolated enrollment of its real palette service and original tests,
not a deployment of a complete CRM. Original CRM source/config/data stay intact.

The CRM business source is not distributed in this repository. Provide its
location explicitly; the packager never reads a default local desktop path:

```sh
python integrations/crm-python/package.py --help
python integrations/crm-python/package.py --source /path/to/AiNativeCrm-python --output .local/integrations/crm-python/context
```

On Windows, use the corresponding quoted absolute source path. The output must
be new or empty, outside the source tree, and contain no symlink path components.
The command exports an allowlisted snapshot, source-file SHA manifest, locked
dependencies and immutable harness.
It excludes `.env`, business data, temporary trees and credentials. Review the
manifest, then transfer that generated context to the controlled Docker host.

Build `Dockerfile` from that context with an explicit new image tag. On the host,
run the operator-only enrollment utility, for example:

```sh
sudo python3 ops/enroll_code_project.py \
  --context /opt/tx-drop-20261020/crm-context \
  --runner-image drop-crm-sandbox:20261020.4 \
  --workload-image drop-crm-sandbox:20261020.3 \
  --workload-container txdrop-crm-workload-1 --agent-id tencent-local
```

The registered source revision cannot be replaced with another version under
the same project ID. Images are pinned to Docker image IDs. Source changes need
a separately reviewed enrollment/release. `tx-drop-code-executor.service` owns
the local socket; the API runs as UID10001 and mounts only its socket directory.
`compose.agent-v2.yml` is the exact Tencent deployment overlay, not a generic
production CRM manifest. Agent/helper can only see their shared container PID
namespace and explicitly allowed UID10001; they do not use host PID namespaces.

Open `/investigations`, select the connected Python CRM, describe the problem,
and optionally consent to linked source processing. Review and confirm the
collection plan. The platform automatically samples, diagnoses, resamples,
diagnoses again, compares evidence, and queues the bound source repair.
Administrators can also start source analysis independently.

The source Agent executes real cProfile, reads exact-SHA lines, applies a model
patch to a separate source copy and runs the original CRM palette tests plus
frozen Python collection/identity/rate-limit regressions. Six alternating A/B
pairs each use four edge colors and 60 seeded random colors, with fresh processes for each side.
Output digests preserve Python types and values, including list/tuple; JSON
equivalence alone is insufficient. CPU process time is primary; wall time is
also retained, but is sensitive to CPU quotas. A median speedup >=1.10 and at
least five ratios >1.05 are a practical preset gate, not a statistical proof.
The candidate is profiled again. Nothing is published into a production CRM.

Generated code has no network, credentials, Docker socket, service volumes or
Agent namespace. Tests/harness/auth/limiters cannot be edited. Cache sizes are
bounded and mutable return values must retain caller isolation. Acceptance
applies only to this synthetic business-function benchmark, not customer traffic,
all CRM endpoints, production P95/P99, or every sentence in a model explanation.
