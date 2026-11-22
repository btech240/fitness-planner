#!/usr/bin/env bash
# Pull the latest code and restart. Run as root:   bash deploy/update.sh
# Your data in /var/lib/fitness-planner is not touched.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE=fitness-planner

[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }

echo "==> Pulling latest code"
git -C "$APP_DIR" pull --ff-only

echo "==> Updating Python packages"
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> Restarting"
systemctl restart "$SERVICE"
sleep 2
if systemctl is-active --quiet "$SERVICE"; then
  echo "==> Updated to $(git -C "$APP_DIR" log -1 --format='%h %s')"
else
  echo "Service failed to start. Logs:" >&2
  journalctl -u "$SERVICE" -n 30 --no-pager >&2
  exit 1
fi
