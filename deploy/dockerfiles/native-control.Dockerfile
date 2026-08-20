FROM ubuntu:22.04 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake libgrpc++-dev libprotobuf-dev libpqxx-dev \
    nlohmann-json3-dev pkg-config protobuf-compiler protobuf-compiler-grpc \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY proto/ ./proto/
COPY native/control/ ./native/control/
RUN cmake -S native/control -B /build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /build --parallel

FROM ubuntu:22.04
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libgrpc++1 libprotobuf23 libpqxx-6.4 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/mini-drop-native-control /usr/local/bin/
EXPOSE 50052
ENTRYPOINT ["/usr/local/bin/mini-drop-native-control"]
