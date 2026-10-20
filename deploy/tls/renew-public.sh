#!/usr/bin/env bash
set -euo pipefail
umask 077
deploy_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$deploy_dir"
exec 9>secrets/.acme.lock
flock -n 9 || exit 0
DROP_PUBLIC_IP="$("${DROP_PYTHON_EXECUTABLE:-python3}" "$deploy_dir/tls/public-address.py")"
export DROP_PUBLIC_IP
docker run --rm --pull never --memory 256m --memory-swap 384m --cpus .25 --pids-limit 64 \
  --cap-drop ALL --security-opt no-new-privileges \
  -v "$deploy_dir/secrets/acme:/etc/letsencrypt" \
  -v "$deploy_dir/acme-webroot:/var/www/acme" \
  certbot/certbot:v5.8.0 renew --quiet --no-random-sleep-on-renew
source "$deploy_dir/tls/public-state.sh"
tls_begin
tls_publish "$deploy_dir/secrets/acme/live/$DROP_PUBLIC_IP" "$deploy_dir/runtime/edge-tls.template.conf"
printf 'Public IP certificate verified and reloaded.\n'
