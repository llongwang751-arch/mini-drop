#!/usr/bin/env bash
# 当前源码的真实 PROC_CPU Runner → MinIO → Analyzer 的隔离门禁。
# 该链路不包含 Go HTTP 建任务、Agent gRPC 或浏览器；控制面由独立测试覆盖。
set -euo pipefail
umask 077
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
cd "$root"
owner="$(openssl rand -hex 8)"
prefix="drop-quality-chain-$owner"
relative=".local/$prefix"
private="$root/$relative"
collector="$prefix-collector"
image="${DROP_CORE_TEST_IMAGE:-drop-core-test}"
docker() { command timeout --signal=TERM --kill-after=5s 120s docker "$@"; }
binary_mount=()
binary_directory=/src/build
if [[ -n "${DROP_CORE_BINARIES_DIR:-}" ]]; then
  host_binaries="$(realpath -- "$DROP_CORE_BINARIES_DIR")"
  [[ -x "$host_binaries/drop_runner_probe" && -x "$host_binaries/drop_workload" ]] || { echo 'Current core fixture binaries missing' >&2; exit 2; }
  binary_mount=(--mount "type=bind,src=$host_binaries,dst=/quality-build,readonly")
  binary_directory=/quality-build
fi
mkdir -p -- "$root/.local"
mkdir -- "$private"
mkdir -- "$private/capture"
chown 10001:10001 "$private/capture"
chmod 700 "$private/capture"
cleanup() {
  local status=$? marker
  trap - EXIT INT TERM
  marker="$(docker inspect --format '{{index .Config.Labels "drop.quality.fixture"}}' "$collector" 2>/dev/null || true)"
  if [[ "$marker" == "$prefix" ]]; then docker rm -f "$collector" >/dev/null 2>&1 || true; fi
  # 私有 RAW 留在显式 ignored 的目录供失败审计，不写入公开证据或 CI artifact。
  exit "$status"
}
if docker container inspect "$collector" >/dev/null 2>&1; then echo 'Refusing existing quality collector' >&2; exit 2; fi
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker run --rm --name "$collector" --network none --label "drop.quality.fixture=$prefix" \
  --user 10001:10001 --read-only --cap-drop ALL --security-opt no-new-privileges \
  --memory=512m --memory-swap=512m --cpus=.75 --pids-limit=64 --tmpfs /tmp:rw,size=64m,mode=1777 \
  --mount "type=bind,src=$private/capture,dst=/capture" \
  "${binary_mount[@]}" -e "DROP_QUALITY_BINARY_DIRECTORY=$binary_directory" \
  -e ALLOW_HOST_PROCESSES=true -e ALLOWED_TARGET_UIDS=10001 \
  --entrypoint /bin/bash "$image" -ceu '
    umask 077
    "$DROP_QUALITY_BINARY_DIRECTORY/drop_workload" >/tmp/workload.log 2>&1 &
    target=$!
    trap "kill $target 2>/dev/null || true; wait $target 2>/dev/null || true" EXIT
    sleep 1
    printf "{\"kind\":\"PROC_CPU\",\"parameters\":{\"pid\":%s,\"scope\":\"process\",\"duration_seconds\":3,\"frequency_hz\":10}}" "$target" >/tmp/request.json
    "$DROP_QUALITY_BINARY_DIRECTORY/drop_runner_probe" /src/contracts/task-kinds.json /tmp/request.json /tmp/live-profile >/capture/receipt.json
    cp /tmp/live-profile/profile.folded /capture/profile.folded
    sha256sum "$DROP_QUALITY_BINARY_DIRECTORY/drop_runner_probe" "$DROP_QUALITY_BINARY_DIRECTORY/drop_workload" >/capture/binaries.sha256
  ' >"$private/collector.private.log" 2>&1
TEST_LIVE_CAPTURE_DIR="$relative/capture" \
  bash analyzer/scripts/run-isolated-integration.sh discover -s tests -p test_live_collection_chain.py -v \
  >"$private/analyzer.stdout.private.log" 2>"$private/analyzer.stderr.private.log"
# unittest -v 的进度写 stderr，print 收据写 stdout；两路合并会把标记粘在进度行尾。
# 独立解析器只接受 stdout 的唯一完整标记行，不能用任意 substring 误认污染日志。
python3 scripts/quality/protocol/chain_receipt.py "$private/analyzer.stdout.private.log" "$private/receipt.json"
