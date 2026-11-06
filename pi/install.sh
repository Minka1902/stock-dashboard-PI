#!/usr/bin/env bash
# Install (or repair) the Stock Signal Dashboard on a Raspberry Pi as a boot-start
# systemd service, reached privately over Tailscale. Safe to re-run: it never
# overwrites your stocks.env, your database or a local git checkout.
#
#   sudo ./pi/install.sh --admin-email you@example.com
#
# Before it: Raspberry Pi OS Lite (64-bit), and Tailscale installed and logged in
# (curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up) with
# HTTPS certificates enabled for the tailnet. See pi/README.md.
set -euo pipefail
HERE=$(dirname "$(readlink -f "$0")")
# shellcheck source=pi/common.sh
source "$HERE/common.sh"

REPO_URL=https://github.com/Minka1902/stock-dashboard-PI.git
BRANCH=main
ADMIN_EMAIL=
PACKAGES=(git curl python3-venv poppler-utils chromium sqlite3 polkitd)

usage() {
	echo "usage: sudo $0 [--admin-email EMAIL] [--repo URL] [--branch NAME]"
	exit 2
}

while (($#)); do
	case $1 in
	--admin-email) ADMIN_EMAIL=${2:-} && shift ;;
	--repo) REPO_URL=${2:-} && shift ;;
	--branch) BRANCH=${2:-} && shift ;;
	*) usage ;;
	esac
	shift
done

set_env() { # set_env FILE KEY VALUE: replace KEY's line, or append it
	if grep -q "^$2=" "$1"; then
		sed -i "s|^$2=.*|$2=$3|" "$1"
	else
		echo "$2=$3" >>"$1"
	fi
}

(($(id -u) == 0)) || die "run it with sudo"
[[ $(uname -m) == aarch64 ]] || warn "$(uname -m) is not 64-bit ARM; the Pi target is Raspberry Pi OS 64-bit"

say "system packages"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${PACKAGES[@]}"

