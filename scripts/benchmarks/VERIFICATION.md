# Local preparation record — 2026-10-18

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
