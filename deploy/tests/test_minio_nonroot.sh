#!/usr/bin/env bash
# Isolated MinIO ownership/read-only compatibility test. Never mounts an
# existing production volume, joins its network, or stops an existing service.
set -euo pipefail
umask 077
minio_image="${MINIO_TEST_IMAGE:-minio/minio:RELEASE.2025-07-23T15-54-02Z}"
client_image="${MINIO_TEST_CLIENT_IMAGE:-drop-analyzer:build}"
init_image="${MINIO_TEST_INIT_IMAGE:-ubuntu:24.04}"
token="$(date -u +%s)-$(openssl rand -hex 4)"
name="drop-minio-nonroot-$token"
volume="$name-data"
network="$name-net"
temporary="$(mktemp -d /tmp/drop-minio-nonroot.XXXXXXXX)"
made_volume=0 made_network=0 made_container=0
cleanup() {
  local status=$?
  if (( made_container )); then docker rm -f "$name" >/dev/null || status=1; fi
  if (( made_network )); then docker network rm "$network" >/dev/null || status=1; fi
  if (( made_volume )); then
    # Prefix plus an unguessable per-run label must match before deleting our
    # own fixture. No supplied or discovered production volume is eligible.
    local label
    label="$(docker volume inspect --format '{{index .Labels "drop.fixture.token"}}' "$volume")"
    if [[ "$volume" = "drop-minio-nonroot-$token-data" && "$label" = "$token" ]]; then
      docker volume rm "$volume" >/dev/null || status=1
    else
      printf 'Refusing cleanup of an unrecognized volume: %s\n' "$volume" >&2
      status=1
    fi
  fi
  rm -f -- "$temporary/password"
  rmdir -- "$temporary" 2>/dev/null || true
  exit "$status"
}
trap cleanup EXIT
openssl rand -hex 24 > "$temporary/password"
chmod 444 "$temporary/password"
docker volume create --label drop.fixture=minio-nonroot --label "drop.fixture.token=$token" "$volume" >/dev/null
made_volume=1
docker network create --internal --label drop.fixture=minio-nonroot "$network" >/dev/null
made_network=1
printf 'Initializing only new fixture volume: %s\n' "$volume"
docker run --rm --network none --read-only --cap-drop ALL --cap-add CHOWN \
  --security-opt no-new-privileges:true \
  --mount "type=volume,source=$volume,target=/fixture,volume-nocopy" \
  --entrypoint /bin/sh "$init_image" -c 'set -eu; chmod 700 /fixture; chown 10001:10001 /fixture; stat -c %u:%g:%a /fixture'
docker run -d --name "$name" --network "$network" --read-only --user 10001:10001 \
  --cap-drop ALL --security-opt no-new-privileges:true --memory 256m --cpus 0.35 \
  --tmpfs /tmp:size=32m,mode=1777 \
  --mount "type=volume,source=$volume,target=/data,volume-nocopy" \
  --mount "type=bind,source=$temporary/password,target=/run/secrets/password,readonly" \
  -e HOME=/tmp -e MINIO_ROOT_USER=drop-nonroot-fixture \
  -e MINIO_ROOT_PASSWORD_FILE=/run/secrets/password -e MINIO_BROWSER=off \
  "$minio_image" server /data --console-address :9001 >/dev/null
made_container=1
ready=0
for (( attempt=0; attempt<60; attempt++ )); do
  if docker exec "$name" curl --max-time 2 -fsS http://127.0.0.1:9000/minio/health/live >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
if (( !ready )); then docker logs --tail 40 "$name"; printf 'Non-root MinIO did not become healthy.\n' >&2; exit 1; fi
docker exec "$name" sh -c '
set -eu
id
cap_value=
while read -r field value rest; do
  if [ "$field" = CapEff: ]; then cap_value="$value"; break; fi
done < /proc/self/status
[ "$cap_value" = 0000000000000000 ]
printf "CapEff: %s\n" "$cap_value"
stat -c %u:%g:%a /data /data/.minio.sys
'
docker run --rm -i --network "$network" --read-only --user 10001:10001 --cap-drop ALL \
  --security-opt no-new-privileges:true --memory 128m --cpus 0.25 \
  --tmpfs /tmp:size=16m,mode=1777 \
  --mount "type=bind,source=$temporary/password,target=/run/secrets/password,readonly" \
  -e "TEST_MINIO_ENDPOINT=$name:9000" --entrypoint python "$client_image" - <<'PY'
import hashlib, io, json, os, pathlib, urllib3
from minio import Minio

client = Minio(os.environ['TEST_MINIO_ENDPOINT'], access_key='drop-nonroot-fixture',
    secret_key=pathlib.Path('/run/secrets/password').read_text().strip(), secure=False,
    http_client=urllib3.PoolManager(timeout=urllib3.Timeout(connect=5, read=20), retries=urllib3.Retry(total=1)))
bucket = 'nonroot-fixture'
client.make_bucket(bucket)
payloads = {'ownership-check.txt': b'explicit isolated nonroot MinIO verification\n',
            'multipart-check.bin': bytes(range(256)) * (7 * 1024 * 1024 // 256)}
report = []
for key, data in payloads.items():
    client.put_object(bucket, key, io.BytesIO(data), len(data), part_size=5 * 1024 * 1024)
    response = client.get_object(bucket, key)
    try:
        actual = response.read()
    finally:
        response.close()
        response.release_conn()
    assert actual == data, 'S3 roundtrip content mismatch'
    assert client.stat_object(bucket, key).size == len(data), 'S3 size mismatch'
    report.append({'key': key, 'bytes': len(data), 'sha256': hashlib.sha256(actual).hexdigest()})
assert sorted(item.object_name for item in client.list_objects(bucket)) == sorted(payloads), 'List mismatch'
for key in payloads:
    client.remove_object(bucket, key)
client.remove_bucket(bucket)
print(json.dumps({'nonroot_s3_roundtrip': 'passed', 'uid': 10001, 'capabilities': 'none',
    'read_only_root': True, 'objects': report, 'production_volumes_mounted': False}, indent=2))
PY
printf 'Non-root MinIO compatibility passed; only owned fixture resources are cleaned.\n'
