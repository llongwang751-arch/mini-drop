#!/bin/sh
set -eu

# bind mount 的私钥通常为 0600 且宿主机 UID 不确定。启动阶段复制到容器临时目录，
# 交给 mini-drop 用户后立即降权；应用进程本身始终不以 root 运行。
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

# 对照实验、三阶段快照等运行数据写入持久卷。镜像内的 /app 保持只读，
# 容器首次挂载命名卷时由 root 创建目录，再交给非 root 应用用户。
runtime_dir="${MINI_DROP_RUNTIME_DIR:-/var/lib/mini-drop-runtime}"
install -d -o mini-drop -g mini-drop -m 0750 "$runtime_dir"

if [ "${MINI_DROP_RUN_MIGRATIONS:-0}" = "1" ]; then
  gosu mini-drop python -m alembic upgrade head
fi

exec gosu mini-drop "$@"
