#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-}"
shift || true
EXPECTED_AGENTS=("$@")

if [[ -z "$BASE_URL" || ${#EXPECTED_AGENTS[@]} -eq 0 ]]; then
  echo "usage: MINI_DROP_API_KEY=... $0 <https://control> <agent-id> [agent-id ...]" >&2
  echo "optional: MINI_DROP_CA_CERT=/path/to/ca.crt" >&2
  exit 2
fi
if [[ -z "${MINI_DROP_API_KEY:-}" ]]; then
  echo "MINI_DROP_API_KEY must be supplied through the environment" >&2
  exit 2
fi
for command in curl python3; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "$command is required" >&2
    exit 1
  }
done

BASE_URL="${BASE_URL%/}"
CURL_TLS=()
if [[ -n "${MINI_DROP_CA_CERT:-}" ]]; then
  [[ -f "$MINI_DROP_CA_CERT" ]] || {
    echo "MINI_DROP_CA_CERT does not exist: $MINI_DROP_CA_CERT" >&2
    exit 2
  }
  CURL_TLS=(--cacert "$MINI_DROP_CA_CERT")
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

api_get() {
  local path="$1"
  local output="$2"
  curl --fail --silent --show-error "${CURL_TLS[@]}" \
    -H "X-API-Key: $MINI_DROP_API_KEY" \
    "$BASE_URL$path" -o "$output"
}

api_get "/api/healthz" "$TMP_DIR/health.json"
python3 - "$TMP_DIR/health.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
if payload.get("code") != 0:
    raise SystemExit(f"health check failed: {payload}")
print("[ok] Control API and internal dependencies are healthy")
PY

api_get "/api/agents?limit=1000" "$TMP_DIR/agents.json"
python3 - "$TMP_DIR/agents.json" "${EXPECTED_AGENTS[@]}" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
items = payload.get("data", {}).get("items", [])
by_id = {str(item.get("id")): item for item in items}
failed = False
for agent_id in sys.argv[2:]:
    item = by_id.get(agent_id)
    if item is None:
        print(f"[fail] {agent_id}: not registered")
        failed = True
        continue
    status = item.get("status")
    capabilities = item.get("capabilities") or []
    required = {"perf_cpu", "ebpf_io", "java_async", "go_pprof", "pyspy", "memory_smaps", "sys_metrics", "continuous_perf"}
    missing = sorted(required.difference(map(str, capabilities)))
    if status != "ONLINE":
        print(f"[fail] {agent_id}: status={status}")
        failed = True
    elif missing:
        print(f"[fail] {agent_id}: missing capabilities={','.join(missing)}")
        failed = True
    else:
        print(f"[ok] {agent_id}: ONLINE, all 8 collectors advertised")
if failed:
    raise SystemExit(1)
PY

for agent_id in "${EXPECTED_AGENTS[@]}"; do
  encoded_agent="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$agent_id")"
  api_get "/api/top-processes?agent_id=$encoded_agent&limit=1" "$TMP_DIR/process-$agent_id.json"
  python3 - "$TMP_DIR/process-$agent_id.json" "$agent_id" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
data = payload.get("data") or {}
items = data.get("items") if isinstance(data, dict) else data
if not items:
    raise SystemExit(f"[fail] {sys.argv[2]}: no trusted process snapshot")
print(f"[ok] {sys.argv[2]}: trusted process snapshot is queryable")
PY
done

echo "multi-node control path verified for ${#EXPECTED_AGENTS[@]} Agent(s)"
