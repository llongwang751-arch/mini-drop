#!/usr/bin/env bash
# 公共证书发布共用带锁实现；调用方先设置deploy_dir及umask 077，防止并发覆盖与权限放宽。
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
 # reload是异步的，旧worker可能暂时仍使用仅HTTP配置。
 # 保持证书验证开启，短暂等待新监听器就绪，不能用跳过TLS验证假装成功。
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
  # 每六小时的续期检查不能重复保存同一份未变化证书与私钥，避免无界累积。
  tls_verify_loaded
  tls_committed=1
  return
 fi
 tls_bundle_dir="$(mktemp -d "$deploy_dir/secrets/public-tls/.bundle-XXXXXX")"
 tls_bundle_identity="$(stat -c '%d:%i' -- "$tls_bundle_dir")"
 install -m 444 -- "$cert_dir/fullchain.pem" "$tls_bundle_dir/fullchain.pem"
 install -m 444 -- "$cert_dir/privkey.pem" "$tls_bundle_dir/privkey.pem"
 # 挂载的父目录和证书bundle允许UID101网关遍历，
 # 外层宿主secrets目录仍保持仅root可访问的0700权限。
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
 # 回滚失败时保留新旧两份状态用于恢复；不能删除仍可能被
 # current引用的bundle，也不能抹掉受保护的备份。
 if [[ "$rollback_ok" = 1 ]]; then
  rm -f -- "$tls_backup_dir/edge.conf" "$tls_backup_dir/cert.pub" "$tls_backup_dir/key.pub"
  rmdir -- "$tls_backup_dir" 2>/dev/null
 fi
 [[ "$rollback_ok" = 1 ]] || status=1
 exit "$status"
}
