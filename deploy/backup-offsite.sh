#!/usr/bin/env bash
set -euo pipefail
# Local backup remains valid when no independent destination is configured.
config=/opt/drop/deploy/secrets/backup-offsite.env
[[ -f "$config" ]] || { printf 'Offsite destination is not configured; local backup retained.\n'; exit 0; }
[[ "$(stat -c %u "$config")" = 0 && "$(stat -c %a "$config")" = 600 ]] || { printf 'Offsite config must be root-owned mode 0600.\n' >&2; exit 1; }
source "$config"
: "${DROP_BACKUP_DESTINATION:?Configured rclone destination is required}"
: "${RCLONE_CONFIG:?Protected rclone config path is required}"
[[ -x /usr/bin/rclone && -f "$RCLONE_CONFIG" ]] || { printf 'rclone and protected configuration are required.\n' >&2; exit 1; }
/usr/bin/rclone copy /opt/drop-backups "$DROP_BACKUP_DESTINATION" \
  --config "$RCLONE_CONFIG" --exclude '*.partial/**' --exclude '.backup.lock' \
  --transfers 1 --checkers 2 --bwlimit 2M --log-level ERROR
/usr/bin/rclone check /opt/drop-backups "$DROP_BACKUP_DESTINATION" \
  --config "$RCLONE_CONFIG" --one-way --exclude '*.partial/**' --exclude '.backup.lock' --log-level ERROR
printf 'Offsite copy and source/destination comparison completed.\n'
