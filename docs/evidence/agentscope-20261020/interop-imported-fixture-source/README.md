# Imported interoperability fixture sources

These are the exact sources hashed by `../interop-numeric-bundle-manifest.json`
and executed by `../interop-numeric-new-runtime.json`. Provider responses and
tool observations are explicitly synthetic. Actual Go/Python processes, the
official SDK, native UDS and distinct Linux UIDs are real. No live credential,
production mount or network is used. The old runtime numeric failure is
retained separately; it is not evidence identifying a past production error.

To reproduce, copy the repository Go module to a temporary build directory,
place `interop_source_test.go` under its `internal/service/`, and compile that
package with `CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go test -tags nomsgpack -c`.
Name the output `interop-service.test`. Put it beside these Python sources in
a private disposable bundle, generate a fresh manifest with SHA256 for all four
files (retain the original public evidence manifest), and run `run_interop.py`
using the exact isolated Docker argv recorded in the successful receipt.
Do not add this Linux fixture to the live API module or connect it to production.
The launcher needs only the recorded SETUID/SETGID/CHOWN/FOWNER capabilities to
assign fixture identities; tested Go/Python children drop to UID10001/10002.

Normal service regressions remain in `agent_runtime/tests` and
`api/internal/service/*agent*test.go`; this artifact preserves the operator
fixture for independent review without shipping a test executable.
