#!/usr/bin/env bash
set -euo pipefail

# Reproducible HolmesGPT comparator. Credentials are read only from the
# environment; they are never accepted as command-line arguments.
: "${OPENAI_API_KEY:?set OPENAI_API_KEY in the process environment}"
: "${OPENAI_API_BASE:?set OPENAI_API_BASE in the process environment}"
: "${HOLMES_MODEL:?set HOLMES_MODEL, for example openai/provider-model}"

HOLMES_BIN="${HOLMES_BIN:-holmes}"
PROMPT_FILE="${1:-reports/benchmark/mature-products/holmesgpt-0.39.0/input.md}"
OUTPUT_FILE="${2:-reports/benchmark/mature-products/holmesgpt-0.39.0/output.txt}"

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
start_ns="$(date +%s%N)"
"${HOLMES_BIN}" ask --prompt-file "${PROMPT_FILE}" --model "${HOLMES_MODEL}" --max-steps 6 >"${OUTPUT_FILE}"
end_ns="$(date +%s%N)"
ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

python3 - "${started_at}" "${ended_at}" "${start_ns}" "${end_ns}" "${OUTPUT_FILE}" <<'PY'
import hashlib
import json
import pathlib
import sys

started_at, ended_at, start_ns, end_ns, output_file = sys.argv[1:]
path = pathlib.Path(output_file)
payload = {
    "started_at": started_at,
    "ended_at": ended_at,
    "latency_seconds": round((int(end_ns) - int(start_ns)) / 1_000_000_000, 6),
    "output_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    "output_bytes": path.stat().st_size,
}
path.with_name("execution.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(payload, ensure_ascii=False))
PY
