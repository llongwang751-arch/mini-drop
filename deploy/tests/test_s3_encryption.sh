#!/usr/bin/env bash
# 私有的一次性夹具，不使用生产卷、密钥、端口或凭据。
set -euo pipefail
umask 077
source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
job="drop-encryption-$(date -u +%s)-$(openssl rand -hex 4)"
[[ "$job" =~ ^drop-encryption-[0-9]+-[a-f0-9]{8}$ ]] || exit 2
fixture="$(mktemp -d /tmp/drop-encryption.XXXXXXXX)"
chmod 700 "$fixture"
network="$job-network"
volume="$job-data"
container="$job-storage"
probe_container="$job-probe"
init_container="$job-volume-init"
client_image="${DROP_ENCRYPTION_TEST_IMAGE:-drop-ops:full-ready}"
network_created=0
volume_created=0
cleanup() {
  local result=$? name owner
  trap - EXIT INT TERM HUP
  # 只有容器所有权标签匹配本次运行时才允许删除，防止误清理其他容器。
  for name in "$container" "$probe_container" "$init_container"; do
    owner="$(docker inspect --format '{{index .Config.Labels "drop.encryption.fixture"}}' "$name" 2>/dev/null || true)"
    if [[ "$owner" == "$job" ]]; then
      docker rm -f "$name" >/dev/null 2>&1 || true
    fi
  done
  if (( network_created )); then
    docker network rm "$network" >/dev/null 2>&1 || true
  fi
  if (( volume_created )); then
    docker volume rm "$volume" >/dev/null 2>&1 || true
  fi
  [[ "$fixture" == /tmp/drop-encryption.* && "$(dirname -- "$fixture")" == /tmp && -d "$fixture" && ! -L "$fixture" ]] || exit 2
  rm -f -- "$fixture/access" "$fixture/password" "$fixture/kms" "$fixture/original-kms" "$fixture/payload.bin"
  if ! rmdir -- "$fixture" >/dev/null 2>&1; then
    printf 'Private encryption fixture directory cleanup failed.\n' >&2
    if (( result == 0 )); then result=1; fi
  fi
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
openssl rand -hex 12 > "$fixture/access"
openssl rand -hex 24 > "$fixture/password"
printf 'fixture-v1:%s\n' "$(openssl rand -base64 32)" > "$fixture/kms"
cp "$fixture/kms" "$fixture/original-kms"
head -c 2097152 /dev/urandom > "$fixture/payload.bin"
chmod 444 "$fixture/"*
# docker volume create可能复用已有卷，因此这里必须显式拒绝已存在的同名卷。
if docker volume inspect "$volume" >/dev/null 2>&1; then
  printf 'Refusing to reuse an existing fixture volume.\n' >&2
  exit 2
fi
docker network create --internal --label "drop.encryption.fixture=$job" "$network" >/dev/null
network_created=1
docker volume create --label "drop.encryption.fixture=$job" "$volume" >/dev/null
volume_created=1
docker run --rm --name "$init_container" --label "drop.encryption.fixture=$job" \
  --memory 64m --memory-swap 64m --cpus .1 --pids-limit 16 \
  --network none --read-only --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER \
  --user 0:0 -v "$volume:/data" ubuntu:24.04 sh -ec 'chown 10001:10001 /data && chmod 700 /data'
start_storage() {
  local auto_encryption="${1:-on}"
  [[ "$auto_encryption" == on || "$auto_encryption" == off ]] || return 2
  docker run -d --name "$container" --label "drop.encryption.fixture=$job" \
    --network "$network" --network-alias encrypted-storage \
    --user 10001:10001 --cap-drop ALL --read-only --security-opt no-new-privileges \
    --memory 256m --memory-swap 256m --cpus .25 --pids-limit 64 \
    --tmpfs /tmp:size=16m,mode=1777 -v "$volume:/data" \
    --mount "type=bind,src=$fixture/access,dst=/fixture/access,readonly" \
    --mount "type=bind,src=$fixture/password,dst=/fixture/password,readonly" \
    --mount "type=bind,src=$fixture/kms,dst=/fixture/kms,readonly" \
    -e HOME=/tmp -e MINIO_ROOT_USER_FILE=/fixture/access \
    -e MINIO_ROOT_PASSWORD_FILE=/fixture/password -e MINIO_KMS_SECRET_KEY_FILE=/fixture/kms \
    -e "MINIO_KMS_AUTO_ENCRYPTION=$auto_encryption" -e MINIO_BROWSER=off \
    minio/minio:RELEASE.2025-07-23T15-54-02Z server /data >/dev/null
  for ((attempt = 0; attempt < 60; attempt++)); do
    if docker exec "$container" curl --connect-timeout 2 --max-time 3 -fsS http://localhost:9000/minio/health/ready >/dev/null 2>&1; then
      return 0
    fi
    sleep .5
  done
  printf 'Private encrypted storage fixture did not become ready.\n' >&2
  return 1
}
probe() {
  docker run --rm --name "$probe_container" --label "drop.encryption.fixture=$job" \
    --network "$network" --memory 96m --memory-swap 96m --cpus .25 --pids-limit 32 --cap-drop ALL --read-only \
    --user 10001:10001 --security-opt no-new-privileges --tmpfs /tmp:size=8m,mode=1777 \
    --mount "type=bind,src=$fixture/access,dst=/fixture/access,readonly" \
    --mount "type=bind,src=$fixture/password,dst=/fixture/password,readonly" \
    --mount "type=bind,src=$fixture/payload.bin,dst=/fixture/payload.bin,readonly" \
    -v "$source_root/deploy/tests/s3_encryption_probe.py:/probe.py:ro" \
    -v "$source_root/ops/encrypt_existing.py:/encrypt_existing.py:ro" \
    --entrypoint python "$client_image" /probe.py "$1"
}
start_storage off
probe legacy-write
docker rm -f "$container" >/dev/null
start_storage on
probe encrypt-legacy
probe write
docker rm -f "$container" >/dev/null
# 先调整文件权限，同时兼容使用非root Docker用户的宿主环境。
chmod 600 "$fixture/kms"
printf 'fixture-v1:%s\n' "$(openssl rand -base64 32)" > "$fixture/kms"
chmod 444 "$fixture/kms"
if cmp -s "$fixture/kms" "$fixture/original-kms"; then
  printf 'The test did not generate a distinct master key.\n' >&2
  exit 2
fi
start_storage
probe wrong-key
docker rm -f "$container" >/dev/null
chmod 600 "$fixture/kms"
cp "$fixture/original-kms" "$fixture/kms"
chmod 444 "$fixture/kms"
start_storage
probe restored-key
printf 'Private plaintext migration, SSE-S3 roundtrip, different-key refusal and original-key recovery passed.\n'
