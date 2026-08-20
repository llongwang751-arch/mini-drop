FROM gcc:14-bookworm AS builder
WORKDIR /src
COPY main.cpp .
RUN g++ -O2 -g -fno-omit-frame-pointer -static-libstdc++ -static-libgcc \
    -std=c++17 main.cpp -o /cpp-hotspot

FROM debian:bookworm-slim
RUN useradd --system --create-home demo
COPY --from=builder /cpp-hotspot /usr/local/bin/cpp-hotspot
USER demo
ENTRYPOINT ["/usr/local/bin/cpp-hotspot"]
