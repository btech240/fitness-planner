#!/usr/bin/env bash
# Install, re-install or update Fitness Planner as a systemd service on Debian 12.
# Run as root from a clone of the repo:   bash deploy/install.sh
# Safe to re-run: your data in /var/lib/fitness-planner is never touched.
#
# Options (environment variables):
#   PORT=8080                         listen on another port (default 8000)
#   EXTRA_HOSTS="fitness.lan,gym"     more names you type in the browser to reach the app
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR=/var/lib/fitness-planner
SERVICE=fitness-planner
APP_USER=fitness
PORT="${PORT:-8000}"
UNIT=/etc/systemd/system/$SERVICE.service
DROPIN_DIR=/etc/systemd/system/$SERVICE.service.d

[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }

echo "==> Installing system packages"
apt-get update -qq
apt-get install -y -qq python3 python3-venv >/dev/null

echo "==> Creating service user and data directory"
id "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$DATA_DIR"
chown "$APP_USER:$APP_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"

echo "==> Building Python environment in $APP_DIR/.venv"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
# The code is owned by root and read-only to the service: a compromised app can't rewrite itself.
chown -R root:root "$APP_DIR"
chmod -R go-w "$APP_DIR"

echo "==> Fetching exercise photos (about 60 MB the first time, then only what's missing)"
mkdir -p "$DATA_DIR/photos"
(cd "$APP_DIR" && "$APP_DIR/.venv/bin/python" -m app.fetch_photos "$DATA_DIR/photos")
chown -R "$APP_USER:$APP_USER" "$DATA_DIR/photos"

# Names and addresses the app answers to (blocks DNS-rebinding tricks from other websites).
# EXTRA_HOSTS is remembered in /etc/default/fitness-planner so updates keep it.
DEFAULTS=/etc/default/$SERVICE
if [ -n "${EXTRA_HOSTS:-}" ]; then
  printf 'EXTRA_HOSTS="%s"\n' "$EXTRA_HOSTS" > "$DEFAULTS"
elif [ -f "$DEFAULTS" ]; then
  # shellcheck disable=SC1090
  . "$DEFAULTS"
fi
ALLOWED="localhost,127.0.0.1,$(hostname)"
for ip in $(hostname -I); do ALLOWED="$ALLOWED,$ip"; done
[ -n "${EXTRA_HOSTS:-}" ] && ALLOWED="$ALLOWED,$EXTRA_HOSTS"

echo "==> Writing $UNIT"
cat >"$UNIT" <<EOF
[Unit]
Description=Fitness Planner
After=network-online.target
Wants=network-online.target

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment=FP_DB=$DATA_DIR/fitness.db
Environment=FP_ALLOWED_HOSTS=$ALLOWED
ExecStart=$APP_DIR/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $PORT --no-server-header
Restart=on-failure
RestartSec=3
UMask=0027
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
EOF

# Extra sandboxing in a drop-in, so it can be backed out without touching the main unit
# if this container's kernel/LXC settings don't support some of it.
mkdir -p "$DROPIN_DIR"
rm -f "$DROPIN_DIR/hardening.conf.disabled"
cat >"$DROPIN_DIR/hardening.conf" <<'EOF'
[Service]
CapabilityBoundingSet=
AmbientCapabilities=
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
ProtectProc=invisible
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~@privileged
EOF

start_service() {
  systemctl daemon-reload
  systemctl enable "$SERVICE" >/dev/null 2>&1
  systemctl restart "$SERVICE"
  sleep 3
  systemctl is-active --quiet "$SERVICE"
}

if start_service; then
  echo "==> Running with the full sandbox."
else
  echo "!!  Service didn't start with the extra sandbox; retrying without it." >&2
  journalctl -u "$SERVICE" -n 15 --no-pager >&2 || true
  mv "$DROPIN_DIR/hardening.conf" "$DROPIN_DIR/hardening.conf.disabled"
  if start_service; then
    echo "==> Running with the basic sandbox only (see $DROPIN_DIR/hardening.conf.disabled)."
  else
    echo "Service failed to start. Logs:" >&2
    journalctl -u "$SERVICE" -n 30 --no-pager >&2
    exit 1
  fi
fi

echo "==> Version $(git -C "$APP_DIR" log -1 --format='%h %s' 2>/dev/null || echo unknown)"
echo "==> Open http://$(hostname -I | awk '{print $1}'):$PORT on your phone."
