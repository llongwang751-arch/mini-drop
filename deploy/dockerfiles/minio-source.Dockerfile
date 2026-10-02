# Rebuild the existing MinIO release from its exact public upstream source.
# The original public container tags are unavailable; this is not a mirrored image.
FROM golang:1.23.6-bookworm AS build

ARG MINIO_SOURCE_COMMIT=d0cada583fce88f60cb276ddfb06f5cb16820069
ARG MINIO_SOURCE_SHA256=989506993f138bc8092368adaa9e0d8e980aef0da3178e8649ff2d34d3a4a665
ENV CGO_ENABLED=0 GOTOOLCHAIN=local GOMAXPROCS=2
WORKDIR /src
RUN curl --fail --show-error --location \
      "https://codeload.github.com/minio/minio/tar.gz/${MINIO_SOURCE_COMMIT}" \
      --output /tmp/minio-source.tar.gz \
    && echo "${MINIO_SOURCE_SHA256}  /tmp/minio-source.tar.gz" | sha256sum --check \
    && tar --extract --gzip --file /tmp/minio-source.tar.gz --strip-components=1 \
    && rm /tmp/minio-source.tar.gz
RUN go build -mod=readonly -trimpath -p 1 \
      -ldflags "-s -w -X github.com/minio/minio/cmd.Version=2025-04-08T15:41:24Z -X github.com/minio/minio/cmd.ReleaseTag=RELEASE.2025-04-08T15-41-24Z -X github.com/minio/minio/cmd.CopyrightYear=2025 -X github.com/minio/minio/cmd.CommitID=${MINIO_SOURCE_COMMIT} -X github.com/minio/minio/cmd.ShortCommitID=d0cada583fce" \
      -o /out/minio .

FROM debian:bookworm-slim
LABEL org.opencontainers.image.title="MinIO, rebuilt from upstream source" \
      org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="d0cada583fce88f60cb276ddfb06f5cb16820069" \
      org.opencontainers.image.version="RELEASE.2025-04-08T15-41-24Z" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"
RUN apt-get update && apt-get install --yes --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/minio /usr/local/bin/minio
COPY --from=build /src/dockerscripts/docker-entrypoint.sh /usr/local/bin/minio-entrypoint
COPY --from=build /src/LICENSE /licenses/LICENSE
COPY --from=build /src/CREDITS /licenses/CREDITS
RUN chmod 0755 /usr/local/bin/minio-entrypoint && mkdir /data
EXPOSE 9000 9001
ENTRYPOINT ["/usr/local/bin/minio-entrypoint"]
CMD ["server", "/data", "--console-address", ":9001"]
