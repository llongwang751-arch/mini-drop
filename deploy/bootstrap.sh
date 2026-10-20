#!/usr/bin/env bash
# 初始化新部署的本地secret、证书和服务配置；不能把它当作覆盖现有生产secret的升级命令。
# 初始化材料留在受限文件中，不打印口令或CA私钥；服务挂载决定各进程能读取哪些内容。

set -euo pipefail
cd "$(dirname "$0")"
umask 027
mkdir -p secrets/tls
secret_value() { local path="secrets/$1"; if [[ ! -s "$path" ]]; then openssl rand -hex "${2:-24}" > "$path"; fi; }
secret_value database_password
secret_value app_database_password
secret_value s3_access_key 12
secret_value s3_secret_key
secret_value admin_password 16
secret_value grafana_password
secret_value alert_webhook_token
if [[ ! -s secrets/s3_kms_master_key ]]; then
 printf 'drop-storage-v1:%s\n' "$(openssl rand -base64 32)" > secrets/s3_kms_master_key
fi
[[ -s secrets/admin_username ]] || printf 'admin\n' > secrets/admin_username
printf 'postgresql://drop:%s@postgres:5432/drop?sslmode=disable\n' "$(cat secrets/database_password)" > secrets/.migration_database_url.tmp
mv -f -- secrets/.migration_database_url.tmp secrets/migration_database_url
printf 'postgresql://drop_app:%s@postgres:5432/drop?sslmode=disable\n' "$(cat secrets/app_database_password)" > secrets/.database_url.tmp
mv -f -- secrets/.database_url.tmp secrets/database_url
cd secrets/tls
if [[ ! -s ca.crt ]]; then
 openssl genrsa -out ca.key 4096 2>/dev/null
 openssl req -x509 -new -key ca.key -sha256 -days 3650 -out ca.crt -subj '/CN=Drop Internal CA' 2>/dev/null
fi
issue_cert() {
 local name="$1" cn="$2" usage="$3" san="${4:-}"
 [[ -s "$name.crt" ]] && return
 openssl genrsa -out "$name.key" 2048 2>/dev/null
 openssl req -new -key "$name.key" -out "$name.csr" -subj "/CN=$cn" 2>/dev/null
 printf 'basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=%s\n' "$usage" > "$name.ext"
 [[ -z "$san" ]] || printf 'subjectAltName=%s\n' "$san" >> "$name.ext"
 local signing_key=ca.key
 [[ ! -s ../signing/ca.key ]] || signing_key=../signing/ca.key
 openssl x509 -req -in "$name.csr" -CA ca.crt -CAkey "$signing_key" -CAcreateserial -out "$name.crt" -days 365 -sha256 -extfile "$name.ext" 2>/dev/null
 rm -- "$name.csr" "$name.ext"
}
issue_cert server drop-server serverAuth 'DNS:drop-server,DNS:drop-server.drop.svc.cluster.local,DNS:localhost,IP:127.0.0.1'
issue_cert api drop-api clientAuth
issue_cert agent "drop-agent-${AGENT_ID:-aliyun-local}" clientAuth
cd ../..
# 独立Compose的secret保留宿主文件权限，可读范围依靠
# 各服务挂载控制；CA签名私钥不进入任何运行容器的挂载。
mkdir -p secrets/signing
if [[ -s secrets/tls/ca.key ]]; then
 if [[ -s secrets/signing/ca.key ]]; then
  cmp <(openssl pkey -in secrets/tls/ca.key -pubout 2>/dev/null) <(openssl pkey -in secrets/signing/ca.key -pubout 2>/dev/null) || { printf 'Refusing mismatched CA signing keys.\n' >&2; exit 1; }
  rm -- secrets/tls/ca.key
 else
  mv -- secrets/tls/ca.key secrets/signing/ca.key
 fi
fi
cmp <(openssl x509 -in secrets/tls/ca.crt -pubkey -noout) <(openssl pkey -in secrets/signing/ca.key -pubout 2>/dev/null) || { printf 'CA certificate and signing key do not match.\n' >&2; exit 1; }
chmod 700 secrets/signing
chmod 600 secrets/signing/ca.key
chmod 700 secrets secrets/tls
find -H secrets -maxdepth 1 -type f -exec chmod 444 {} \;
find secrets/tls -type f -exec chmod 444 {} \;
printf 'Bootstrap complete. Credentials saved under deploy/secrets (never commit).\n'
