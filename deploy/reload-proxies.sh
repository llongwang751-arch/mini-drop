#!/usr/bin/env bash
# Refresh startup-resolved Docker upstream addresses after replacing services.
# Run from the selected deployment directory; optional arguments are Compose
# global options (for example explicit -p/-f/--env-file for an isolated stage).
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
