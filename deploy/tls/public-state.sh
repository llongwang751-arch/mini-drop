#!/usr/bin/env bash
# Shared, locked public certificate publication. Callers set deploy_dir and umask 077.
tls_public_ip="${DROP_PUBLIC_IP:-120.24.187.205}"
[[ "$tls_public_ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1

tls_current_target() {
 local target=''
 if [[ -e "$deploy_dir/secrets/public-tls/current" || -L "$deploy_dir/secrets/public-tls/current" ]]; then
  target="$(readlink -- "$deploy_dir/secrets/public-tls/current")" || return 1
  [[ "$target" =~ ^\.bundle-[a-zA-Z0-9]+$ ]] || return 1
  [[ -d "$deploy_dir/secrets/public-tls/$target" && ! -L "$deploy_dir/secrets/public-tls/$target" ]] || return 1
 fi
 printf '%s' "$target"
}

tls_replace_current() {
 local target="$1" next="$tls_bundle_dir.current-next"
 [[ ! -e "$next" && ! -L "$next" ]] || return 1
 ln -s -- "$target" "$next" || return 1
 if ! mv -T -- "$next" "$deploy_dir/secrets/public-tls/current"; then rm -f -- "$next"; return 1; fi
}

tls_atomic_config() {
 local source="$1" next
 next="$(mktemp "$deploy_dir/runtime/.edge-next.XXXXXX")"
 if ! install -m 644 -- "$source" "$next"; then rm -- "$next"; return 1; fi
 if ! mv -T -- "$next" "$deploy_dir/runtime/edge.conf"; then rm -f -- "$next"; return 1; fi
}

tls_begin() {
 [[ -d "$deploy_dir/secrets/public-tls" && ! -L "$deploy_dir/secrets/public-tls" ]] || return 1
 [[ -f "$deploy_dir/runtime/edge.conf" && ! -L "$deploy_dir/runtime/edge.conf" ]] || return 1
 tls_previous_target="$(tls_current_target)" || return 1
 tls_backup_dir="$(mktemp -d "$deploy_dir/secrets/.public-tls-rollback.XXXXXX")"
 chmod 700 "$tls_backup_dir"
 cp -- "$deploy_dir/runtime/edge.conf" "$tls_backup_dir/edge.conf"
 chmod 600 "$tls_backup_dir/edge.conf"
 tls_bundle_dir=''; tls_bundle_identity=''; tls_committed=0; tls_changed=0
 trap 'tls_finish "$?"' EXIT
}

tls_verify_loaded() {
 docker compose exec -T gateway nginx -c /etc/drop/runtime/edge.conf -t > /dev/null 2>&1
 docker compose exec -T gateway nginx -c /etc/drop/runtime/edge.conf -s reload > /dev/null 2>&1
 # Reload is asynchronous: the old worker can still serve the HTTP-only config.
 # Keep certificate validation enabled and wait briefly for the new listener.
 local attempt
 for attempt in {1..10}; do
  if curl --fail --silent --connect-timeout 2 --max-time 3 --resolve "$tls_public_ip:443:127.0.0.1" "https://$tls_public_ip/healthz" > /dev/null; then return 0; fi
  [[ "$attempt" = 10 ]] || sleep 1
 done
 return 1
}

tls_publish() {
 local cert_dir="$1" template="$2"
 openssl x509 -in "$cert_dir/fullchain.pem" -noout -checkip "$tls_public_ip" > /dev/null
 openssl x509 -in "$cert_dir/fullchain.pem" -noout -checkend 86400 > /dev/null
 openssl x509 -in "$cert_dir/fullchain.pem" -pubkey -noout | openssl pkey -pubin -outform DER -out "$tls_backup_dir/cert.pub"
 openssl pkey -in "$cert_dir/privkey.pem" -passin pass: -pubout -outform DER -out "$tls_backup_dir/key.pub"
 cmp -s -- "$tls_backup_dir/cert.pub" "$tls_backup_dir/key.pub"
 if [[ -n "$tls_previous_target" ]] && cmp -s -- "$cert_dir/fullchain.pem" "$deploy_dir/secrets/public-tls/$tls_previous_target/fullchain.pem" && cmp -s -- "$cert_dir/privkey.pem" "$deploy_dir/secrets/public-tls/$tls_previous_target/privkey.pem" && cmp -s -- "$template" "$deploy_dir/runtime/edge.conf"; then
  # A six-hour renewal check must not accumulate copies of an unchanged pair.
  tls_verify_loaded
  tls_committed=1
  return
 fi
 tls_bundle_dir="$(mktemp -d "$deploy_dir/secrets/public-tls/.bundle-XXXXXX")"
 tls_bundle_identity="$(stat -c '%d:%i' -- "$tls_bundle_dir")"
 install -m 444 -- "$cert_dir/fullchain.pem" "$tls_bundle_dir/fullchain.pem"
 install -m 444 -- "$cert_dir/privkey.pem" "$tls_bundle_dir/privkey.pem"
 # The mounted parent and bundle are traversable by the UID101 gateway;
 # the host's enclosing secrets directory remains root-only mode0700.
 chmod 755 "$tls_bundle_dir"
 tls_changed=1
 tls_replace_current "${tls_bundle_dir##*/}"
 tls_atomic_config "$template"
 tls_verify_loaded
 tls_committed=1
}

tls_finish() {
 local status="$1" rollback_ok=1
 trap - EXIT
 set +e
 if [[ "$tls_committed" != 1 && "$tls_changed" = 1 ]]; then
  if [[ -n "$tls_previous_target" ]]; then
   tls_replace_current "$tls_previous_target" || rollback_ok=0
  elif [[ ! -e "$deploy_dir/secrets/public-tls/current" && ! -L "$deploy_dir/secrets/public-tls/current" ]]; then
   : # A failed first pointer rename left the previous HTTP state untouched.
  elif [[ "$(tls_current_target)" = "${tls_bundle_dir##*/}" ]]; then
   rm -- "$deploy_dir/secrets/public-tls/current" || rollback_ok=0
  else
   rollback_ok=0
  fi
  tls_atomic_config "$tls_backup_dir/edge.conf" || rollback_ok=0
  docker compose exec -T gateway nginx -c /etc/drop/runtime/edge.conf -t > /dev/null 2>&1 || rollback_ok=0
  docker compose exec -T gateway nginx -c /etc/drop/runtime/edge.conf -s reload > /dev/null 2>&1 || rollback_ok=0
  if [[ "$rollback_ok" = 1 ]]; then printf 'PUBLIC_TLS_FAILED_PREVIOUS_STATE_RESTORED\n' >&2;
  else printf 'PUBLIC_TLS_ROLLBACK_FAILED\n' >&2; fi
 fi
 if [[ "$rollback_ok" = 1 && "$tls_committed" != 1 && -n "$tls_bundle_dir" && ! -L "$tls_bundle_dir" && -d "$tls_bundle_dir" && "$(stat -c '%d:%i' -- "$tls_bundle_dir")" = "$tls_bundle_identity" ]]; then
  rm -f -- "$tls_bundle_dir/fullchain.pem" "$tls_bundle_dir/privkey.pem" "$tls_bundle_dir.current-next"
  rmdir -- "$tls_bundle_dir" 2>/dev/null
 fi
 # Failed rollback retains both states for recovery; never remove a bundle
 # that might still be referenced by current or erase its protected backup.
 if [[ "$rollback_ok" = 1 ]]; then
  rm -f -- "$tls_backup_dir/edge.conf" "$tls_backup_dir/cert.pub" "$tls_backup_dir/key.pub"
  rmdir -- "$tls_backup_dir" 2>/dev/null
 fi
 [[ "$rollback_ok" = 1 ]] || status=1
 exit "$status"
}
