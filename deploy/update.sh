#!/usr/bin/env bash
# Pull the latest code, then run the (freshly pulled) installer, which updates packages,
# photos, the service definition and restarts. Run as root:   bash deploy/update.sh
# Your data in /var/lib/fitness-planner is not touched.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }

echo "==> Pulling latest code"
git -C "$APP_DIR" pull --ff-only

exec bash "$APP_DIR/deploy/install.sh"
