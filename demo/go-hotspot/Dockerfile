FROM golang:1.24-alpine AS builder
WORKDIR /src
COPY main.go .
RUN go mod init mini-drop/go-hotspot \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /go-hotspot .

FROM alpine:3.22
RUN addgroup -S demo && adduser -S -G demo demo
COPY --from=builder /go-hotspot /usr/local/bin/go-hotspot
USER demo
EXPOSE 6060
ENTRYPOINT ["/usr/local/bin/go-hotspot"]