say "Tailscale"
command -v tailscale >/dev/null || die "Tailscale is not installed: curl -fsSL https://tailscale.com/install.sh | sh"
TS_HOST=$(tailscale status --json 2>/dev/null |
	python3 -c 'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null) ||
	die "Tailscale is not logged in: sudo tailscale up"
[[ -n $TS_HOST ]] || die "Tailscale gave this Pi no DNS name: enable MagicDNS for the tailnet"
ORIGIN="https://$TS_HOST"

say "user, directories"
id -u "$SERVICE_USER" >/dev/null 2>&1 ||
	useradd --system --user-group --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR" "$DATA_DIR/db" "$DATA_DIR/backups" "$LOG_DIR" "$APP_DIR"
install -d -o root -g "$SERVICE_USER" -m 750 "$CONF_DIR"

say "code: $APP_DIR"
if [[ ! -d $APP_DIR/.git ]]; then
	as_service_user git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
	as_service_user git -C "$APP_DIR" fetch --quiet origin || warn "could not fetch; keeping the current checkout"
fi

say "Python environment"
[[ -x $VENV_PY ]] || as_service_user python3 -m venv "$APP_DIR/backend/.venv"
as_service_user "$VENV_PY" -m pip install --quiet --disable-pip-version-check -r "$APP_DIR/backend/requirements.txt"
as_service_user "$VENV_PY" -m compileall -q "$APP_DIR/backend/app"

say "configuration: $CONF_DIR"
if [[ ! -f $CONF_DIR/stocks.env ]]; then
	install -o root -g "$SERVICE_USER" -m 640 "$APP_DIR/pi/stocks.env.example" "$CONF_DIR/stocks.env"
fi
if [[ -n $ADMIN_EMAIL ]]; then
	set_env "$CONF_DIR/stocks.env" STOCKS_ADMIN_EMAIL "${ADMIN_EMAIL,,}"
fi
grep -q '^STOCKS_ADMIN_EMAIL=.\+' "$CONF_DIR/stocks.env" ||
	die "no admin email yet: re-run with --admin-email you@example.com"
# Derived from where things live and from the tailnet name; rewritten on every run.
cat >"$CONF_DIR/install.env" <<EOF
# Written by pi/install.sh on every run; edit stocks.env instead.
STOCKS_DB_PATH=$DATA_DIR/db/stocks.db
STOCKS_BACKUP_DIR=$DATA_DIR/backups
STOCKS_LOG_DIR=$LOG_DIR
STOCKS_CORS_ORIGINS=$ORIGIN
STOCKS_OAUTH_REDIRECT_BASE=$ORIGIN
STOCKS_COOKIE_SECURE=1
STOCKS_HSTS_SECONDS=31536000
STOCKS_TRUSTED_PROXY_IPS=127.0.0.1,::1
EOF
chown root:"$SERVICE_USER" "$CONF_DIR/install.env"
chmod 640 "$CONF_DIR/install.env"

say "running unattended: watchdog, swap, journal, clock"
CONFIG_TXT=/boot/firmware/config.txt
[[ -f $CONFIG_TXT ]] || CONFIG_TXT=/boot/config.txt
REBOOT=
if [[ -f $CONFIG_TXT ]] && ! grep -q '^dtparam=watchdog=on' "$CONFIG_TXT"; then
	echo 'dtparam=watchdog=on' >>"$CONFIG_TXT"
	REBOOT=1
fi
install -d /etc/systemd/system.conf.d /etc/systemd/journald.conf.d
printf '[Manager]\nRuntimeWatchdogSec=15s\n' >/etc/systemd/system.conf.d/stocks-watchdog.conf
printf '[Journal]\nSystemMaxUse=64M\n' >/etc/systemd/journald.conf.d/stocks.conf
if ! swapon --show=NAME --noheadings | grep -q zram; then
	DEBIAN_FRONTEND=noninteractive apt-get install -y -qq zram-tools
	printf 'ALGO=zstd\nPERCENT=50\n' >/etc/default/zramswap
	systemctl restart zramswap.service
	systemctl disable --now dphys-swapfile.service 2>/dev/null || true
fi
if systemctl is-active --quiet systemd-timesyncd.service; then
	systemctl enable systemd-time-wait-sync.service
else
	warn "systemd-timesyncd is not the NTP client; make sure the clock is synced before the service starts"
fi
systemctl daemon-reexec
systemctl restart systemd-journald

say "web app (prebuilt by CI)"
STOCKS_LOG_DIR=$LOG_DIR as_service_user python3 "$APP_DIR/pi/update.py" fetch-dist \
	"$(as_service_user git -C "$APP_DIR" rev-parse HEAD)"

say "services"
install -m 644 "$APP_DIR"/pi/stocks-{dashboard,update,healthcheck}.service "$APP_DIR/pi/stocks-healthcheck.timer" /etc/systemd/system/
install -m 644 "$APP_DIR/pi/50-stocks-dashboard.rules" /etc/polkit-1/rules.d/
systemctl daemon-reload
systemctl enable stocks-dashboard.service stocks-healthcheck.timer
systemctl restart stocks-dashboard.service
systemctl start stocks-healthcheck.timer
tailscale serve --bg --https=443 "http://127.0.0.1:$PORT" >/dev/null ||
	die "tailscale serve failed: enable HTTPS certificates for the tailnet (admin console -> DNS)"

say "waiting for the dashboard to answer"
for _ in $(seq 90); do
	curl -fsS --max-time 5 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
	sleep 2
done
curl -fsS --max-time 5 "http://127.0.0.1:$PORT/api/health" >/dev/null ||
	die "the service did not answer; see: journalctl -u stocks-dashboard -n 100"

say "done: $ORIGIN"
echo "Sign up there with $(grep '^STOCKS_ADMIN_EMAIL=' "$CONF_DIR/stocks.env" | cut -d= -f2) — that account is the admin."
echo "Secrets and keys: sudo nano $CONF_DIR/stocks.env, then sudo systemctl restart stocks-dashboard"
[[ -z $REBOOT ]] || warn "reboot once to enable the hardware watchdog: sudo reboot"
