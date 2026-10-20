#!/usr/bin/env sh
set -eu

# 在具备Docker的宿主上从仓库根目录运行；全部资源都是本次一次性夹具，
# 不发布端口、不使用持久卷，也不连接应用数据库。
context_root=$(pwd)
prefix=${ANALYZER_TEST_PREFIX:-drop-analyzer-test-$(openssl rand -hex 8)}
case "$prefix" in
  ''|*[!a-zA-Z0-9_-]*) echo 'Invalid isolated test prefix' >&2; exit 2 ;;
esac
if [ "${#prefix}" -gt 80 ]; then
  echo 'Isolated test prefix exceeds bound' >&2
  exit 2
fi
network=$prefix-network
database=$prefix-postgres
runner=$prefix-runner
storage=$prefix-minio
image=${ANALYZER_TEST_IMAGE:-drop-analyzer:build}
if [ "$#" -eq 0 ]; then
  set -- discover -s tests -v
fi
test_password=$(openssl rand -hex 24)

cleanup() {
  result=$?
  trap - EXIT INT TERM
  for name in "$runner" "$database" "$storage"; do
    owner=$(docker inspect --format '{{index .Config.Labels "drop.analyzer.fixture"}}' "$name" 2>/dev/null || true)
    if [ "$owner" = "$prefix" ]; then
      docker rm -f "$name" >/dev/null 2>&1 || true
    fi
  done
  owner=$(docker network inspect --format '{{index .Labels "drop.analyzer.fixture"}}' "$network" 2>/dev/null || true)
  if [ "$owner" = "$prefix" ]; then
    docker network rm "$network" >/dev/null 2>&1 || true
  fi
  exit "$result"
}
# 注册清理逻辑前先拒绝资源名冲突，创建失败不能导致
# 删除上一次运行或其他项目持有的资源。
for name in "$runner" "$database" "$storage"; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    echo 'Refusing to reuse an existing isolated test container' >&2
    exit 2
  fi
done
if docker network inspect "$network" >/dev/null 2>&1; then
  echo 'Refusing to reuse an existing isolated test network' >&2
  exit 2
fi
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker network create --internal --label "drop.analyzer.fixture=$prefix" "$network" >/dev/null
docker run --detach --name "$database" --network "$network" \
  --label "drop.analyzer.fixture=$prefix" \
  --memory=384m --memory-swap=384m --cpus=0.5 --pids-limit=64 \
  --tmpfs /var/lib/postgresql/data:rw,size=256m \
  -e POSTGRES_USER=analyzer_test -e POSTGRES_DB=analyzer_test -e POSTGRES_PASSWORD="$test_password" \
  postgres:17.11-alpine >/dev/null
docker run --detach --name "$storage" --network "$network" \
  --label "drop.analyzer.fixture=$prefix" \
  --memory=256m --memory-swap=256m --cpus=0.5 --pids-limit=64 --tmpfs /data:rw,size=64m \
  -e MINIO_ROOT_USER=analyzer-test -e MINIO_ROOT_PASSWORD="$test_password" \
  minio/minio:RELEASE.2025-07-23T15-54-02Z server /data >/dev/null

attempt=0
until docker exec "$database" pg_isready -U analyzer_test -d analyzer_test >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo 'Isolated PostgreSQL did not become ready' >&2
    exit 1
  fi
  sleep 1
done

attempt=0
until docker exec "$storage" curl --connect-timeout 2 --max-time 3 -fsS http://localhost:9000/minio/health/ready >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo 'Isolated MinIO did not become ready' >&2
    exit 1
  fi
  sleep 1
done

docker run --rm --name "$runner" --network "$network" \
  --label "drop.analyzer.fixture=$prefix" \
  --memory=768m --memory-swap=768m --cpus=1 --pids-limit=128 \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,size=384m,mode=1777 \
  --mount "type=bind,src=$context_root,dst=/workspace,readonly" --workdir /workspace/analyzer \
  -e "TEST_DATABASE_URL=postgresql://analyzer_test:$test_password@$database/analyzer_test" \
  -e "TEST_S3_ENDPOINT=$storage:9000" -e TEST_S3_ACCESS_KEY=analyzer-test -e "TEST_S3_SECRET_KEY=$test_password" \
  -e ANALYZER_METRICS_PORT=0 \
  "$image" python -m unittest "$@"
