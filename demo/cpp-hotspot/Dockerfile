FROM alpine:3.24.1 AS build
ARG ALPINE_MIRROR=https://mirrors.aliyun.com/alpine
RUN sed -i "s|https://dl-cdn.alpinelinux.org/alpine|${ALPINE_MIRROR}|g" /etc/apk/repositories \
    && apk add --no-cache g++
WORKDIR /src
COPY main.cpp build.sh ./
RUN chmod 0755 build.sh && ./build.sh /out/cpp-hotspot

FROM alpine:3.24.1
COPY --from=build /out/cpp-hotspot /usr/local/bin/cpp-hotspot
EXPOSE 8084
ENTRYPOINT ["/usr/local/bin/cpp-hotspot"]
