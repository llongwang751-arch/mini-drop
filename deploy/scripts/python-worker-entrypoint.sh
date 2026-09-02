#!/bin/sh
set -eu

# The diagnosis worker may terminate TLS for the private Go-to-Python gRPC hop.
if [ "${MINI_DROP_GRPC_SECURE:-0}" = "1" ]; then
  cert_source="${MINI_DROP_GRPC_CERT_FILE:-}"
  key_source="${MINI_DROP_GRPC_KEY_FILE:-}"
  if [ -z "$cert_source" ] || [ -z "$key_source" ]; then
    echo "gRPC TLS enabled but certificate paths are missing" >&2
    exit 1
  fi
  install -d -o mini-drop -g mini-drop -m 0700 /tmp/mini-drop-tls
  install -o mini-drop -g mini-drop -m 0644 "$cert_source" /tmp/mini-drop-tls/server.crt
  install -o mini-drop -g mini-drop -m 0600 "$key_source" /tmp/mini-drop-tls/server.key
  export MINI_DROP_GRPC_CERT_FILE=/tmp/mini-drop-tls/server.crt
  export MINI_DROP_GRPC_KEY_FILE=/tmp/mini-drop-tls/server.key
fi

runtime_dir="${MINI_DROP_RUNTIME_DIR:-/var/lib/mini-drop-runtime}"
install -d -o mini-drop -g mini-drop -m 0750 "$runtime_dir"

exec gosu mini-drop "$@"
