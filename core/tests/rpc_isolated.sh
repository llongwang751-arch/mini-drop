#!/usr/bin/env bash
set -euo pipefail
# Explicit isolated database/control-plane regression. Production data and
# services are never written or restarted. Only the three named TLS identities
# needed by this regression are mounted; the CA signing key is never mounted.
workspace="$(realpath "${1:?workspace required}")"
tls="$(realpath "${2:?TLS certificate directory required}")"
image="${3:-drop-core-toolchain:v2}"
owner="$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
[[ "$owner" =~ ^[a-f0-9]{16}$ ]]
prefix="drop-core-rpc-$owner"
network="$prefix-network"
run_id="${DROP_CORE_EVIDENCE_RUN_ID:-$owner}"
[[ "$run_id" =~ ^[A-Za-z0-9_-]{1,64}$ ]]
evidence="$workspace/core/evidence/rpc-$run_id"
mkdir -- "$evidence"
ownership=(--label "io.drop.owner=$owner" --label io.drop.purpose=isolated-core-rpc)
docker() {
  command timeout --signal=TERM --kill-after=5s 90s docker "$@"
}
cleanup() {
  for suffix in otlp probe schema4 server server2 postgres; do
    container="$prefix-$suffix"
    if test "$(docker inspect --format '{{index .Config.Labels "io.drop.owner"}}' "$container" 2>/dev/null || true)" = "$owner"; then
      docker rm -f "$container" >/dev/null 2>&1 || true
    fi
  done
  if test "$(docker network inspect --format '{{index .Labels "io.drop.owner"}}' "$network" 2>/dev/null || true)" = "$owner"; then
    docker network rm "$network" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
test -x "$workspace/build/drop_rpc_probe"
test -x "$workspace/build/drop_enqueue_probe"
test -f "$workspace/db/migrations/002_platform.sql"
migration_mounts=()
expected_schema_version=0
for migration in "$workspace"/db/migrations/*.sql; do
  migration_name="$(basename "$migration")"
  migration_number="${migration_name%%_*}"
  expected_schema_version="$((10#$migration_number))"
  migration_mounts+=(-v "$migration:/docker-entrypoint-initdb.d/$migration_name:ro")
done
for name in ca.crt server.crt server.key api.crt api.key agent.crt agent.key; do test -f "$tls/$name"; done
docker network create --internal --label "io.drop.owner=$owner" "$network" >/dev/null
docker run -d --name "$prefix-postgres" "${ownership[@]}" --network "$network" --network-alias postgres \
  --memory=192m --cpus=.3 --tmpfs /var/lib/postgresql/data:rw,size=192m \
  -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=isolated-fixture -e POSTGRES_DB=drop \
  "${migration_mounts[@]}" \
  postgres:17.11-alpine postgres -c shared_buffers=24MB -c max_connections=12 >/dev/null
ready=false
for _ in $(seq 1 60); do
  if docker exec "$prefix-postgres" psql -U postgres -d drop -tAc "SELECT max(version)=$expected_schema_version FROM schema_migrations" 2>/dev/null | grep -qx t; then ready=true; break; fi
  sleep 1
done
test "$ready" = true
sleep 2
if test "${TEST_ONLY_DISPATCH_RACE:-false}" != true && test "${TEST_ONLY_ENQUEUE_RACE:-false}" != true; then
  # Build an actual schema-4 database, rather than merely deleting its version
  # marker. The final Server must reject startup before opening its RPC socket.
  docker exec "$prefix-postgres" createdb -U postgres drop_schema4
  for migration in "$workspace"/db/migrations/*.sql; do
    migration_name="$(basename "$migration")"
    migration_number="${migration_name%%_*}"
    if (( 10#$migration_number < 5 )); then
      docker exec "$prefix-postgres" psql -U postgres -d drop_schema4 -v ON_ERROR_STOP=1 -f "/docker-entrypoint-initdb.d/$migration_name" >/dev/null
    fi
  done
  docker run -d --name "$prefix-schema4" "${ownership[@]}" --network "$network" --user 10001:10001 \
    --read-only --cap-drop ALL --security-opt no-new-privileges --memory=96m --cpus=.25 \
    -e DATABASE_URL=postgresql://postgres:isolated-fixture@postgres/drop_schema4 \
    -e METRICS_LISTEN=off -e TASK_KINDS_FILE=/workspace/contracts/task-kinds.json \
    -v "$workspace:/workspace:ro" "$image" /workspace/build/drop_server >/dev/null
  for _ in $(seq 1 15); do
    if test "$(docker inspect -f '{{.State.Running}}' "$prefix-schema4")" = false; then break; fi
    sleep 1
  done
  test "$(docker inspect -f '{{.State.ExitCode}}' "$prefix-schema4")" = 1
  docker logs "$prefix-schema4" 2>&1 | grep -q CONFIGURATION_ERROR
  printf '{"schema4_startup_rejected":true,"required_schema":5}\n'
fi
database='postgresql://postgres:isolated-fixture@postgres/drop'
for replica in server server2; do
alias=drop-server
if test "$replica" = server2; then alias=drop-server-secondary; fi
docker run -d --name "$prefix-$replica" "${ownership[@]}" --network "$network" --network-alias "$alias" \
  --user 10001:10001 --read-only --cap-drop ALL --security-opt no-new-privileges \
  --memory=96m --cpus=.25 --tmpfs /tmp:rw,size=16m \
  -e OTEL_EXPORTER_OTLP_ENDPOINT=http://otlp:4318 -e DATABASE_URL="$database" -e METRICS_LISTEN=off -e TASK_KINDS_FILE=/workspace/contracts/task-kinds.json \
  -e TLS_CA_FILE=/tls/ca.crt -e TLS_CERT_FILE=/tls/server.crt -e TLS_KEY_FILE=/tls/server.key \
  -v "$workspace:/workspace:ro" -v "$tls/ca.crt:/tls/ca.crt:ro" \
  -v "$tls/server.crt:/tls/server.crt:ro" -v "$tls/server.key:/tls/server.key:ro" \
  "$image" "/workspace/build/${TEST_SERVER_BINARY:-drop_server}" >/dev/null
ready=false
for _ in $(seq 1 30); do
  if docker exec "$prefix-$replica" bash -c 'exec 3<>/dev/tcp/127.0.0.1/50051' 2>/dev/null; then ready=true; break; fi
  sleep 1
done
if test "$ready" != true; then docker logs "$prefix-$replica"; exit 1; fi
done
run_probe() {
docker run --rm --name "$prefix-probe" "${ownership[@]}" --network "$network" --user 10001:10001 \
  --read-only --cap-drop ALL --security-opt no-new-privileges --memory=96m --cpus=.25 \
  -e TEST_ONLY_DISPATCH_RACE="${TEST_ONLY_DISPATCH_RACE:-false}" -e DATABASE_URL="$database" -e ALLOW_ISOLATED_RPC_FIXTURE=true -e AGENT_ID=aliyun-local \
  -e TLS_CA_FILE=/tls/ca.crt -e TEST_API_CERT_FILE=/tls/api.crt -e TEST_API_KEY_FILE=/tls/api.key \
  -e TEST_AGENT_CERT_FILE=/tls/agent.crt -e TEST_AGENT_KEY_FILE=/tls/agent.key \
  -e DROP_SERVER_ADDRESS=drop-server:50051 -e TEST_SECONDARY_SERVER_ADDRESS=drop-server-secondary:50051 \
  -v "$workspace:/workspace:ro" -v "$tls/ca.crt:/tls/ca.crt:ro" \
  -v "$tls/api.crt:/tls/api.crt:ro" -v "$tls/api.key:/tls/api.key:ro" \
  -v "$tls/agent.crt:/tls/agent.crt:ro" -v "$tls/agent.key:/tls/agent.key:ro" \
  "$image" "/workspace/build/$1"
}
if test "${TEST_ONLY_DISPATCH_RACE:-false}" != true; then
  run_probe drop_enqueue_probe
fi
if test "${TEST_ONLY_ENQUEUE_RACE:-false}" != true; then
  # The enqueue admission fixture deliberately has no caller trace context.
  # Start this receiver only for the following explicitly linked RPC fixture.
  docker run -d --name "$prefix-otlp" "${ownership[@]}" --network "$network" --network-alias otlp \
    --user 10001:10001 --read-only --cap-drop ALL --security-opt no-new-privileges \
    --memory=48m --cpus=.1 --entrypoint /usr/bin/python3 \
    -v "$workspace/core/tests/otlp_fixture.py:/otlp_fixture.py:ro" \
    drop-core-full-tools-base:v2 /otlp_fixture.py >/dev/null
  run_probe drop_rpc_probe
fi
if test "${TEST_ONLY_DISPATCH_RACE:-false}" != true && test "${TEST_ONLY_ENQUEUE_RACE:-false}" != true; then
  docker exec "$prefix-otlp" python3 /otlp_fixture.py check | tee "$evidence/server-otlp.json"
fi
printf '{"schema_version":1,"rpc_probe":"passed","isolated_database":true,"production_mutations":false}\n'
