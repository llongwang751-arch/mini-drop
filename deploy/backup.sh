#!/usr/bin/env bash
# Online logical backup. Never stops services or copies a live database volume.
set -euo pipefail
umask 077
deploy_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$deploy_dir"
compose=(docker compose)
backup_root="${1:-/opt/drop-backups}"
[[ "$backup_root" = /* ]] || { printf 'Backup destination must be absolute.\n' >&2; exit 2; }
mkdir -p -- "$backup_root"
backup_root="$(cd -- "$backup_root" && pwd -P)"
[[ "$backup_root" != / && "$(stat -c %u -- "$backup_root")" = "$(id -u)" && "$(stat -c %a -- "$backup_root")" = 700 ]] || {
  printf 'Backup destination must be an owned directory with mode 0700; no existing directory permissions were changed.\n' >&2
  exit 2
}
exec 9>"$backup_root/.backup.lock"
flock -n 9 || { printf 'Another Drop backup is running.\n' >&2; exit 1; }
stamp="$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 3)"
job="$backup_root/drop-$stamp.partial"
mkdir -m 700 -- "$job"
trap 'status=$?; if (( status != 0 )); then printf "Backup failed; incomplete evidence remains at %s\n" "$job" >&2; fi' EXIT
available_kib="$(df -Pk -- "$backup_root" | awk 'NR==2 {print $4}')"
database_bytes="$("${compose[@]}" exec -T postgres sh -c 'export PGPASSWORD="$(cat /run/secrets/database_password)"; exec psql -X -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT pg_database_size(current_database())"')"
[[ "$database_bytes" =~ ^[0-9]+$ ]] || { printf 'Could not estimate database size.\n' >&2; exit 1; }
(( available_kib * 1024 >= database_bytes * 3 + 1073741824 )) || { printf 'Insufficient disk: reserve 3x database size plus 1 GiB before backup.\n' >&2; exit 1; }
printf '%s\n' "$database_bytes" > "$job/database-size-bytes.txt"
printf 'Creating online PostgreSQL custom archive...\n'
"${compose[@]}" exec -T postgres sh -c 'export PGPASSWORD="$(cat /run/secrets/database_password)"; exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl --lock-wait-timeout=10s' > "$job/postgres.dump"
test -s "$job/postgres.dump"
"${compose[@]}" exec -T postgres pg_dump --version > "$job/postgres-version.txt"
sha256sum "$job/postgres.dump" | awk '{print $1}' > "$job/postgres.dump.sha256"
printf 'Backing up MinIO through its S3 API (not the live data directory)...\n'
"${compose[@]}" run --rm --no-deps -T --user "$(id -u):$(id -g)" --entrypoint python \
  --volume "$deploy_dir/backup_metadata.py:/opt/drop-backup/backup_metadata.py:ro" \
  --volume "$job:/backup:rw" backup-client - <<'PY'
import hashlib, json, os, pathlib, sys, urllib3
from datetime import datetime, timezone
from minio import Minio
sys.path.insert(0, '/opt/drop-backup')
from backup_metadata import capture_metadata

def setting(name, default=''):
    path = os.getenv(name + '_FILE')
    return pathlib.Path(path).read_text().strip() if path else os.getenv(name, default)

endpoint = setting('S3_ENDPOINT', 'minio:9000')
secure = endpoint.startswith('https://')
endpoint = endpoint.removeprefix('https://').removeprefix('http://')
client = Minio(endpoint, access_key=setting('S3_ACCESS_KEY'), secret_key=setting('S3_SECRET_KEY'), secure=secure,
    http_client=urllib3.PoolManager(timeout=urllib3.Timeout(connect=5, read=60), retries=urllib3.Retry(total=2, backoff_factor=0.5)))
bucket = setting('S3_BUCKET', 'drop-artifacts')
root = pathlib.Path('/backup')
data = root / 'objects' / 'data'
data.mkdir(parents=True, mode=0o700)
count = total = 0
with (root / 'objects' / 'manifest.jsonl').open('x', encoding='utf-8') as manifest:
    for item in client.list_objects(bucket, recursive=True):
        # Object names are untrusted paths. Store by digest, retaining the exact
        # S3 name only in the manifest; no key becomes a filesystem pathname.
        info = client.stat_object(bucket, item.object_name)
        free_bytes = os.statvfs(root).f_bavail * os.statvfs(root).f_frsize
        database_bytes = int((root / 'database-size-bytes.txt').read_text())
        if free_bytes < info.size + database_bytes + 1024 ** 3:
            raise RuntimeError('Backup stopped before consuming the database-restore and 1 GiB free-space reserve')
        version = info.version_id if info.version_id and info.version_id != 'null' else None
        file_id = hashlib.sha256(item.object_name.encode('utf-8')).hexdigest()
        destination = data / (file_id + '.bin')
        response = client.get_object(bucket, item.object_name, version_id=version)
        digest = hashlib.sha256()
        size = 0
        try:
            with destination.open('xb') as output:
                for chunk in response.stream(1024 * 1024, decode_content=False):
                    size += len(chunk)
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        finally:
            response.close()
            response.release_conn()
        after = client.stat_object(bucket, item.object_name, version_id=version)
        if size != info.size or info.etag != after.etag or size != after.size:
            raise RuntimeError('An object changed during backup; rerun for a consistent backup')
        metadata = capture_metadata(info.metadata)
        if metadata != capture_metadata(after.metadata):
            raise RuntimeError('Object metadata changed during backup')
        manifest.write(json.dumps({'key': item.object_name, 'file': file_id + '.bin', 'size': size,
            'sha256': digest.hexdigest(), 'etag': info.etag, 'version_id': version,
            'content_type': info.content_type, 'metadata': metadata,
            'last_modified': info.last_modified.isoformat()}, ensure_ascii=False) + '\n')
        count += 1
        total += size
    manifest.flush()
    os.fsync(manifest.fileno())
(root / 'backup.json').write_text(json.dumps({'schema_version': 2, 'created_at': datetime.now(timezone.utc).isoformat(),
    'bucket': bucket, 'objects': count, 'object_bytes': total,
    'consistency': 'PostgreSQL snapshot first, then immutable object keys; restored artifact hashes must all match',
    'includes_secrets': False}, indent=2) + '\n')
print(f'Object backup: {count} objects, {total} bytes')
PY
printf 'Verifying a real restore into an isolated database and S3 bucket...\n'
bash "$deploy_dir/restore-check.sh" "$job"
final="$backup_root/drop-$stamp"
mv -- "$job" "$final"
trap - EXIT
printf 'Verified backup complete: %s\n' "$final"
