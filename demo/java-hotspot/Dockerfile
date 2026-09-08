FROM eclipse-temurin:24.0.2_12-jdk-noble AS build
WORKDIR /src
COPY demo/java-hotspot/Hotspot.java ./
RUN javac Hotspot.java

ARG ASYNC_PROFILER_VERSION=4.4
ARG ASYNC_PROFILER_SHA256=1233f26fc95753e75ce32733bbcaf8f0bedc2c098b0e798af87935b08a63b24e
COPY deploy/vendor/async-profiler-4.4-linux-x64.tar.gz /tmp/async-profiler.tar.gz
RUN echo "${ASYNC_PROFILER_SHA256}  /tmp/async-profiler.tar.gz" | sha256sum -c - \
    && mkdir -p /opt/async-profiler \
    && tar -xzf /tmp/async-profiler.tar.gz -C /opt/async-profiler --strip-components=1 \
    && test -f /opt/async-profiler/lib/libasyncProfiler.so \
    && rm -f /tmp/async-profiler.tar.gz

FROM eclipse-temurin:24.0.2_12-jdk-noble
WORKDIR /app
COPY --from=build /src/Hotspot*.class ./
COPY --from=build /opt/async-profiler /opt/async-profiler
EXPOSE 8082
ENTRYPOINT ["java", "-Xms64m", "-Xmx256m", "Hotspot"]
