#!/usr/bin/env bash
set -euo pipefail

API_BASE="${MINI_DROP_API_BASE:-http://127.0.0.1/api}"
API_KEY="${MINI_DROP_API_KEY:-}"
TASK_COUNT="${MULTI_REPLICA_TASK_COUNT:-12}"
TIMEOUT="${MULTI_REPLICA_TIMEOUT:-180}"
output="${MULTI_REPLICA_OUTPUT:-reports/acceptance/multi-replica-$(date -u +%Y%m%dT%H%M%SZ)}"
mkdir -p "$output"
headers=(-H 'Content-Type: application/json')
if [[ -n "$API_KEY" ]]; then headers+=(-H "X-API-Key: $API_KEY"); fi

cleanup() {
  docker compose up -d --scale apiserver=1 --scale analyzer=1 --scale diagnosis-worker=1 >/dev/null 2>&1 || true
}
trap cleanup EXIT

command -v docker >/dev/null
command -v curl >/dev/null
command -v jq >/dev/null

echo "[1/5] Scaling stateless API and leased workers to two replicas..."
docker compose up -d --scale apiserver=2 --scale analyzer=2 --scale diagnosis-worker=2
for service in apiserver analyzer diagnosis-worker; do
  test "$(docker compose ps -q "$service" | wc -l)" -eq 2
done

echo "[2/5] Selecting an online Agent..."
agent_id="$(curl -fsS "${headers[@]}" "$API_BASE/agents?limit=100" | jq -er '.data.items[] | select(.status=="ONLINE") | .id' | head -n1)"

echo "[3/5] Creating $TASK_COUNT concurrent tasks..."
: >"$output/task-ids.txt"
for index in $(seq 1 "$TASK_COUNT"); do
  jq -nc --arg name "multi-replica-$index" --arg agent "$agent_id" '{name:$name,agent_id:$agent,target_pid:1,collector_type:"sys_metrics",sample_rate:10,duration_sec:2,options:{}}' |
    curl -fsS "${headers[@]}" -X POST "$API_BASE/tasks" -d @- |
    jq -er '.data.task_id' >>"$output/task-ids.txt" &
done
wait
test "$(wc -l <"$output/task-ids.txt")" -eq "$TASK_COUNT"

echo "[4/5] Waiting for every task to reach DONE..."
deadline=$((SECONDS + TIMEOUT))
while (( SECONDS < deadline )); do
  done_count=0
  failed=0
  while read -r task_id; do
    status="$(curl -fsS "${headers[@]}" "$API_BASE/tasks/$task_id" | jq -r '.data.status')"
    [[ "$status" == "DONE" ]] && done_count=$((done_count + 1))
    [[ "$status" == "FAILED" || "$status" == "CANCELLED" ]] && failed=$((failed + 1))
  done <"$output/task-ids.txt"
  (( failed == 0 )) || { echo "A task failed under multi-replica load" >&2; exit 1; }
  (( done_count == TASK_COUNT )) && break
  sleep 2
done
test "$done_count" -eq "$TASK_COUNT"

echo "[5/5] Capturing replica and task evidence..."
docker compose ps --format json >"$output/compose-ps.jsonl"
jq -n --arg agent "$agent_id" --argjson tasks "$TASK_COUNT" \
  '{status:"PASS",agent_id:$agent,completed_tasks:$tasks,apiserver_replicas:2,analyzer_replicas:2,diagnosis_worker_replicas:2}' \
  >"$output/result.json"
echo "PASS: $TASK_COUNT tasks completed with two API/analyzer/diagnosis-worker replicas. Evidence: $output"
