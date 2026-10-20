# Python runtime build inputs

The public Analyzer and Ops recipes use the official multi-platform
`python:3.12.15-slim-trixie` index, pinned to
`sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1`.
The reviewed amd64 child manifest is
`sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe`.
They retain Debian's Trixie, Trixie updates and Trixie security suites. The
Aliyun HTTPS mirror changes transport location only; APT still requires the
official `/usr/share/keyrings/debian-archive-keyring.pgp` signature keyring.
Retries are limited to two, with 20-second HTTP/HTTPS timeouts and IPv4.

| Input | Analyzer | Ops |
| --- | --- | --- |
| Python | 3.12.15 | 3.12.15 |
| urllib3 | 2.8.0 | 2.8.0 |
| psycopg | 3.3.6, pure Python | 3.3.6, pure Python |
| libpq5 | 17.11-0+deb13u1 | 17.11-0+deb13u1 |
| OpenJDK headless JDK | 21.0.12.1+1-1~deb13u1 | not installed directly |
| bolt-19 | 1:19.1.7-3+b1 | not installed directly |
| binutils | 2.44-3 | not installed directly |
| linux-perf | 6.12.113-1 | not installed directly |

`PSYCOPG_IMPL=python` makes the database implementation explicit. The lock
contains neither `psycopg-binary` nor `psycopg-c`; libpq and its TLS dependency
come from authenticated Debian packages. Local development therefore needs a
loadable system libpq. The Analyzer exposes the genuine LLVM19 binary through
`/usr/local/bin/llvm-bolt`, and verifies JFR, perf and addr2line are present.
It does not fabricate an LLVM16 installation or execute uploaded ELF files.

Build from the repository root:

```sh
docker build -f analyzer/Dockerfile -t drop-analyzer:review .
docker build -f ops/Dockerfile -t drop-ops:review .
```

`ops/Dockerfile.runtime` is the same repository-build recipe as
`ops/Dockerfile`. All public recipes download wheels from official PyPI using
`--only-binary=:all: --require-hashes`, then run `pip check`. Every COPY input
exists in the repository; no private wheel directory is needed.

The public locks preserve the prior official artifact hashes for unchanged
versions and include all official release hashes for
[psycopg3.3.6](https://pypi.org/pypi/psycopg/3.3.6/json) and
[urllib3 2.8.0](https://pypi.org/pypi/urllib3/2.8.0/json). Analyzer has34
distributions and Ops10. CPython3.12/Linux ARM64 dependency-marker closure and
34 selected wheel hashes were checked, including seven actually downloaded
native ARM64 wheels. These are download/metadata checks, with no ARM execution
or successful ARM image build implied. Debian lists the same BOLT19 build for
[amd64 and arm64](https://packages.debian.org/trixie/bolt-19); native image and
tool execution remain separate release gates.

The measured release candidates were built from small private contexts using
preverified amd64 wheels, a single-artifact-per-package lock and `--no-index`.
The public locks contain the same package versions and every offline wheel's
SHA256, plus the other official architecture artifacts. Public recipes use
the same base digest, direct APT pins, timeout policy, service sources, UID/GID
10001 and health entrypoints. Online installation and the broader lock change
the build inputs, so a public rebuild has its own image ID and must be tested
and scanned independently. APT upgrades retain a signed, moving transitive
dependency closure; installed package inventories and SBOMs are the evidence
for each image. Missing pinned packages fail the build rather than silently
selecting a different version.

The actual Ops rc4 evidence is recorded in
[`ops-security-rc4-runtime-2026-10-18.json`](../docs/evidence/full/ops-security-rc4-runtime-2026-10-18.json)
and its adjacent package inventory. It does not validate a later public
rebuild. Its security scan still reports0 Critical and61 High; the high-severity
gate remains failed. Historical Analyzer evidence retains its original
Python/JDK/LLVM versions. Current candidate integration, format, tool and
512MiB measurements must be read from their own release evidence, without
relabeling those older results.
