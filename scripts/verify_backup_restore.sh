#!/usr/bin/env bash
set -euo pipefail

# Git Bash rewrites container-internal paths such as /bin/sh unless path
# conversion is disabled. This variable is ignored by regular Linux shells.
export MSYS_NO_PATHCONV=1

# Destructive only to temporary verification resources. Production data is
# dumped/read, never overwritten. Run from the repository root on Linux.
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
output="${BACKUP_VERIFY_OUTPUT:-reports/acceptance/backup-${stamp}}"
restore_db="mini_drop_restore_verify_${RANDOM}_$$"
restore_bucket="mini-drop-restore-verify-${RANDOM}-$$"
mkdir -p "$output/minio"

command -v docker >/dev/null
command -v sha256sum >/dev/null
docker compose ps --status running postgres minio >/dev/null

cleanup() {
  docker compose exec -T postgres sh -ec \
    'dropdb -U "$POSTGRES_USER" --if-exists "$1"' sh "$restore_db" >/dev/null 2>&1 || true
  if [[ -n "${network:-}" ]]; then
    docker run --rm --network "$network" --entrypoint /bin/sh minio/mc -c \
      "mc alias set local http://minio:9000 '$MINIO_ACCESS_KEY' '$MINIO_SECRET_KEY' >/dev/null && mc rb --force local/$restore_bucket" \
      >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "[1/6] Dumping PostgreSQL..."
docker compose exec -T postgres sh -ec \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$output/postgres.dump"
test -s "$output/postgres.dump"

count_sql="SELECT json_build_object('tasks',(SELECT count(*) FROM tasks),'agents',(SELECT count(*) FROM agents),'artifacts',(SELECT count(*) FROM artifacts),'diagnosis_sessions',(SELECT count(*) FROM diagnosis_sessions));"
docker compose exec -T postgres sh -ec \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "$1"' sh "$count_sql" \
  >"$output/postgres-before.json"

echo "[2/6] Restoring into temporary PostgreSQL database..."
docker compose exec -T postgres sh -ec \
  'createdb -U "$POSTGRES_USER" "$1"' sh "$restore_db"
docker compose exec -T postgres sh -ec \
  'pg_restore -U "$POSTGRES_USER" -d "$1" --no-owner --no-privileges' sh "$restore_db" \
  <"$output/postgres.dump"
docker compose exec -T postgres sh -ec \
  'psql -U "$POSTGRES_USER" -d "$1" -Atc "$2"' sh "$restore_db" "$count_sql" \
  >"$output/postgres-after.json"
cmp -s "$output/postgres-before.json" "$output/postgres-after.json"

echo "[3/6] Discovering Compose network and mirroring MinIO..."
minio_container="$(docker compose ps -q minio)"
network="$(docker inspect "$minio_container" --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}')"
MINIO_ACCESS_KEY="${MINIO_ACCESS_KEY:-mini_drop}"
MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-mini_drop_secret}"
MINIO_BUCKET="${MINIO_BUCKET:-mini-drop}"
output_abs="$(cd "$output" && pwd)"
docker run --rm --network "$network" -v "$output_abs/minio:/backup" --entrypoint /bin/sh minio/mc -ec \
  "mc alias set local http://minio:9000 '$MINIO_ACCESS_KEY' '$MINIO_SECRET_KEY' >/dev/null; mc mirror --overwrite local/$MINIO_BUCKET /backup"

echo "[4/6] Restoring MinIO objects into a temporary bucket..."
docker run --rm --network "$network" -v "$output_abs/minio:/backup:ro" --entrypoint /bin/sh minio/mc -ec \
  "mc alias set local http://minio:9000 '$MINIO_ACCESS_KEY' '$MINIO_SECRET_KEY' >/dev/null; mc mb --ignore-existing local/$restore_bucket >/dev/null; mc mirror --overwrite /backup local/$restore_bucket"

echo "[5/6] Comparing MinIO object counts..."
source_count="$(docker run --rm --network "$network" --entrypoint /bin/sh minio/mc -ec "mc alias set local http://minio:9000 '$MINIO_ACCESS_KEY' '$MINIO_SECRET_KEY' >/dev/null; mc ls --recursive --json local/$MINIO_BUCKET | wc -l")"
restore_count="$(docker run --rm --network "$network" --entrypoint /bin/sh minio/mc -ec "mc alias set local http://minio:9000 '$MINIO_ACCESS_KEY' '$MINIO_SECRET_KEY' >/dev/null; mc ls --recursive --json local/$restore_bucket | wc -l")"
[[ "$source_count" =~ ^[0-9]+$ ]]
[[ "$restore_count" =~ ^[0-9]+$ ]]
test "$source_count" -eq "$restore_count"

echo "[6/6] Writing checksums and evidence summary..."
(cd "$output" && find . -type f ! -name SHA256SUMS.txt -print0 | sort -z | xargs -0 sha256sum >SHA256SUMS.txt)
printf '{"status":"PASS","verified_at":"%s","postgres_restore_database":"%s","minio_object_count":%s}\n' \
  "$stamp" "$restore_db" "$source_count" >"$output/result.json"
echo "PASS: PostgreSQL rows and MinIO object counts survived backup/restore. Evidence: $output"
