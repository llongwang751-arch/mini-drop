FROM golang:1.26.5-alpine AS build
WORKDIR /src
COPY go.mod main.go ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/go-hotspot .

FROM alpine:3.24.1
COPY --from=build /out/go-hotspot /usr/local/bin/go-hotspot
EXPOSE 6060
ENTRYPOINT ["/usr/local/bin/go-hotspot"]
