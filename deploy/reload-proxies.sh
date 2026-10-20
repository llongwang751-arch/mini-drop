#!/usr/bin/env bash
# 替换服务容器后，刷新Nginx在启动时解析并缓存的Docker上游地址。
# 从选定部署目录执行；可选参数是Compose
# 全局选项，例如隔离stage使用明确的-p/-f/--env-file，避免误操作默认项目。
set -euo pipefail
compose=(docker compose "$@")
services="$("${compose[@]}" config --services)"
for proxy in minio web gateway; do
  if ! [[ $'\n'"$services"$'\n' == *$'\n'"$proxy"$'\n'* ]]; then
    continue
  fi
  if [[ -z "$("${compose[@]}" ps -q "$proxy")" ]]; then
    echo "Selected reverse proxy is not running: $proxy" >&2
    exit 1
  fi
  config=()
  if [[ "$proxy" == gateway ]]; then
    config=(-c /etc/drop/runtime/edge.conf)
  fi
  "${compose[@]}" exec -T "$proxy" nginx "${config[@]}" -t
  "${compose[@]}" exec -T "$proxy" nginx "${config[@]}" -s reload
done
echo 'Selected reverse proxies reloaded; verify application readiness.'
