FROM golang:1.23-alpine AS build

# CN-friendly module proxy; override with --build-arg GOPROXY=... if needed.
ARG GOPROXY=https://goproxy.cn,direct
ENV GOPROXY=${GOPROXY}

WORKDIR /src
COPY apiserver/go.mod ./
COPY apiserver/ ./
RUN CGO_ENABLED=0 GOOS=linux go test ./... \
    && CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/mini-drop-apiserver ./cmd/apiserver

FROM alpine:3.21
RUN apk add --no-cache ca-certificates
USER 65532:65532
COPY --from=build /out/mini-drop-apiserver /usr/local/bin/mini-drop-apiserver
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/mini-drop-apiserver"]
