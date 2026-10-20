#!/usr/bin/env bash
set -euo pipefail
umask 077
# 只运行一个串行、有资源边界的夹具，不使用生产凭据或卷。
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
binary=''
core_image=''
output=''
io_device=/dev/nvme0n1
while [[ $# -gt 0 ]]; do
  [[ $# -ge 2 ]] || { printf 'ARGUMENTS_INVALID\n' >&2; exit 2; }
  case "$1" in
    --binary) binary="$2" ;;
    --core-image) core_image="$2" ;;
    --output) output="$2" ;;
    --io-device) io_device="$2" ;;
    *) printf 'ARGUMENTS_INVALID\n' >&2; exit 2 ;;
  esac
  shift 2
done
[[ "$(id -u)" = 0 && -n "$binary" && -n "$core_image" && "$output" = /* && -b "$io_device" ]] || {
  printf 'ROOT_BINARY_IMAGE_NEW_OUTPUT_AND_BLOCK_DEVICE_REQUIRED\n' >&2; exit 2;
}
binary="$(realpath -- "$binary")"
[[ -f "$binary" && -x "$binary" && ! -L "$binary" ]] || { printf 'BINARY_INVALID\n' >&2; exit 2; }
core_id="$(docker image inspect "$core_image" --format '{{.Id}}')"
postgres_id="$(docker image inspect postgres:17.11-alpine --format '{{.Id}}')"
docker_version="$(docker version --format '{{.Server.Version}}')"
[[ "$core_id" =~ ^sha256:[0-9a-f]{64}$ && "$postgres_id" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 2
run_id="$(openssl rand -hex 8)"
prefix="drop-heartbeat-$run_id"
network="$prefix-network"
database="$prefix-postgres"
server="$prefix-server"
client="$prefix-client"
prepare="$prefix-prepare"
for name in "$database" "$server" "$client" "$prepare"; do
  if docker container inspect "$name" >/dev/null 2>&1; then printf 'RESOURCE_COLLISION\n' >&2; exit 2; fi
done
if docker network inspect "$network" >/dev/null 2>&1; then printf 'RESOURCE_COLLISION\n' >&2; exit 2; fi
[[ ! -e "$output" && ! -L "$output" ]] || { printf 'OUTPUT_ALREADY_EXISTS\n' >&2; exit 2; }
mkdir -m 0755 -- "$output"
output="$(realpath -- "$output")"
state="$(mktemp -d "$output/.fixture.XXXXXXXX")"
chown 10001:10001 "$state"
chmod 0700 "$state"
cleanup() {
  local result=$? owner name
  trap - EXIT INT TERM
  for name in "$client" "$prepare" "$server" "$database"; do
    owner="$(docker inspect --format '{{index .Config.Labels "drop.heartbeat.fixture"}}' "$name" 2>/dev/null || true)"
    if [[ "$owner" = "$run_id" ]]; then docker rm -f "$name" >/dev/null 2>&1 || true; fi
  done
  owner="$(docker network inspect --format '{{index .Labels "drop.heartbeat.fixture"}}' "$network" 2>/dev/null || true)"
  if [[ "$owner" = "$run_id" ]]; then docker network rm "$network" >/dev/null 2>&1 || true; fi
  # 只删除本次mktemp目录里已知的文件，不使用递归rm扩大清理范围。
  for name in ca.crt ca.key server.crt server.key postgres.env database-url; do rm -f -- "$state/$name"; done
  rmdir -- "$state" 2>/dev/null || true
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf '%s\n' "$run_id" > "$output/run-id.txt"
sha256sum "$binary" > "$output/client-binary.sha256"
{
  docker run --rm --name "$prepare" --label "drop.heartbeat.fixture=$run_id" --network none \
    --user 10001:10001 --memory=128m --memory-swap=128m --cpus=.25 --pids-limit=32 \
    --cap-drop ALL --read-only --security-opt no-new-privileges --tmpfs /tmp:rw,size=4m \
    --mount "type=bind,src=$binary,dst=/bench,readonly" --mount "type=bind,src=$state,dst=/fixture" \
    --entrypoint /bench "$core_id" --mode prepare --run-id "$run_id"
} > "$output/preparation.json" 2> "$output/preparation.stderr"
password="$(openssl rand -hex 24)"
printf 'POSTGRES_PASSWORD=%s\n' "$password" > "$state/postgres.env"
printf 'postgresql://heartbeat_fixture:%s@postgres/drop_heartbeat\n' "$password" > "$state/database-url"
unset password
chgrp 10001 "$state/database-url"
chmod 0440 "$state/database-url"
docker network create --internal --label "drop.heartbeat.fixture=$run_id" "$network" >/dev/null
migrations=()
schema=0
for migration in "$root"/db/migrations/*.sql; do
  [[ -f "$migration" && ! -L "$migration" ]] || { printf 'MIGRATION_INVALID\n' >&2; exit 2; }
  name="$(basename -- "$migration")"
  number="${name%%_*}"
  [[ "$number" =~ ^[0-9]+$ ]] || exit 2
  schema="$((10#$number))"
  migrations+=(--mount "type=bind,src=$migration,dst=/docker-entrypoint-initdb.d/$name,readonly")
done
[[ "$schema" -ge 5 ]] || { printf 'MIGRATION_SET_INCOMPLETE\n' >&2; exit 2; }
docker run -d --name "$database" --label "drop.heartbeat.fixture=$run_id" \
  --network "$network" --network-alias postgres --memory=192m --memory-swap=192m --cpus=.30 --pids-limit=64 \
  --device-read-bps "$io_device:4194304" --device-write-bps "$io_device:2097152" \
  --tmpfs /var/lib/postgresql/data:rw,size=128m --env-file "$state/postgres.env" \
  -e POSTGRES_USER=heartbeat_fixture -e POSTGRES_DB=drop_heartbeat "${migrations[@]}" \
  "$postgres_id" postgres -c shared_buffers=24MB -c max_connections=12 >/dev/null
ready=false
for _ in $(seq 1 60); do
  if docker exec "$database" psql -U heartbeat_fixture -d drop_heartbeat -tAc \
    "SELECT max(version)=$schema FROM schema_migrations" 2>/dev/null | grep -qx t; then ready=true; break; fi
  sleep 1
done
[[ "$ready" = true ]] || { printf 'PRIVATE_DATABASE_NOT_READY\n' >&2; exit 1; }
docker run -d --name "$server" --label "drop.heartbeat.fixture=$run_id" \
  --network "$network" --network-alias drop-server --user 10001:10001 \
  --memory=192m --memory-swap=192m --cpus=.45 --pids-limit=96 \
  --device-read-bps "$io_device:2097152" --device-write-bps "$io_device:1048576" \
  --cap-drop ALL --read-only --security-opt no-new-privileges --tmpfs /tmp:rw,size=8m \
  -e DATABASE_URL_FILE=/fixture/database-url -e METRICS_LISTEN=off -e GRPC_LISTEN=0.0.0.0:50051 \
  -e TLS_CA_FILE=/tls/ca.crt -e TLS_CERT_FILE=/tls/server.crt -e TLS_KEY_FILE=/tls/server.key \
  -e OTEL_EXPORTER_OTLP_ENDPOINT= -e OTEL_EXPORTER_OTLP_TRACES_ENDPOINT= \
  --mount "type=bind,src=$state/database-url,dst=/fixture/database-url,readonly" \
  --mount "type=bind,src=$state/ca.crt,dst=/tls/ca.crt,readonly" \
  --mount "type=bind,src=$state/server.crt,dst=/tls/server.crt,readonly" \
  --mount "type=bind,src=$state/server.key,dst=/tls/server.key,readonly" \
  --entrypoint /usr/local/bin/drop_server "$core_id" >/dev/null
ready=false
for _ in $(seq 1 30); do
  if docker exec "$server" bash -c 'exec 3<>/dev/tcp/127.0.0.1/50051' >/dev/null 2>&1; then ready=true; break; fi
  sleep 1
done
[[ "$ready" = true ]] || { printf 'PRIVATE_CORE_NOT_READY\n' >&2; exit 1; }
docker create --name "$client" --label "drop.heartbeat.fixture=$run_id" \
  --network "$network" --user 10001:10001 --memory=128m --memory-swap=128m --cpus=.25 --pids-limit=64 \
  --device-read-bps "$io_device:2097152" --device-write-bps "$io_device:1048576" \
  --cap-drop ALL --read-only --security-opt no-new-privileges --tmpfs /tmp:rw,size=8m \
  -e GOMAXPROCS=1 -e GOMEMLIMIT=96MiB \
  --mount "type=bind,src=$binary,dst=/bench,readonly" \
  --mount "type=bind,src=$state/ca.crt,dst=/fixture/ca.crt,readonly" \
  --mount "type=bind,src=$state/ca.key,dst=/fixture/ca.key,readonly" \
  --entrypoint /bench "$core_id" --mode run --run-id "$run_id" \
  --core-image-id "$core_id" --postgres-image-id "$postgres_id" --docker-version "$docker_version" >/dev/null
set +e
docker start -a "$client" > "$output/heartbeat.json" 2> "$output/client.stderr"
attached_status=$?
set -e
client_status="$(docker inspect "$client" --format '{{.State.ExitCode}}')"
snapshot() {
  local name="$1" role="$2"
  docker inspect "$name" --format \
    '{"role":"'"$role"'","memory_bytes":{{.HostConfig.Memory}},"memory_swap_combined_bytes":{{.HostConfig.MemorySwap}},"nano_cpus":{{.HostConfig.NanoCpus}},"read_device_limits":{{json .HostConfig.BlkioDeviceReadBps}},"write_device_limits":{{json .HostConfig.BlkioDeviceWriteBps}},"published_ports":{{json .HostConfig.PortBindings}},"read_only_root":{{.HostConfig.ReadonlyRootfs}},"oom_killed":{{.State.OOMKilled}},"running":{{.State.Running}},"exit_code":{{.State.ExitCode}}}'
}
{
  printf '{"schema_version":1,"provenance":"Actual Docker inspection, no container environment or credential data","containers":['
  snapshot "$database" postgres
  printf ','
  snapshot "$server" core
  printf ','
  snapshot "$client" client
  printf ']}\n'
} > "$output/resources.json"
# 只保留固定的启动/运行日志，不能输出docker inspect中的.Config.Env以免泄密。
docker logs "$server" > "$output/core.log" 2>&1
[[ "$attached_status" = 0 && "$client_status" = 0 ]] || { printf 'HEARTBEAT_BASELINE_FAILED\n' >&2; exit 1; }
for name in "$database" "$server" "$client"; do
  [[ "$(docker inspect "$name" --format '{{.State.OOMKilled}}')" = false ]] || { printf 'FIXTURE_OOM\n' >&2; exit 1; }
done
printf 'HEARTBEAT_BASELINE_COMPLETED\n'
