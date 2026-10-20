#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.12
go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1
protoc --proto_path=../contracts --go_out=. --go_opt=module=drop/api --go-grpc_out=. --go-grpc_opt=module=drop/api ../contracts/drop.proto
