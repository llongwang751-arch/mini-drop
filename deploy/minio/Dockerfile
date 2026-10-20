# Unofficial Drop fixture repackage of the unchanged official MinIO binary.
# This recipe does not replace any running production storage container.
FROM ubuntu:24.04
ARG TARGETARCH
LABEL org.opencontainers.image.title="Drop MinIO integration-test fixture" \
      org.opencontainers.image.description="Unofficial fixture repackage of the unchanged official MinIO release binary" \
      org.opencontainers.image.version="RELEASE.2025-07-23T15-54-02Z" \
      org.opencontainers.image.source="https://github.com/llongwang751-arch/mini-drop" \
      io.drop.upstream.source="https://github.com/minio/minio" \
      io.drop.upstream.release="https://github.com/minio/minio/releases/tag/RELEASE.2025-07-23T15-54-02Z" \
      io.drop.image.purpose="isolated-integration-test-fixture"
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY deploy/minio/binary-sha256.json /usr/share/doc/minio/binary-sha256.json
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) expected_sha=eef6581f6509f43ece007a6f2eb4c5e3ce41498c8956e919a7ac7b4b170fa431; expected_size=110780600 ;; \
      arm64) expected_sha=ec9f48fee97dee7e6ce8fe9f576471bcd52380c035091d8b075b64d606124c7e; expected_size=105119928 ;; \
      *) echo 'Only TARGETARCH=amd64 or TARGETARCH=arm64 is supported' >&2; exit 2 ;; \
    esac; \
    test "$(dpkg --print-architecture)" = "$TARGETARCH"; \
    release=RELEASE.2025-07-23T15-54-02Z; \
    curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
      --connect-timeout 10 --max-time 180 --retry 2 --retry-delay 2 --retry-max-time 360 \
      --max-filesize 120000000 \
      "https://github.com/minio/minio/releases/download/$release/minio.linux-$TARGETARCH.$release" \
      --output /tmp/minio; \
    test "$(stat -c %s /tmp/minio)" = "$expected_size"; \
    printf '%s  /tmp/minio\n' "$expected_sha" | sha256sum --check --strict -; \
    install -m 0755 /tmp/minio /usr/local/bin/minio; \
    rm /tmp/minio; \
    curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
      --connect-timeout 10 --max-time 30 --retry 2 --retry-delay 2 --retry-max-time 90 \
      --max-filesize 131072 \
      "https://raw.githubusercontent.com/minio/minio/$release/LICENSE" \
      --output /usr/share/doc/minio/LICENSE; \
    printf '%s  /usr/share/doc/minio/LICENSE\n' 0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0 \
      | sha256sum --check --strict -; \
    minio --version | grep -F "$release"; \
    mkdir -p /data
EXPOSE 9000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 CMD curl --fail --silent --show-error --connect-timeout 2 --max-time 3 http://127.0.0.1:9000/minio/health/live || exit 1
ENTRYPOINT ["/usr/local/bin/minio"]
CMD ["server", "/data"]
