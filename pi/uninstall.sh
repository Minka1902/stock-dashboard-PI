#!/usr/bin/env bash
# Remove the dashboard's services and code. Your database, backups, logs and
# stocks.env stay unless you pass --purge-data. The system-wide settings
# install.sh made (hardware watchdog, zram swap, journal size) are kept.
#
#   sudo ./pi/uninstall.sh [--purge-data]
set -euo pipefail
# shellcheck source=pi/common.sh
source "$(dirname "$(readlink -f "$0")")/common.sh"

PURGE=
case ${1:-} in
"") ;;
--purge-data) PURGE=1 ;;
*) echo "usage: sudo $0 [--purge-data]" && exit 2 ;;
esac
(($(id -u) == 0)) || die "run it with sudo"

say "services"
systemctl disable --now stocks-healthcheck.timer stocks-dashboard.service 2>/dev/null || true
rm -f /etc/systemd/system/stocks-{dashboard,update,healthcheck}.service \
	/etc/systemd/system/stocks-healthcheck.timer /etc/polkit-1/rules.d/50-stocks-dashboard.rules
systemctl daemon-reload
command -v tailscale >/dev/null && { tailscale serve --https=443 off >/dev/null 2>&1 || true; }

say "code: $APP_DIR"
rm -rf "$APP_DIR"

if [[ -n $PURGE ]]; then
	say "data, logs, configuration, user"
	rm -rf "$DATA_DIR" "$LOG_DIR" "$CONF_DIR"
	userdel "$SERVICE_USER" 2>/dev/null || true
else
	say "kept $DATA_DIR (database, backups), $LOG_DIR and $CONF_DIR; --purge-data removes them"
fi
