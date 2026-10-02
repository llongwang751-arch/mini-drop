#!/usr/bin/env bash
set -euo pipefail

# Native-Linux acceptance for the real eBPF end-to-end path. This script must
# be run on Ubuntu/Linux, not inside WSL2 backed by Docker Desktop.
API_BASE="${MINI_DROP_API_BASE:-http://127.0.0.1/api}"
AGENT_ID="${NATIVE_AGENT_ID:-agent_native_cpp}"
DURATION="${EBPF_VERIFY_DURATION:-15}"
API_KEY="${MINI_DROP_API_KEY:-}"

command -v docker >/dev/null
command -v curl >/dev/null
command -v jq >/dev/null
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This acceptance script requires native Linux." >&2
  exit 2
fi

headers=(-H 'Content-Type: application/json')
if [[ -n "$API_KEY" ]]; then headers+=(-H "X-API-Key: $API_KEY"); fi

docker compose --profile native-agent up -d --build native-agent
echo "[1/5] Waiting for native C++ Agent registration..."
for _ in $(seq 1 40); do
  if curl -fsS "${headers[@]}" "$API_BASE/agents" | jq -e \
      --arg id "$AGENT_ID" '.data.items[] | select(.id == $id and .status == "ONLINE")' >/dev/null; then
    break
  fi
  sleep 2
done
curl -fsS "${headers[@]}" "$API_BASE/agents" | jq -e \
  --arg id "$AGENT_ID" '.data.items[] | select(.id == $id and .status == "ONLINE")' >/dev/null

echo "[2/5] Checking tracefs and block tracepoints inside the Agent..."
docker compose exec -T native-agent sh -ec '
  test -r /sys/kernel/tracing/available_events || test -r /sys/kernel/debug/tracing/available_events
  bpftrace -l "tracepoint:block:block_rq_*" | grep -q block_rq_issue
'

echo "[3/5] Starting real disk IO and creating the eBPF task..."
docker compose exec -T -d native-agent sh -c \
  'dd if=/dev/zero of=/tmp/mini-drop-ebpf-load.bin bs=1M count=2048 oflag=direct conv=fsync; rm -f /tmp/mini-drop-ebpf-load.bin'
payload=$(jq -nc --arg agent "$AGENT_ID" --argjson duration "$DURATION" '{
  name:"native-ebpf-acceptance", agent_id:$agent, target_pid:1,
  collector_type:"ebpf_io", sample_rate:99, duration_sec:$duration, options:{}
}')
task_id=$(curl -fsS "${headers[@]}" -X POST "$API_BASE/tasks" -d "$payload" | jq -er '.data.task_id')
echo "task_id=$task_id"

echo "[4/5] Waiting for DONE..."
status=""
for _ in $(seq 1 90); do
  response=$(curl -fsS "${headers[@]}" "$API_BASE/tasks/$task_id")
  status=$(jq -r '.data.status' <<<"$response")
  [[ "$status" == "DONE" || "$status" == "FAILED" ]] && break
  sleep 2
done
if [[ "$status" != "DONE" ]]; then
  curl -fsS "${headers[@]}" "$API_BASE/tasks/$task_id" | jq . >&2
  docker compose logs --tail=120 native-agent >&2
  exit 1
fi

echo "[5/5] Verifying traceable artifact and non-zero IO distribution..."
artifacts=$(curl -fsS "${headers[@]}" "$API_BASE/tasks/$task_id/artifacts")
jq -e '.data[] | select(.artifact_type == "ebpf_metrics")' <<<"$artifacts" >/dev/null
metrics=$(curl -fsS "${headers[@]}" "$API_BASE/tasks/$task_id/artifacts/ebpf_metrics/content")
jq -e '.total_samples > 0 and (.io_latency_us | length) > 0' <<<"$metrics" >/dev/null
echo "$metrics" | jq .
echo "PASS: native eBPF task reached DONE with a non-empty, traceable artifact."
