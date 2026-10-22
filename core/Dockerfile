ARG FULL_TOOLS_BASE=profiler-deps
FROM ubuntu:24.04 AS toolchain
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake pkg-config libgrpc++-dev protobuf-compiler-grpc libprotobuf-dev libpq-dev libcurl4-openssl-dev libssl-dev nlohmann-json3-dev ca-certificates clang-format && rm -rf /var/lib/apt/lists/*
FROM toolchain AS build
WORKDIR /src
COPY contracts /src/contracts
COPY core /src/core
RUN cmake -S core -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build --parallel 1 && ctest --test-dir build --output-on-failure && cmake --install build --prefix /output

# 独立目标只执行无网络、无内核权限的协议/参数/恢复纯逻辑；发布运行镜像仍来自上面的普通 build。
FROM toolchain AS sanitizer
WORKDIR /src
COPY contracts /src/contracts
COPY core /src/core
ENV ASAN_OPTIONS=detect_leaks=1:halt_on_error=1:abort_on_error=1 UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
RUN cmake -S core -B build-sanitizer -DCMAKE_BUILD_TYPE=Debug -DDROP_ENABLE_SANITIZERS=ON && \
    cmake --build build-sanitizer --parallel 1 --target domain_test json_test catalog_test lifecycle_test sampling_test protocol_property_test && \
    ctest --test-dir build-sanitizer --output-on-failure -R '^(domain|json|catalog|lifecycle|sampling|protocol_properties)$'

FROM ubuntu:24.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends libgrpc++1.51t64 libprotobuf32t64 libpq5 libcurl4t64 libssl3t64 ca-certificates linux-tools-generic && rm -rf /var/lib/apt/lists/* && useradd --system --uid 10001 --create-home drop && mkdir -p /var/lib/drop-agent && chown drop:drop /var/lib/drop-agent
COPY --from=build /output/bin/ /usr/local/bin/
COPY --from=build /output/lib/ /usr/local/lib/
COPY --from=build /output/include/ /usr/local/include/
RUN ldconfig
RUN mkdir -p /opt/drop-tools && perf_path="$(find -L /usr/lib/linux-tools -type f -name perf | head -n 1)" && test -n "$perf_path" && perf_hash="$(sha256sum "$perf_path" | cut -d' ' -f1)" && printf '{"schema_version":1,"tools":{"perf":{"path":"%s","sha256":"%s"}}}\n' "$perf_path" "$perf_hash" > /opt/drop-tools/manifest.json
COPY contracts/task-kinds.json /etc/drop/task-kinds.json
USER 10001:10001
ENTRYPOINT ["/usr/local/bin/drop_server"]

FROM runtime AS profiler-deps
USER root
COPY core/install-arm64-bolt.py /tmp/drop-install-arm64-bolt.py
RUN apt-get update && apt-get install -y --no-install-recommends openjdk-17-jdk-headless python3-venv python3-dev gdb bpftrace binutils libunwind8 libgoogle-perftools-dev libjemalloc-dev g++ golang-go docker.io && \
    case "$(dpkg --print-architecture)" in amd64) apt-get install -y --no-install-recommends bolt-16 ;; arm64) python3 /tmp/drop-install-arm64-bolt.py ;; *) exit 1 ;; esac && \
    rm /tmp/drop-install-arm64-bolt.py && rm -rf /var/lib/apt/lists/*
FROM ${FULL_TOOLS_BASE} AS full-tools
USER root
COPY core/tools-install.py /tmp/drop-tools-install.py
COPY --from=build /output/bin/ /usr/local/bin/
COPY --from=build /output/lib/ /usr/local/lib/
COPY --from=build /output/include/ /usr/local/include/
COPY contracts/task-kinds.json /etc/drop/task-kinds.json
RUN if test -x /opt/drop-tools/venv/bin/memray; then python3 /tmp/drop-tools-install.py --manifest-only; else python3 /tmp/drop-tools-install.py; fi && rm /tmp/drop-tools-install.py
RUN ldconfig
ENV ASPROF_EXECUTABLE=/opt/drop-tools/async-profiler/bin/asprof \
    PY_SPY_EXECUTABLE=/opt/drop-tools/venv/bin/py-spy \
    MEMRAY_EXECUTABLE=/opt/drop-tools/venv/bin/memray \
    PERF2BOLT_EXECUTABLE=/usr/bin/perf2bolt-16
USER 10001:10001
