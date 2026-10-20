# Controlled synthetic targets: real profilers, no imported profile output.
FROM drop-agent:20261020.1
USER 0:0
COPY core/fixtures/pprof_workload.go core/fixtures/DropJvmFixture.java core/fixtures/io_workload.cpp core/fixtures/python_workload.py core/fixtures/bolt_workload.cpp /demo/src/
RUN mkdir -p /demo/bin /demo/classes && GOCACHE=/tmp/go-cache go build -trimpath -o /demo/bin/drop_go_demo /demo/src/pprof_workload.go && javac -d /demo/classes /demo/src/DropJvmFixture.java && g++ -std=c++20 -Wall -Wextra -Werror -O1 -g -fno-omit-frame-pointer -fno-builtin-malloc -fno-builtin-free /demo/src/io_workload.cpp -o /demo/bin/drop_io_demo && rm -rf /tmp/go-cache
RUN g++ -std=c++20 -Wall -Wextra -Wpedantic -Werror -O2 -g -fno-omit-frame-pointer -fno-reorder-blocks-and-partition -Wl,--emit-relocs -Wl,--build-id=sha1 -no-pie /demo/src/bolt_workload.cpp -o /demo/bin/drop_bolt_demo
USER 10001:10001
