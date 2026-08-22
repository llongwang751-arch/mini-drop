#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run as root: sudo $0 [repository-path]" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${1:-$SCRIPT_DIR/../..}" && pwd)"

if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
fi
DISTRO_ID="${ID:-unknown}"
DISTRO_VERSION="${VERSION_ID:-unknown}"
DISTRO_TEXT="${ID:-} ${NAME:-} ${PRETTY_NAME:-}"
if [[ "${DISTRO_TEXT,,}" == *tlinux* || "${DISTRO_TEXT,,}" == *tencentos* ]]; then
  TLINUX_MAJOR="${DISTRO_VERSION%%.*}"
  case "$TLINUX_MAJOR" in
    2|3|4) ;;
    *) echo "unsupported TencentOS/TLinux generation: $DISTRO_VERSION" >&2; exit 2 ;;
  esac
fi

PYTHON_BIN="${MINI_DROP_PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  for candidate in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$(command -v "$candidate")"
      break
    fi
  done
fi

echo "detected host: ${PRETTY_NAME:-$DISTRO_ID $DISTRO_VERSION}, kernel=$(uname -r), arch=$(uname -m)"
echo "package manager: $(command -v dnf || command -v yum || command -v apt-get || echo unavailable)"

[[ -n "$PYTHON_BIN" ]] || { echo "Python >=3.9 is required; set MINI_DROP_PYTHON" >&2; exit 1; }
"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 9):
    raise SystemExit("Python >=3.9 is required; set MINI_DROP_PYTHON to a newer interpreter")
PY
"$PYTHON_BIN" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --upgrade pip
"$ROOT/.venv/bin/pip" install -e "$ROOT[dev]"
(cd "$ROOT/proto" && bash compile.sh)
"$ROOT/.venv/bin/python" "$ROOT/scripts/check_tlinux_compat.py"

install -d -m 0750 /etc/mini-drop
if [[ ! -e /etc/mini-drop/worker.env ]]; then
  install -m 0600 "$ROOT/deploy/env/worker.env.example" /etc/mini-drop/worker.env
fi
escaped_root="${ROOT//|/\\|}"
sed "s|@MINI_DROP_ROOT@|$escaped_root|g" \
  "$ROOT/deploy/systemd/mini-drop-agent.service" > /etc/systemd/system/mini-drop-agent.service
systemctl daemon-reload

echo "installed unit: /etc/systemd/system/mini-drop-agent.service"
echo "next: edit /etc/mini-drop/worker.env, copy ca.crt to $ROOT/deploy/certs/, then run:"
echo "  systemctl enable --now mini-drop-agent"
echo "verify required collectors, for example:"
echo "  $ROOT/.venv/bin/python $ROOT/scripts/check_tlinux_compat.py --require sys_metrics --require perf_cpu"
