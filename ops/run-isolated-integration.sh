#!/usr/bin/env bash
set -euo pipefail
umask 077
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
prefix="${OPS_TEST_PREFIX:-drop-ops-test-$(openssl rand -hex 8)}"
[[ "$prefix" =~ ^[a-zA-Z0-9_-]{1,80}$ ]] || { printf 'Invalid isolated Ops prefix.\n' >&2; exit 2; }
network="$prefix-network"
database="$prefix-postgres"
storage="$prefix-minio"
runner="$prefix-runner"
image="${OPS_TEST_IMAGE:-drop-ops:full}"
env_file=''
cleanup() {
  local result=$? owner name
  trap - EXIT INT TERM
  for name in "$runner" "$database" "$storage"; do
    owner="$(docker inspect --format '{{index .Config.Labels "drop.ops.fixture"}}' "$name" 2>/dev/null || true)"
    if [[ "$owner" = "$prefix" ]]; then docker rm -f "$name" >/dev/null 2>&1 || true; fi
  done
  owner="$(docker network inspect --format '{{index .Labels "drop.ops.fixture"}}' "$network" 2>/dev/null || true)"
  if [[ "$owner" = "$prefix" ]]; then docker network rm "$network" >/dev/null 2>&1 || true; fi
  if [[ -n "$env_file" ]]; then rm -f -- "$env_file"; fi
  exit "$result"
}
# Refuse collisions before installing cleanup; only this run's labelled
# disposable resources are eligible for removal.
for name in "$runner" "$database" "$storage"; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    printf 'Refusing an existing isolated Ops container.\n' >&2; exit 2
  fi
done
if docker network inspect "$network" >/dev/null 2>&1; then
  printf 'Refusing an existing isolated Ops network.\n' >&2; exit 2
fi
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
env_file="$(mktemp /tmp/drop-ops-test.XXXXXXXX)"
password="$(openssl rand -hex 24)"
access="$(openssl rand -hex 12)"
key="$(openssl rand -hex 24)"
printf 'POSTGRES_PASSWORD=%s\nMINIO_ROOT_USER=%s\nMINIO_ROOT_PASSWORD=%s\nTEST_DATABASE_URL=postgresql://drop_test:%s@%s:5432/drop_ops_test?sslmode=disable\nTEST_S3_ENDPOINT=%s:9000\nTEST_S3_ACCESS_KEY=%s\nTEST_S3_SECRET_KEY=%s\n' "$password" "$access" "$key" "$password" "$database" "$storage" "$access" "$key" > "$env_file"
unset password access key
docker network create --internal --label "drop.ops.fixture=$prefix" "$network" >/dev/null
docker run --detach --name "$database" --network "$network" --label "drop.ops.fixture=$prefix" \
  --env-file "$env_file" --memory=192m --memory-swap=192m --cpus=.4 --pids-limit=64 \
  --tmpfs /var/lib/postgresql/data:rw,size=128m -e POSTGRES_USER=drop_test -e POSTGRES_DB=drop_ops_test postgres:17.11-alpine >/dev/null
docker run --detach --name "$storage" --network "$network" --label "drop.ops.fixture=$prefix" \
  --env-file "$env_file" --memory=256m --memory-swap=256m --cpus=.3 --pids-limit=64 \
  --tmpfs /data:rw,size=64m minio/minio:RELEASE.2025-07-23T15-54-02Z server /data >/dev/null
ready=0
for i in $(seq 1 60); do
  if docker exec "$database" pg_isready -U drop_test -d drop_ops_test >/dev/null 2>&1 && \
     docker exec "$storage" curl --connect-timeout 2 --max-time 3 -fsS http://localhost:9000/minio/health/ready >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[[ "$ready" = 1 ]] || { printf 'Isolated Ops dependencies did not become ready.\n' >&2; exit 1; }
docker run --rm --name "$runner" --network "$network" --label "drop.ops.fixture=$prefix" \
  --env-file "$env_file" --memory=192m --memory-swap=192m --cpus=.4 --pids-limit=64 \
  --cap-drop ALL --read-only --security-opt no-new-privileges --tmpfs /tmp:rw,size=16m,mode=1777 \
  -v "$root/ops:/tests:ro" -v "$root/db/migrations:/migrations:ro" -w /tests \
  --entrypoint python "$image" -m unittest test_reconcile test_lifecycle test_storage_bytes test_encrypt_existing test_runtime_config test_integration -v
