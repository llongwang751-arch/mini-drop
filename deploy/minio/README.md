# MinIO integration fixture image

This is an **unofficial Drop repackage** of the unchanged official MinIO
`RELEASE.2025-07-23T15-54-02Z` Linux binary. It is for isolated integration tests
and new disposable local development environments. It is not the original
MinIO container image and does not have its image ID or filesystem layers.

On 2026-10-20, the CI pull from Docker Hub was denied. The official Quay manifest
also returned `401 / UNAUTHORIZED` after obtaining an anonymous pull token.
Neither registry was anonymously usable for this exact tag in those checks.
This recipe fetches that same release's binary from the official GitHub release,
checks its fixed byte count and SHA256, and installs it on Ubuntu 24.04. It does
not use a third-party image mirror, change MinIO versions, or replace the tests.
`curl` remains available for the existing readiness probes.

Build from the Drop repository root on a Docker host, before running the
existing Analyzer/Ops isolated integration scripts:

```sh
docker build --platform linux/amd64 --build-arg TARGETARCH=amd64 -t minio/minio:RELEASE.2025-07-23T15-54-02Z -f deploy/minio/Dockerfile .
```

An ARM64 host can use `--platform linux/arm64 --build-arg TARGETARCH=arm64`.
Only these two architectures are accepted, and the selected Ubuntu architecture
must match. A BuildKit multi-platform builder also supplies `TARGETARCH`.
These build instructions are not evidence of successful ARM64 execution.

The local tag matches the existing test scripts' cache lookup. Do not push this
fixture into MinIO's namespace or run this command against the production
Docker daemon. The production storage container, its preserved original cached
image and data volumes are not replaced or restarted. A new local environment
can build this cache entry first; this is not a procedure for upgrading an
existing storage deployment.

[`binary-sha256.json`](binary-sha256.json) records the official download and
checksum URLs, expected sizes, binary hashes and same-tag license hash. The
two binary hashes were independently matched against the official GitHub release
asset metadata and its downloaded `.sha256sum` files. The Dockerfile pins these
same values; updates require reviewing both files together. Its bounded HTTPS
downloads fail on a size/hash mismatch. The official same-tag AGPLv3 license is
downloaded, hash checked and included at `/usr/share/doc/minio/LICENSE`, with
the manifest beside it; source and release URLs are also recorded in image
labels. This recipe does not claim signature verification or byte-identical
reproduction of the unavailable original image.
