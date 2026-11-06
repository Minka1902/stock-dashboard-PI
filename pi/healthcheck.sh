#!/usr/bin/env bash
# Run by stocks-healthcheck.timer every 2 minutes. Restarts the dashboard after
# MAX_FAILURES health checks in a row that don't answer "ok" — a hung process,
# a stuck refresh thread, a dead scheduler — which Restart=always alone can't
# catch, because the process is still alive.
set -uo pipefail
# shellcheck source=pi/common.sh
source "$(dirname "$(readlink -f "$0")")/common.sh"

MAX_FAILURES=3
STATE=/run/stocks-healthcheck/failures

# Stopped on purpose (or already being restarted by systemd): not our business.
systemctl is-active --quiet "$SERVICE" || exit 0
# An update restarts the service itself and rolls back if it doesn't come up.
if update_running; then echo "update in progress; skipping"; exit 0; fi

status=$(curl -fsS --max-time 15 "http://127.0.0.1:$PORT/api/health" 2>/dev/null \
	| python3 -c 'import json, sys; print(json.load(sys.stdin)["status"])' 2>/dev/null) \
	|| status=unreachable

if [[ $status == ok ]]; then
	echo 0 >"$STATE"
	exit 0
fi
failures=$(($(cat "$STATE" 2>/dev/null || echo 0) + 1))
echo "$failures" >"$STATE"
echo "health check failed ($failures/$MAX_FAILURES): $status"
if ((failures >= MAX_FAILURES)); then
	echo "restarting $SERVICE"
	echo 0 >"$STATE"
	systemctl restart "$SERVICE"
fi
