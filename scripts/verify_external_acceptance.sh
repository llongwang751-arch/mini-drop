#!/usr/bin/env bash
set -euo pipefail

case "${1:-}" in
  ebpf) exec bash scripts/verify_native_ebpf.sh ;;
  backup) exec bash scripts/verify_backup_restore.sh ;;
  replicas) exec bash scripts/verify_multi_replica.sh ;;
  *)
    echo "usage: $0 {ebpf|backup|replicas}" >&2
    exit 2
    ;;
esac
