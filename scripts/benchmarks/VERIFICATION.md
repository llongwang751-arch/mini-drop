# Local preparation record — 2026-10-18

## Later execution — 2026-10-23 (UTC+8)

The unchanged bounded tool was rebuilt with Go1.26.10 after its tests and vet
passed, then actually ran in fresh resource-capped Linux containers. All850
measured heartbeats succeeded across10/25/50 identities. The client binary
SHA256 is `b7446d2a81b1d88b168902c5574be1302534759de92cc4b8c2bda4f38f6b0de0`.
See [the actual baseline](../../docs/evidence/jd-quality-20261023/core-heartbeat.json)
and [resource inspection](../../docs/evidence/jd-quality-20261023/core-heartbeat-isolation.json).
The original preparation-only notes below remain a historical record.

Actual local checks used Go1.26.9 on Windows/amd64:

- TDD statistics tests first failed to compile because `sample`/`summarize` did
  not exist. After implementation, all5 tests passed:3 statistics/queue/failure
  cases and2 certificate/option boundary cases.
- `go vet ./...` passed; all Go files were formatted with `gofmt`.
- Native Windows and `CGO_ENABLED=0 GOOS=linux GOARCH=amd64` binaries built.
- `bash -n run-isolated.sh` passed using Git Bash.
- Linux binary SHA256:
  `ddb1b7ffdd84ca3ff4ccaed90e325566352e546077a37323bf473b86a65a457d`.
- Windows binary SHA256:
  `bd1cb5ce5fafb6edf5322a0d0a9886424ad0ef79ef9d2185459d4b402cbcf5e3`.

No Docker fixture, remote host or real Server baseline was executed in this
local preparation. Throughput and latency remain unmeasured until the root
task runs the reviewed tool against the private Linux fixture. The local
certificate test verifies generated identities; it does not replace the
candidate Server's actual mTLS acceptance.
