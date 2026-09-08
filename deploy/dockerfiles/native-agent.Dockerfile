FROM ubuntu:22.04 AS builder

ARG UBUNTU_MIRROR=http://mirrors.aliyun.com/ubuntu
RUN sed -i \
    -e "s|http://archive.ubuntu.com/ubuntu|${UBUNTU_MIRROR}|g" \
    -e "s|http://security.ubuntu.com/ubuntu|${UBUNTU_MIRROR}|g" \
    /etc/apt/sources.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    libgrpc++-dev \
    libprotobuf-dev \
    pkg-config \
    protobuf-compiler \
    protobuf-compiler-grpc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY proto/ ./proto/
COPY native/agent/ ./native/agent/
COPY native/gperftools_bridge/ ./native/gperftools_bridge/
COPY native/generated/ ./native/generated/
ARG NATIVE_BUILD_JOBS=1
RUN cmake -S native/agent -B /build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /build --parallel "${NATIVE_BUILD_JOBS}" \
    && ctest --test-dir /build --output-on-failure \
    && cmake -S native/gperftools_bridge -B /bridge-build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /bridge-build --parallel "${NATIVE_BUILD_JOBS}"

FROM ubuntu:22.04

ARG UBUNTU_MIRROR=http://mirrors.aliyun.com/ubuntu
RUN sed -i \
    -e "s|http://archive.ubuntu.com/ubuntu|${UBUNTU_MIRROR}|g" \
    -e "s|http://security.ubuntu.com/ubuntu|${UBUNTU_MIRROR}|g" \
    /etc/apt/sources.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    bpftrace \
    curl \
    python3 \
    python3-pip \
    libgrpc++1 \
    libprotobuf23 \
    linux-libc-dev \
    linux-tools-generic \
    gzip \
    tar \
    util-linux \
    && rm -rf /var/lib/apt/lists/* \
    && cp "$(find /usr/lib -path '*/linux-tools-*/perf' \
       -type f | head -n 1)" \
       /usr/local/bin/perf

ARG ASYNC_PROFILER_VERSION=4.4
ARG ASYNC_PROFILER_SHA256=1233f26fc95753e75ce32733bbcaf8f0bedc2c098b0e798af87935b08a63b24e
COPY deploy/vendor/async-profiler-4.4-linux-x64.tar.gz /tmp/async-profiler.tar.gz
RUN echo "${ASYNC_PROFILER_SHA256}  /tmp/async-profiler.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/async-profiler \
    && tar -xzf /tmp/async-profiler.tar.gz -C /opt/async-profiler --strip-components=1 \
    && test -x /opt/async-profiler/bin/asprof \
    && rm -f /tmp/async-profiler.tar.gz

RUN pip3 install --no-cache-dir py-spy==0.4.2

COPY --from=builder /build/mini-drop-native-agent /usr/local/bin/
COPY --from=builder /bridge-build/mini-drop-gperftools-bridge /usr/local/bin/
COPY native/agent/io_latency.bt /opt/mini-drop/io_latency.bt
COPY native/agent/bpftrace_compat.h /opt/mini-drop/bpftrace_compat.h

ENTRYPOINT ["/usr/local/bin/mini-drop-native-agent"]
