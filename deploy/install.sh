#!/usr/bin/env bash
# Install (or re-install) Fitness Planner as a systemd service on Debian 12.
# Run as root from a clone of the repo:   bash deploy/install.sh
# Optional: PORT=8080 bash deploy/install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR=/var/lib/fitness-planner
SERVICE=fitness-planner
APP_USER=fitness
PORT="${PORT:-8000}"

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

echo "==> Downloading exercise photos (about 60 MB the first time)"
mkdir -p "$DATA_DIR/photos"
(cd "$APP_DIR" && "$APP_DIR/.venv/bin/python" -m app.fetch_photos "$DATA_DIR/photos")
chown -R "$APP_USER:$APP_USER" "$DATA_DIR/photos"

echo "==> Writing /etc/systemd/system/$SERVICE.service"
cat >"/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Fitness Planner
After=network-online.target
Wants=network-online.target

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment=FP_DB=$DATA_DIR/fitness.db
ExecStart=$APP_DIR/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "$SERVICE" >/dev/null
systemctl restart "$SERVICE"
sleep 2

if systemctl is-active --quiet "$SERVICE"; then
  echo "==> Running. Open http://$(hostname -I | awk '{print $1}'):$PORT on your phone."
else
  echo "Service failed to start. Logs:" >&2
  journalctl -u "$SERVICE" -n 30 --no-pager >&2
  exit 1
fi
