#!/usr/bin/env bash
# 备份恢复验收入口：在本次独立命名的数据库与存储资源中验证结构、对象及校验信息。
# 所有清理都限定到本次持有的资源，不能接收生产资源名作为删除目标。

# 只恢复到本次新建且名称唯一的验证资源，不覆盖生产数据库或卷。
set -euo pipefail
umask 077
restore_started="$(date +%s)"
deploy_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$deploy_dir"
compose=(docker compose)
[[ $# -eq 1 && "$1" = /* ]] || { printf 'Usage: restore-check.sh /absolute/backup-directory\n' >&2; exit 2; }
backup="$(cd -- "$1" && pwd -P)"
test -s "$backup/postgres.dump"
test -f "$backup/objects/manifest.jsonl"
expected="$(tr -d '\r\n' < "$backup/postgres.dump.sha256")"
actual="$(sha256sum "$backup/postgres.dump" | awk '{print $1}')"
[[ "$expected" =~ ^[a-f0-9]{64}$ && "$expected" = "$actual" ]] || { printf 'PostgreSQL archive checksum mismatch.\n' >&2; exit 1; }
database_bytes="$(tr -d '\r\n' < "$backup/database-size-bytes.txt")"
[[ "$database_bytes" =~ ^[0-9]+$ ]] || { printf 'Missing database size estimate.\n' >&2; exit 1; }
pg_available_kib="$("${compose[@]}" exec -T postgres sh -c 'df -Pk "$PGDATA"' | awk 'NR==2 {print $4}')"
[[ "$pg_available_kib" =~ ^[0-9]+$ ]] && (( pg_available_kib * 1024 >= database_bytes * 2 + 1073741824 )) || { printf 'Insufficient PostgreSQL volume headroom for isolated restore.\n' >&2; exit 1; }
isolated="drop_restore_$(date -u +%s)_$(openssl rand -hex 4)"
[[ "$isolated" =~ ^drop_restore_[0-9]+_[a-f0-9]{8}$ ]] || exit 2
temporary="$(mktemp -d /tmp/drop-restore-check.XXXXXXXX)"
created=0
cleanup() {
  local status=$?
  if (( created )); then
    # 目标名称由上方生成，不能来自用户输入或
    # 备份内容；清理范围不允许包含生产数据库或数据卷。
    if ! "${compose[@]}" exec -T postgres sh -c 'export PGPASSWORD="$(cat /run/secrets/database_password)"; exec dropdb -U "$POSTGRES_USER" -- "$1"' sh "$isolated"; then
      printf 'Verification DB cleanup failed; remove only %s after inspection.\n' "$isolated" >&2
      status=1
    fi
  fi
  rm -f -- "$temporary/artifacts.csv" "$temporary/counts.json" "$temporary/restore-check.json"
  rmdir -- "$temporary" 2>/dev/null || true
  exit "$status"
}
trap cleanup EXIT
printf 'Creating isolated verification database: %s\n' "$isolated"
"${compose[@]}" exec -T postgres sh -c 'export PGPASSWORD="$(cat /run/secrets/database_password)"; exec createdb -U "$POSTGRES_USER" -T template0 -- "$1"' sh "$isolated"
created=1
"${compose[@]}" exec -T postgres sh -c 'export PGPASSWORD="$(cat /run/secrets/database_password)"; exec pg_restore --exit-on-error --single-transaction --no-owner --no-acl -U "$POSTGRES_USER" -d "$1"' sh "$isolated" < "$backup/postgres.dump"
"${compose[@]}" exec -T postgres sh -c 'export PGPASSWORD="$(cat /run/secrets/database_password)"; exec psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1"' sh "$isolated" > "$temporary/artifacts.csv" <<'SQL'
COPY (SELECT object_key,sha256,size FROM artifacts WHERE status='ready' ORDER BY id) TO STDOUT WITH CSV HEADER;
SQL
"${compose[@]}" exec -T postgres sh -c 'export PGPASSWORD="$(cat /run/secrets/database_password)"; exec psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1"' sh "$isolated" > "$temporary/counts.json" <<'SQL'
SELECT 'SELECT json_build_object(' || string_agg(format('%L,(SELECT count(*) FROM public.%I)',tablename,tablename),',') || ',''schema_version'',(SELECT max(version) FROM public.schema_migrations));' FROM pg_tables WHERE schemaname='public'
\gexec
SQL
"${compose[@]}" run --rm --no-deps -T --user "$(id -u):$(id -g)" --entrypoint python -e RESTORE_CHECK_STARTED="$restore_started" \
  --volume "$deploy_dir/backup_metadata.py:/opt/drop-backup/backup_metadata.py:ro" \
  --volume "$backup:/backup:ro" --volume "$temporary:/verification:rw" backup-client - <<'PY'
import csv, hashlib, json, os, pathlib, re, secrets, sys, time, urllib3
from datetime import datetime, timezone
from minio import Minio
from minio.deleteobjects import DeleteObject
sys.path.insert(0, '/opt/drop-backup')
from backup_metadata import validate_metadata, verify_metadata

def setting(name, default=''):
    path = os.getenv(name + '_FILE')
    return pathlib.Path(path).read_text().strip() if path else os.getenv(name, default)

root = pathlib.Path('/backup')
manifest = {}
for line in (root / 'objects' / 'manifest.jsonl').read_text().splitlines():
    row = json.loads(line)
    expected_file = hashlib.sha256(row['key'].encode('utf-8')).hexdigest() + '.bin'
    if row['file'] != expected_file or not re.fullmatch(r'[a-f0-9]{64}', row['sha256']):
        raise RuntimeError('Invalid object backup manifest')
    source = root / 'objects' / 'data' / expected_file
    if source.is_symlink() or not source.is_file() or source.stat().st_size != row['size']:
        raise RuntimeError('Missing or unsafe object backup file')
    with source.open('rb') as content:
        digest = hashlib.file_digest(content, 'sha256').hexdigest()
    if digest != row['sha256']:
        raise RuntimeError('Object backup checksum mismatch')
    if row['key'] in manifest:
        raise RuntimeError('Duplicate object key in backup')
    row['metadata'] = validate_metadata(row.get('metadata'))
    manifest[row['key']] = row
referenced = 0
with pathlib.Path('/verification/artifacts.csv').open(newline='') as source:
    for artifact in csv.DictReader(source):
        row = manifest.get(artifact['object_key'])
        if not row or row['sha256'] != artifact['sha256'] or row['size'] != int(artifact['size']):
            raise RuntimeError('Restored database references an absent or different object; backup is inconsistent')
        referenced += 1
counts = json.loads(pathlib.Path('/verification/counts.json').read_text())
if not counts.get('schema_version'):
    raise RuntimeError('Restored schema migration is missing')
endpoint = setting('S3_ENDPOINT', 'minio:9000')
secure = endpoint.startswith('https://')
client = Minio(endpoint.removeprefix('https://').removeprefix('http://'), access_key=setting('S3_ACCESS_KEY'), secret_key=setting('S3_SECRET_KEY'), secure=secure,
    http_client=urllib3.PoolManager(timeout=urllib3.Timeout(connect=5, read=60), retries=urllib3.Retry(total=2, backoff_factor=0.5)))
bucket = f'drop-restore-check-{int(time.time())}-{secrets.token_hex(4)}'
if not re.fullmatch(r'drop-restore-check-[0-9]+-[a-f0-9]{8}', bucket) or client.bucket_exists(bucket):
    raise RuntimeError('Verification bucket is not a fresh isolated target')
print(f'Creating isolated verification bucket: {bucket}', flush=True)
made_bucket = False
try:
    client.make_bucket(bucket)
    made_bucket = True
    for key, row in manifest.items():
        client.fput_object(bucket, key, str(root / 'objects' / 'data' / row['file']),
                           content_type=row.get('content_type') or 'application/octet-stream', metadata=row['metadata'])
        response = client.get_object(bucket, key)
        digest = hashlib.sha256()
        size = 0
        try:
            for chunk in response.stream(1024 * 1024, decode_content=False):
                digest.update(chunk)
                size += len(chunk)
        finally:
            response.close()
            response.release_conn()
        if digest.hexdigest() != row['sha256'] or size != row['size']:
            raise RuntimeError('Isolated S3 object restore round-trip mismatch')
        restored = client.stat_object(bucket, key)
        verify_metadata(row['metadata'], restored.metadata)
        if restored.content_type != (row.get('content_type') or 'application/octet-stream'):
            raise RuntimeError('Isolated S3 restore content type mismatch')
        # 小主机上每次最多保留一个已恢复对象，避免校验过程耗尽临时磁盘。
        client.remove_object(bucket, key)
finally:
    if made_bucket:
        errors = list(client.remove_objects(bucket, (DeleteObject(item.object_name) for item in client.list_objects(bucket, recursive=True))))
        if errors:
            raise RuntimeError('Verification bucket object cleanup failed')
        client.remove_bucket(bucket)
report = {'verified_at': datetime.now(timezone.utc).isoformat(), 'postgres_restore': 'passed',
    'database_counts': counts, 'referenced_artifacts_verified': referenced,
    'object_files_verified': len(manifest), 'object_metadata_verified': True,
    'isolated_s3_roundtrip': 'passed', 'production_resources_modified': False,
    'restore_duration_seconds': round(time.time()-int(os.environ['RESTORE_CHECK_STARTED']),3)}
pathlib.Path('/verification/restore-check.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
PY
cp -- "$temporary/restore-check.json" "$backup/restore-check.json"
printf 'Restore check passed; temporary database and bucket are cleaned automatically.\n'
