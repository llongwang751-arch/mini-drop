#!/usr/bin/env bash
set -euo pipefail

CONTROL_NAME="${1:-}"
PKI_DIR="${2:-deploy/certs/control}"
API_CLIENT_IDENTITY="${3:-mini-drop-control-client}"

if [[ -z "$CONTROL_NAME" ]]; then
  echo "usage: $0 <control-IP-or-DNS> [pki-dir] [api-client-identity]" >&2
  exit 2
fi
if [[ ! "$API_CLIENT_IDENTITY" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "invalid API client identity: use 1-128 letters, digits, dot, underscore or dash" >&2
  exit 2
fi

command -v openssl >/dev/null 2>&1 || {
  echo "openssl is required" >&2
  exit 1
}

for file in ca.key ca.crt server.key server.crt client.key client.crt; do
  if [[ -e "$PKI_DIR/$file" ]]; then
    echo "refusing to overwrite $PKI_DIR/$file" >&2
    exit 1
  fi
done

mkdir -p "$PKI_DIR"
umask 077

# Git for Windows rewrites /CN=... unless argument conversion is disabled.
if [[ -n "${MSYSTEM:-}" ]]; then
  export MSYS2_ARG_CONV_EXCL="/CN="
fi

if [[ "$CONTROL_NAME" =~ ^[0-9a-fA-F:.]+$ ]]; then
  CONTROL_SAN="IP:$CONTROL_NAME"
else
  CONTROL_SAN="DNS:$CONTROL_NAME"
fi

openssl genrsa -out "$PKI_DIR/ca.key" 4096
openssl req -x509 -new -nodes -key "$PKI_DIR/ca.key" -sha256 -days 3650 \
  -subj "/CN=Mini-Drop Private CA" -out "$PKI_DIR/ca.crt"

openssl genrsa -out "$PKI_DIR/server.key" 2048
openssl req -new -key "$PKI_DIR/server.key" -subj "/CN=$CONTROL_NAME" \
  -out "$PKI_DIR/server.csr"
printf '%s\n' \
  'authorityKeyIdentifier=keyid,issuer' \
  'basicConstraints=CA:FALSE' \
  'keyUsage=digitalSignature,keyEncipherment' \
  'extendedKeyUsage=serverAuth' \
  "subjectAltName=$CONTROL_SAN,DNS:control-plane,DNS:diagnosis-worker,DNS:localhost,IP:127.0.0.1" \
  > "$PKI_DIR/server.ext"
openssl x509 -req -in "$PKI_DIR/server.csr" \
  -CA "$PKI_DIR/ca.crt" -CAkey "$PKI_DIR/ca.key" -CAcreateserial \
  -out "$PKI_DIR/server.crt" -days 825 -sha256 -extfile "$PKI_DIR/server.ext"

openssl genrsa -out "$PKI_DIR/client.key" 2048
openssl req -new -key "$PKI_DIR/client.key" -subj "/CN=$API_CLIENT_IDENTITY" \
  -out "$PKI_DIR/client.csr"
printf '%s\n' \
  'authorityKeyIdentifier=keyid,issuer' \
  'basicConstraints=CA:FALSE' \
  'keyUsage=digitalSignature,keyEncipherment' \
  'extendedKeyUsage=clientAuth' \
  > "$PKI_DIR/client.ext"
openssl x509 -req -in "$PKI_DIR/client.csr" \
  -CA "$PKI_DIR/ca.crt" -CAkey "$PKI_DIR/ca.key" -CAserial "$PKI_DIR/ca.srl" \
  -out "$PKI_DIR/client.crt" -days 825 -sha256 -extfile "$PKI_DIR/client.ext"

rm -f "$PKI_DIR/server.csr" "$PKI_DIR/server.ext" \
  "$PKI_DIR/client.csr" "$PKI_DIR/client.ext"
chmod 600 "$PKI_DIR/ca.key" "$PKI_DIR/server.key"
# The Go API runs as UID/GID 65532 and receives root as a supplemental group
# in Compose. Keep the client key root-owned but group-readable for that process.
chmod 640 "$PKI_DIR/client.key"
chmod 644 "$PKI_DIR/ca.crt" "$PKI_DIR/server.crt" "$PKI_DIR/client.crt"

echo "initialized Mini-Drop PKI in $PKI_DIR"
echo "server identity: $CONTROL_NAME"
echo "API client identity: $API_CLIENT_IDENTITY"
echo "keep ca.key and server.key on Control; issue one certificate per Agent next"
