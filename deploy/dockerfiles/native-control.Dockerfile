FROM ubuntu:22.04 AS builder

ARG UBUNTU_MIRROR=http://mirrors.aliyun.com/ubuntu
RUN sed -i \
    -e "s|http://archive.ubuntu.com/ubuntu|${UBUNTU_MIRROR}|g" \
    -e "s|http://security.ubuntu.com/ubuntu|${UBUNTU_MIRROR}|g" \
    /etc/apt/sources.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake libgrpc++-dev libprotobuf-dev libpqxx-dev libssl-dev \
    nlohmann-json3-dev pkg-config protobuf-compiler protobuf-compiler-grpc \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY proto/ ./proto/
COPY native/control/ ./native/control/
COPY native/generated/ ./native/generated/
ARG NATIVE_BUILD_JOBS=1
RUN cmake -S native/control -B /build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /build --parallel "${NATIVE_BUILD_JOBS}"

FROM ubuntu:22.04
ARG UBUNTU_MIRROR=http://mirrors.aliyun.com/ubuntu
RUN sed -i \
    -e "s|http://archive.ubuntu.com/ubuntu|${UBUNTU_MIRROR}|g" \
    -e "s|http://security.ubuntu.com/ubuntu|${UBUNTU_MIRROR}|g" \
    /etc/apt/sources.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libgrpc++1 libprotobuf23 libpqxx-6.4 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/mini-drop-native-control /usr/local/bin/
EXPOSE 50051
ENTRYPOINT ["/usr/local/bin/mini-drop-native-control"]
