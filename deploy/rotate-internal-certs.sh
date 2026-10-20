#!/usr/bin/env bash
# 内部证书轮换入口：沿用原CA，只替换叶证书，并按服务依赖重新建立连接。
# Agent重建后helper要重新加入其命名空间；代理也可能需要reload以刷新旧Docker地址。

# 只在既有信任根下轮换叶证书，绝不发布或分发CA签名私钥。
set -euo pipefail
umask 077
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$root"
compose=(docker compose --project-directory "$root")
exec 9>secrets/.rotate.lock
flock -n 9 || exit 0
force="${1:-}"
[[ -z "$force" || "$force" = --force ]] || { printf 'Usage: rotate-internal-certs.sh [--force]\n' >&2; exit 2; }
if [[ "$force" != --force ]] && openssl x509 -in secrets/tls/server.crt -checkend 2592000 -noout >/dev/null; then exit 0; fi
active="$("${compose[@]}" exec -T postgres sh -c 'export PGPASSWORD="$(cat /run/secrets/database_password)"; exec psql -X -At -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM tasks WHERE collection_status IN ('"'"'CREATED'"'"','"'"'QUEUED'"'"','"'"'DELIVERED'"'"','"'"'RUNNING'"'"','"'"'UPLOADING'"'"')"')"
[[ "$active" = 0 ]] || { printf 'Active collections exist; keep current leaves and retry in the next quiet window.\n'; exit 0; }
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
stage="$root/secrets/rotation-$stamp"
agent_id="$(openssl x509 -in secrets/tls/agent.crt -noout -subject -nameopt RFC2253)"
agent_id="${agent_id#subject=CN=drop-agent-}"
[[ "$agent_id" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$ ]] || { printf 'Agent certificate identity is invalid.\n' >&2; exit 1; }
mkdir -m 700 "$stage"
for identity in server api agent; do
 cn=drop-api; usage=clientAuth
 if [[ "$identity" = server ]]; then cn=drop-server; usage=serverAuth; fi
 if [[ "$identity" = agent ]]; then cn="drop-agent-$agent_id"; fi
 openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$stage/$identity.key" 2>/dev/null
 openssl req -new -key "$stage/$identity.key" -out "$stage/$identity.csr" -subj "/CN=$cn"
 printf 'basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=%s\n' "$usage" > "$stage/$identity.ext"
 if [[ "$identity" = server ]]; then printf 'subjectAltName=DNS:drop-server,DNS:drop-server.drop.svc.cluster.local,DNS:localhost,IP:127.0.0.1\n' >> "$stage/$identity.ext"; fi
 openssl x509 -req -in "$stage/$identity.csr" -CA secrets/tls/ca.crt -CAkey secrets/signing/ca.key \
  -set_serial "0x$(openssl rand -hex 16)" -out "$stage/$identity.crt" -days 90 -sha256 -extfile "$stage/$identity.ext" 2>/dev/null
 openssl verify -CAfile secrets/tls/ca.crt "$stage/$identity.crt" >/dev/null
done
mkdir -m 700 "$stage/previous"
for identity in server api agent; do
 cp -p -- "secrets/tls/$identity.crt" "secrets/tls/$identity.key" "$stage/previous/"
 install -m 444 "$stage/$identity.crt" "secrets/tls/$identity.crt"
 install -m 444 "$stage/$identity.key" "secrets/tls/$identity.key"
done
healthy=0
recreate_services() {
 "${compose[@]}" up -d --no-deps --no-build --pull never --force-recreate drop-server api agent || return
 # helper共享Agent容器的PID/网络命名空间；Agent
 # 重建后，helper必须加入新容器，不能继续停留在旧的
 # 命名空间中；helper自身没有需要轮换的TLS凭据。
 if "${compose[@]}" config --services | grep -qx collector-helper; then
  "${compose[@]}" up -d --no-deps --no-build --pull never --force-recreate collector-helper || return
 fi
 # API重建可能改变Docker地址，即使Web容器健康也仍需刷新上游解析。
 bash "$root/reload-proxies.sh" --project-directory "$root" || return
}
if recreate_services; then
 for i in $(seq 1 45); do
  if "${compose[@]}" exec -T api wget -q -O /dev/null http://127.0.0.1:8080/health/ready; then healthy=1; break; fi
  sleep 1
 done
fi
if [[ "$healthy" != 1 ]]; then
 for identity in server api agent; do
  install -m 444 "$stage/previous/$identity.crt" "secrets/tls/$identity.crt"
  install -m 444 "$stage/previous/$identity.key" "secrets/tls/$identity.key"
 done
 recreate_services
 printf 'Rotation start failed; previous leaves restored.\n' >&2
 exit 1
fi
"${compose[@]}" exec -T postgres sh -c 'export PGPASSWORD="$(cat /run/secrets/database_password)"; exec psql -X -q -v ON_ERROR_STOP=1 --set agent_id="$1" -U "$POSTGRES_USER" -d "$POSTGRES_DB"' sh "$agent_id" <<'SQL'
INSERT INTO audit_logs(action,resource_type,resource_id,request_id,detail)
VALUES('agent.certificate.rotate','agent',:'agent_id','certificate-rotation','{"source":"systemd","trust_root_changed":false}'::jsonb);
SQL
printf 'Verified ninety-day leaves installed; previous leaves retained in protected rotation directory.\n'
