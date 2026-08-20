FROM ubuntu:22.04 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
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
RUN cmake -S native/agent -B /build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /build --parallel \
    && ctest --test-dir /build --output-on-failure

FROM ubuntu:22.04

RUN apt-get update && apt-get install -y --no-install-recommends \
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
       /usr/local/bin/perf \
    && curl -fsSL https://dl.min.io/client/mc/release/linux-amd64/mc \
       -o /usr/local/bin/mc \
    && chmod +x /usr/local/bin/mc

ARG ASYNC_PROFILER_VERSION=4.4
RUN curl -fsSL \
      "https://github.com/async-profiler/async-profiler/releases/download/v${ASYNC_PROFILER_VERSION}/async-profiler-${ASYNC_PROFILER_VERSION}-linux-x64.tar.gz" \
      -o /tmp/async-profiler.tar.gz \
    && mkdir -p /opt/async-profiler \
    && tar -xzf /tmp/async-profiler.tar.gz -C /opt/async-profiler --strip-components=1 \
    && test -x /opt/async-profiler/bin/asprof \
    && rm -f /tmp/async-profiler.tar.gz

RUN pip3 install --no-cache-dir py-spy==0.4.2

COPY --from=builder /build/mini-drop-native-agent /usr/local/bin/
COPY agent/mini_drop_agent/collectors/scripts/io_latency.bt /opt/mini-drop/io_latency.bt
COPY native/agent/bpftrace_compat.h /opt/mini-drop/bpftrace_compat.h

ENTRYPOINT ["/usr/local/bin/mini-drop-native-agent"]
