#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
  ebpf) exec bash scripts/verify_native_ebpf.sh ;;
  backup) exec bash scripts/verify_backup_restore.sh ;;
  replicas) exec bash scripts/verify_multi_replica.sh ;;
  benchmark)
    submissions="${2:-reports/benchmark/submissions.json}"
    if [[ ! -f "$submissions" ]]; then
      echo "benchmark submissions not found: $submissions" >&2
      echo "generate the campaign with: python scripts/diagnosis_benchmark.py campaign" >&2
      exit 3
    fi
    exec python scripts/diagnosis_benchmark.py evaluate "$submissions" --require-complete \
      --output reports/benchmark/evaluation.json
    ;;
  *)
    echo "usage: $0 {ebpf|backup|replicas|benchmark [submissions.json]}" >&2
    exit 2
    ;;
esac
