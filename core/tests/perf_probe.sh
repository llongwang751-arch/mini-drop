#!/bin/sh
# Runs only the bundled controlled workload. Not a production Agent entrypoint.
set -eu
out="${1:-/tmp/drop-perf-probe}"
mkdir -p "$out"
printf 'Kernel: '; uname -r
printf 'Identity: '; id
printf 'Capabilities: '; sed -n '/^CapEff:/p' /proc/self/status
printf 'perf_event_paranoid: '; cat /proc/sys/kernel/perf_event_paranoid
perf_path="$(find -L /usr/lib/linux-tools -name perf -type f | head -n 1)"
test -n "$perf_path"
"$perf_path" version
/usr/local/bin/drop_workload > "$out/workload.log" 2>&1 &
target_pid=$!
trap 'kill "$target_pid" 2>/dev/null || true; wait "$target_pid" 2>/dev/null || true' EXIT INT TERM
printf 'Controlled workload PID: %s\n' "$target_pid"
sleep 1
"$perf_path" record -e cpu-clock -F 49 -g -p "$target_pid" -o "$out/perf.data" -- /usr/bin/sleep 3 2> "$out/record.stderr" || {
  cat "$out/record.stderr"
  exit 1
}
cat "$out/record.stderr"
"$perf_path" script -i "$out/perf.data" > "$out/perf.script" 2> "$out/script.stderr" || {
  cat "$out/script.stderr"
  exit 1
}
test -s "$out/perf.script"
printf 'Raw artifact hash: '; sha256sum "$out/perf.data"
printf 'Decoded samples: '; grep -c 'cpu-clock:' "$out/perf.script"
printf 'Controlled stack excerpt:\n'; head -n 30 "$out/perf.script"
