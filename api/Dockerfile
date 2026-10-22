FROM golang:1.26.10-alpine AS build
ARG GOPROXY=https://goproxy.cn,direct
# 默认使用Alpine官方仓库；内网构建可显式传入可信镜像主机，不改变apk签名校验。
ARG ALPINE_MIRROR=dl-cdn.alpinelinux.org
ENV GOPROXY=${GOPROXY}
ENV GOMAXPROCS=2 GOMEMLIMIT=512MiB GOFLAGS=-p=1
WORKDIR /src/api
COPY api/go.mod api/go.sum ./
RUN go mod download
COPY contracts/drop.proto /src/contracts/drop.proto
COPY contracts/task-kinds.json /src/contracts/task-kinds.json
COPY contracts/fixtures/ /src/contracts/fixtures/
COPY scripts/evaluation/diagnostic_cases.json /src/scripts/evaluation/diagnostic_cases.json
COPY scripts/evaluation/diagnostic_cases_v2.json /src/scripts/evaluation/diagnostic_cases_v2.json
RUN case "$ALPINE_MIRROR" in ''|*[!A-Za-z0-9.-]*) exit 1 ;; esac && sed -i "s#dl-cdn.alpinelinux.org#${ALPINE_MIRROR}#g" /etc/apk/repositories && apk add --no-cache protobuf && go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.12 && go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1
COPY api/ ./
RUN protoc --proto_path=/src/contracts --go_out=. --go_opt=module=drop/api --go-grpc_out=. --go-grpc_opt=module=drop/api /src/contracts/drop.proto
RUN CGO_ENABLED=0 go test -trimpath -tags=nomsgpack ./... && CGO_ENABLED=0 go build -trimpath -tags=nomsgpack -ldflags="-s -w" -o /out/drop-api ./cmd/drop-api

FROM alpine:3.21.3
ARG ALPINE_MIRROR=dl-cdn.alpinelinux.org
RUN case "$ALPINE_MIRROR" in ''|*[!A-Za-z0-9.-]*) exit 1 ;; esac && sed -i "s#dl-cdn.alpinelinux.org#${ALPINE_MIRROR}#g" /etc/apk/repositories && apk upgrade --no-cache && apk add --no-cache ca-certificates tzdata && addgroup -S drop && adduser -S drop -G drop
WORKDIR /app
ENV GOMEMLIMIT=192MiB GOMAXPROCS=2
COPY --from=build /out/drop-api /usr/local/bin/drop-api
COPY contracts/task-kinds.json /app/contracts/task-kinds.json
COPY db/migrations/ /app/db/migrations/
USER drop
EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=5s --start-period=20s CMD wget -q -O /dev/null http://127.0.0.1:8080/health/live || exit 1
ENTRYPOINT ["drop-api"]
