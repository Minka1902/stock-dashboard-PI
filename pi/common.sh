# shellcheck shell=bash disable=SC2034  # the variables are used by the scripts that source this
# Shared by install.sh, uninstall.sh and healthcheck.sh. The unit files in this
# directory hard-code the same paths (systemd does not expand variables in
# paths), so change both together.

APP_DIR=/opt/stocks-dashboard
DATA_DIR=/var/lib/stocks-dashboard
LOG_DIR=/var/log/stocks-dashboard
CONF_DIR=/etc/stocks-dashboard
SERVICE_USER=stocks
SERVICE=stocks-dashboard.service
PORT=8000
VENV_PY="$APP_DIR/backend/.venv/bin/python"
# pi/update.py and app/updater.py treat a "running" update older than this as dead.
STALE_UPDATE_SECONDS=1800

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

as_service_user() { runuser -u "$SERVICE_USER" -- "$@"; }

# 0 while pi/update.py is mid-update: nothing should restart the service then.
update_running() {
	python3 - "$LOG_DIR/update-status.json" "$STALE_UPDATE_SECONDS" <<'PY'
import json, sys
from datetime import datetime, timezone
try:
    s = json.load(open(sys.argv[1], encoding="utf-8"))
    at = datetime.fromisoformat(str(s.get("updated_at") or s["started_at"]).replace("Z", "+00:00"))
    fresh = (datetime.now(timezone.utc) - at).total_seconds() < float(sys.argv[2])
    sys.exit(0 if s.get("state") == "running" and fresh else 1)
except (OSError, ValueError, KeyError):
    sys.exit(1)
PY
}
