FROM eclipse-temurin:21-jdk AS build
WORKDIR /src
COPY HotspotApp.java .
RUN javac --add-modules jdk.httpserver HotspotApp.java

FROM eclipse-temurin:21-jre
ARG ASYNC_PROFILER_VERSION=4.4
WORKDIR /app
# Keep the large profiler download independent from application source changes,
# otherwise every HotspotApp edit invalidates this network-heavy layer.
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) profiler_arch="x64" ;; \
      arm64) profiler_arch="arm64" ;; \
      *) echo "unsupported async-profiler architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL \
      "https://github.com/async-profiler/async-profiler/releases/download/v${ASYNC_PROFILER_VERSION}/async-profiler-${ASYNC_PROFILER_VERSION}-linux-${profiler_arch}.tar.gz" \
      -o /tmp/async-profiler.tar.gz; \
    mkdir -p /opt/async-profiler; \
    tar -xzf /tmp/async-profiler.tar.gz --strip-components=1 -C /opt/async-profiler; \
    rm /tmp/async-profiler.tar.gz
COPY --from=build /src/HotspotApp.class .
EXPOSE 7070
ENTRYPOINT ["java", "--add-modules", "jdk.httpserver", "-XX:+PreserveFramePointer", "-cp", "/app", "HotspotApp"]
