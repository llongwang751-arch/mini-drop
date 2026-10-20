#!/usr/bin/env bash
# 维护监控状态的显式入口，与业务采集任务分离。
# 运维状态只报告真实执行结果，不能因可选监控未接入就伪造健康数据。

set -euo pipefail
umask 022
status_dir=/opt/drop-monitoring
mkdir -p "$status_dir"
chmod 755 "$status_dir"
file="$status_dir/status.prom.next"
last=0
verified_backup=''
for backup in /opt/drop-backups/drop-????????T??????Z-??????; do
 [[ -d "$backup" && ! -L "$backup" && -s "$backup/restore-check.json" ]] || continue
 stamp="$(stat -c %Y "$backup/restore-check.json")"
 if (( stamp > last )); then last="$stamp"; verified_backup="$backup"; fi
done
printf 'drop_backup_last_success %s\n' "$last" > "$file"
if [[ -n "$verified_backup" ]]; then
 python3 - "$verified_backup/restore-check.json" >> "$file" <<'PY'
import json, math, pathlib, sys
source = pathlib.Path(sys.argv[1])
if source.stat().st_size <= 1048576:
    try:
        value = json.loads(source.read_text()).get('restore_duration_seconds')
        if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 864000:
            print(f'drop_backup_restore_duration_seconds {value:.3f}')
    except (OSError, ValueError, TypeError, AttributeError):
        pass
PY
fi
deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tls_root="$deploy_dir/secrets/public-tls"
current="$(readlink -- "$tls_root/current" 2>/dev/null || true)"
certificate="$tls_root/current/fullchain.pem"
if [[ "$current" =~ ^\.bundle-[a-zA-Z0-9]+$ && -d "$tls_root/$current" && ! -L "$tls_root/$current" && -s "$certificate" ]]; then
 expires="$(openssl x509 -in "$certificate" -enddate -noout)"
 end="$(date -u -d "${expires#notAfter=}" +%s)"
 now="$(date -u +%s)"
 remaining=$((end-now))
 (( remaining >= 0 )) || remaining=0
 printf 'drop_public_certificate_expiry_seconds %s\n' "$remaining" >> "$file"
fi
chmod 644 "$file"
mv -f -- "$file" "$status_dir/status.prom"
