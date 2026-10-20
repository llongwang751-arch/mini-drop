#!/usr/bin/env bash
# 必须在完整Compose已包含compose.https.yml后调用，确保HTTPS入口已经定义。
set -euo pipefail
umask 077
deploy_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$deploy_dir"
mkdir -p runtime acme-webroot secrets/acme secrets/acme-staging secrets/public-tls
chmod 755 runtime acme-webroot secrets/public-tls
exec 9>secrets/.acme.lock
flock -n 9 || exit 0
DROP_PUBLIC_IP="$("${DROP_PYTHON_EXECUTABLE:-python3}" "$deploy_dir/tls/public-address.py")"
export DROP_PUBLIC_IP
if [[ ! -s runtime/edge.conf ]]; then install -m 644 tls/edge-http.conf runtime/edge.conf; fi
docker compose up -d --no-deps --no-build --pull never web gateway
issue() {
 local folder="$1"; shift
 docker run --rm --pull never --memory 256m --memory-swap 384m --cpus .25 --pids-limit 64 \
  --cap-drop ALL --security-opt no-new-privileges \
  -v "$deploy_dir/secrets/$folder:/etc/letsencrypt" \
  -v "$deploy_dir/acme-webroot:/var/www/acme" \
  certbot/certbot:v5.8.0 certonly --non-interactive --agree-tos \
  --register-unsafely-without-email --preferred-profile shortlived \
  --webroot --webroot-path /var/www/acme --ip-address "$DROP_PUBLIC_IP" "$@"
}
if [[ ! -s "secrets/acme/live/$DROP_PUBLIC_IP/fullchain.pem" ]]; then
 issue acme-staging --staging
 issue acme
fi
source "$deploy_dir/tls/public-state.sh"
tls_begin
tls_publish "$deploy_dir/secrets/acme/live/$DROP_PUBLIC_IP" "$deploy_dir/runtime/edge-tls.template.conf"
printf 'Public IP TLS installed. Verify external HTTPS before changing PUBLIC_URL.\n'
