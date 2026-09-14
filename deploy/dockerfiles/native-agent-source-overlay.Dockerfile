# Build the builder target of native-agent.Dockerfile first (it runs CTest).
# Overlay only tested binaries on the deployed collector toolchain image.
ARG BUILDER_IMAGE
ARG BASE_IMAGE
FROM ${BUILDER_IMAGE} AS builder
FROM ${BASE_IMAGE}
COPY --from=builder /build/mini-drop-native-agent /usr/local/bin/
COPY --from=builder /bridge-build/mini-drop-gperftools-bridge /usr/local/bin/
