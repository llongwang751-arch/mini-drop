module drop/benchmarks

go 1.26.0

toolchain go1.26.10

require (
	drop/api v0.0.0
	google.golang.org/grpc v1.84.0
)

require (
	golang.org/x/net v0.60.0 // indirect
	golang.org/x/sys v0.49.0 // indirect
	golang.org/x/text v0.43.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20260928230214-8a89bd6388cc // indirect
	google.golang.org/protobuf v1.36.12 // indirect
)

replace drop/api => ../../api
