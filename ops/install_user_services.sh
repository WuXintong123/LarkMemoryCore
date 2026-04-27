#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export SYSTEMD_PAGER=
source "${REPO_ROOT}/ops/common.sh"

mkdir -p "$HOME/.config/systemd/user" "$HOME/.config/ruyi-serving" "$HOME/.local/bin" "$REPO_ROOT/.run"

install_caddy() {
  if [[ -x "$HOME/.local/bin/caddy" ]]; then
    echo "[ok] caddy already installed: $HOME/.local/bin/caddy"
    return
  fi

  tmpdir=$(mktemp -d)
  trap 'rm -rf "$tmpdir"' RETURN

  asset_url=$(ruyi_python - <<'PY'
import json
import urllib.request

url = 'https://api.github.com/repos/caddyserver/caddy/releases/latest'
with urllib.request.urlopen(url, timeout=30) as response:
    release = json.load(response)
for asset in release.get('assets', []):
    name = asset.get('name', '')
    if name.endswith('linux_amd64.tar.gz'):
        print(asset['browser_download_url'])
        break
else:
    raise SystemExit('failed to find latest Caddy linux_amd64 tarball')
PY
)

  curl -fsSL "$asset_url" -o "$tmpdir/caddy.tar.gz"
  tar -xzf "$tmpdir/caddy.tar.gz" -C "$tmpdir"
  install -m 0755 "$tmpdir/caddy" "$HOME/.local/bin/caddy"
  echo "[ok] installed caddy: $HOME/.local/bin/caddy"
}

build_compute_server() {
  ruyi_configure_cmake
  ruyi_build_targets generate_python_proto compute_server
  echo "[ok] built runtime artifacts for current machine"
}

ensure_model_config() {
  if [[ -f "$HOME/.config/ruyi-serving/models.json" ]]; then
    echo "[ok] using existing model config: $HOME/.config/ruyi-serving/models.json"
    return
  fi

  if [[ ! -f "$REPO_ROOT/models.json.example" ]]; then
    echo "[error] missing template: $REPO_ROOT/models.json.example" >&2
    exit 1
  fi

  cp "$REPO_ROOT/models.json.example" "$HOME/.config/ruyi-serving/models.json"
  echo "[ok] copied model template to: $HOME/.config/ruyi-serving/models.json"
}

ensure_tls_cert() {
  mkdir -p "$HOME/.config/ruyi-serving/tls"
  if [[ -f "$HOME/.config/ruyi-serving/tls/server.crt" && -f "$HOME/.config/ruyi-serving/tls/server.key" ]]; then
    echo "[ok] using existing self-signed TLS certificate"
    return
  fi

  public_ip=$(curl -4 -fsS --max-time 10 https://ifconfig.me || echo 127.0.0.1)
  openssl req -x509 -nodes -newkey rsa:2048 -days 3650     -keyout "$HOME/.config/ruyi-serving/tls/server.key"     -out "$HOME/.config/ruyi-serving/tls/server.crt"     -subj "/CN=localhost"     -addext "subjectAltName=DNS:localhost,DNS:plct-gpu,IP:127.0.0.1,IP:${public_ip}"
  chmod 600 "$HOME/.config/ruyi-serving/tls/server.key" "$HOME/.config/ruyi-serving/tls/server.crt"
  echo "[ok] generated self-signed TLS certificate"
}

install_units() {
  cp "$REPO_ROOT/deploy/systemd-user/ruyi-compute.service" "$HOME/.config/systemd/user/"
  cp "$REPO_ROOT/deploy/systemd-user/ruyi-api.service" "$HOME/.config/systemd/user/"
  cp "$REPO_ROOT/deploy/systemd-user/ruyi-proxy.service" "$HOME/.config/systemd/user/"
  ruyi_python "$REPO_ROOT/ops/systemd_layout.py" write-target "$HOME/.config/systemd/user/ruyi-serving.target"
  public_ip=$(curl -4 -fsS --max-time 10 https://ifconfig.me || echo 127.0.0.1)
  sed -e "s#__PUBLIC_IP__#${public_ip}#g" -e "s#__HOME__#${HOME}#g" "$REPO_ROOT/deploy/caddy/Caddyfile" > "$HOME/.config/ruyi-serving/Caddyfile"
  echo "[ok] installed systemd user units and Caddyfile (public_ip=${public_ip})"
}

stop_existing_listeners() {
  local ports=()
  mapfile -t compute_ports < <(ruyi_local_compute_ports)
  ports=(8000 18080 18443 "${compute_ports[@]}")
  for port in $(printf '%s\n' "${ports[@]}" | sed '/^$/d' | sort -u); do
    if ss -lntp | grep -q ":${port} "; then
      pids=$(ss -lntp | awk -v port=":${port} " '$0 ~ port {print $NF}' | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | sort -u)
      for pid in $pids; do
        kill -TERM "$pid" || true
      done
    fi
  done
  sleep 3
}

warn_linger() {
  linger=$(loginctl show-user "$USER" -p Linger 2>/dev/null | awk -F= '{print $2}')
  if [[ "$linger" != "yes" ]]; then
    echo "[warn] loginctl lingering 未开启。当前 systemd --user 可正常管理服务，但若要在完全退出登录后仍常驻/开机自启，需要管理员执行："
    echo "       sudo loginctl enable-linger $USER"
  fi
}

install_caddy
ensure_model_config
validate_active_runtime_config
ensure_tls_cert
build_compute_server
install_units
stop_existing_listeners
systemctl --user daemon-reload
mapfile -t managed_units < <(ruyi_managed_units)
systemctl --user enable "${managed_units[@]}" ruyi-serving.target >/dev/null
systemctl --user restart "${managed_units[@]}"
warn_linger
systemctl --user --no-pager --full status "${managed_units[@]}" | sed -n '1,160p'
