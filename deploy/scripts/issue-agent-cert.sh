#!/usr/bin/env bash
set -euo pipefail

AGENT_ID="${1:-}"
PKI_DIR="${2:-deploy/certs/control}"
OUTPUT_DIR="${3:-$PKI_DIR/agents/$AGENT_ID}"

if [[ -z "$AGENT_ID" ]]; then
  echo "usage: $0 <agent-id> [control-pki-dir] [output-dir]" >&2
  exit 2
fi
if [[ ! "$AGENT_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "invalid agent-id: use 1-128 letters, digits, dot, underscore or dash" >&2
  exit 2
fi

command -v openssl >/dev/null 2>&1 || {
  echo "openssl is required" >&2
  exit 1
}
for file in ca.key ca.crt; do
  if [[ ! -f "$PKI_DIR/$file" ]]; then
    echo "missing $PKI_DIR/$file; run init-control-pki.sh first" >&2
    exit 1
  fi
done
for file in ca.crt agent.key agent.crt; do
  if [[ -e "$OUTPUT_DIR/$file" ]]; then
    echo "refusing to overwrite $OUTPUT_DIR/$file" >&2
    exit 1
  fi
done

mkdir -p "$OUTPUT_DIR"
umask 077
if [[ -n "${MSYSTEM:-}" ]]; then
  export MSYS2_ARG_CONV_EXCL="/CN="
fi

openssl genrsa -out "$OUTPUT_DIR/agent.key" 2048
openssl req -new -key "$OUTPUT_DIR/agent.key" -subj "/CN=$AGENT_ID" \
  -out "$OUTPUT_DIR/agent.csr"
printf '%s\n' \
  'authorityKeyIdentifier=keyid,issuer' \
  'basicConstraints=CA:FALSE' \
  'keyUsage=digitalSignature,keyEncipherment' \
  'extendedKeyUsage=clientAuth' \
  > "$OUTPUT_DIR/agent.ext"
openssl x509 -req -in "$OUTPUT_DIR/agent.csr" \
  -CA "$PKI_DIR/ca.crt" -CAkey "$PKI_DIR/ca.key" -CAserial "$PKI_DIR/ca.srl" \
  -out "$OUTPUT_DIR/agent.crt" -days 825 -sha256 -extfile "$OUTPUT_DIR/agent.ext"
cp "$PKI_DIR/ca.crt" "$OUTPUT_DIR/ca.crt"

rm -f "$OUTPUT_DIR/agent.csr" "$OUTPUT_DIR/agent.ext"
chmod 600 "$OUTPUT_DIR/agent.key"
chmod 644 "$OUTPUT_DIR/agent.crt" "$OUTPUT_DIR/ca.crt"

subject="$(openssl x509 -in "$OUTPUT_DIR/agent.crt" -noout -subject)"
echo "issued Agent certificate: $subject"
echo "Worker bundle: $OUTPUT_DIR/{ca.crt,agent.crt,agent.key}"
echo "copy only those three files to Worker $AGENT_ID"
