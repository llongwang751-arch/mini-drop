#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run as root: sudo $0 [repository-path]" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${1:-$SCRIPT_DIR/../..}" && pwd)"
BUILD_DIR="${MINI_DROP_NATIVE_BUILD_DIR:-$ROOT/build/native-agent}"

for command in cmake protoc grpc_cpp_plugin; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "missing build dependency: $command" >&2
    exit 2
  }
done

cmake -S "$ROOT/native/agent" -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release
cmake --build "$BUILD_DIR" --parallel
ctest --test-dir "$BUILD_DIR" --output-on-failure
install -m 0755 "$BUILD_DIR/mini-drop-native-agent" /usr/local/bin/mini-drop-native-agent

install -d -m 0750 /etc/mini-drop /etc/mini-drop/certs \
  /var/lib/mini-drop-agent /opt/mini-drop
install -m 0644 "$ROOT/native/agent/io_latency.bt" /opt/mini-drop/io_latency.bt
install -m 0644 "$ROOT/native/agent/bpftrace_compat.h" /opt/mini-drop/bpftrace_compat.h
if [[ ! -e /etc/mini-drop/worker.env ]]; then
  install -m 0600 "$ROOT/deploy/env/worker.env.example" /etc/mini-drop/worker.env
fi
install -m 0644 "$ROOT/deploy/systemd/mini-drop-agent.service" /etc/systemd/system/mini-drop-agent.service
systemctl daemon-reload

echo "installed native Agent: /usr/local/bin/mini-drop-native-agent"
echo "installed eBPF assets: /opt/mini-drop"
for runtime in curl perf bpftrace py-spy; do
  if ! command -v "$runtime" >/dev/null 2>&1; then
    echo "warning: $runtime is not installed; its collector will not be advertised" >&2
  fi
done
if [[ ! -x /opt/async-profiler/bin/asprof ]]; then
  echo "warning: /opt/async-profiler/bin/asprof is missing; java_async will not be advertised" >&2
fi
echo "copy this Worker's ca.crt, agent.crt and agent.key to /etc/mini-drop/certs"
echo "edit /etc/mini-drop/worker.env, then run:"
echo "  systemctl enable --now mini-drop-agent"
