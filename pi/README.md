# Running on a Raspberry Pi

The dashboard runs headless on the Pi as a systemd service. It starts at boot,
restarts itself after any crash or hang, waits out network outages, backs up its
database every night, and is reached privately from your phone or PC over
Tailscale.

## What you need

| | |
|---|---|
| Board | Raspberry Pi 3 B/B+ (1 GB) or newer. A Pi 4 (2 GB+) or Pi 5 gives the headless-browser margin-debt tier comfortable headroom |
| Power | The official 2.5 A (Pi 3) / 3 A (Pi 4) / 5 A (Pi 5) supply. Under-voltage is the most common cause of crashes and SD corruption; the Server page shows it |
| Storage | A 32 GB+ A2 microSD card, or better a USB SSD (a 3B+ boots from USB as is; a 3B needs `program_usb_boot_mode=1` once). Optionally a USB stick for backups |
| Network | Ethernet preferred; Wi-Fi works |
| OS | Raspberry Pi OS **Lite (64-bit)**, Trixie (Python 3.13) |

## Install

1. Flash Raspberry Pi OS Lite (64-bit) with Raspberry Pi Imager. In its settings
   set a hostname (e.g. `stocks-pi`), your user, and enable SSH.
2. Install Tailscale on the Pi and log in:

   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   ```

   ```bash
   sudo tailscale up
   ```

   In the Tailscale admin console → DNS, turn on **MagicDNS** and **HTTPS
   certificates** (needed for the `https://<pi>.<tailnet>.ts.net` address).
3. Clone and install (the branch must be merged to `main` first: the Pi tracks
   `main`, and CI publishes the web app only for `main`):

   ```bash
   git clone https://github.com/Minka1902/stock-dashboard-PI.git
   ```

   ```bash
   sudo stock-dashboard-PI/pi/install.sh --admin-email you@example.com
   ```

   It prints the dashboard's address when done. Reboot once if it asks to (that
   turns the hardware watchdog on). The clone you ran it from can be deleted;
   the installed copy lives in `/opt/stocks-dashboard`.
4. Open the address on a device in your tailnet and **create the account with
   the admin email**. That account is approved and admin; anyone else who signs
   up waits on the Server page until you approve them.

Re-running `install.sh` is safe. It repairs the install and never touches
`stocks.env`, the database or the checkout.

## Settings and keys

Every setting is an environment variable in `/etc/stocks-dashboard/stocks.env`
(SEC user agent, Telegram, SMTP, Alpaca, OAuth, API keys…; the file lists them
all). Edit it, then restart:

```bash
sudo nano /etc/stocks-dashboard/stocks.env
```

```bash
sudo systemctl restart stocks-dashboard
```

`/etc/stocks-dashboard/install.env` holds what `install.sh` derives (paths, the
tailnet origin, secure cookies) and is rewritten on every run.

## How it stays up

| Failure | What handles it |
|---|---|
| The app crashes or is killed | `Restart=always` brings it back in 5 s, with no restart limit |
| The app hangs, or a fetch hangs the refresh thread | `stocks-healthcheck.timer` checks `/api/health` every 2 min and restarts after 3 non-ok answers in a row |
| The kernel hangs | The hardware watchdog (`RuntimeWatchdogSec=15s`) reboots the Pi |
| Power cut | WAL mode with `synchronous=NORMAL`: the last moments can be lost, never the database. Boot brings everything back |
| No network | Sources record `deferred: network offline` (one run each, nothing silently skipped) and the minute jobs wait; within 30 s of the network returning every deferred source runs |
| Boot without a synced clock (the Pi 3 has no RTC) | The service waits for NTP (`systemd-time-wait-sync`), because TOTP and every schedule need the right time |
| SD card wear or death | Few fsyncs, a 64 MB journal cap, zram instead of swap on the card, and a verified backup every night (keep it on a USB stick) |
| Memory pressure | zram swap (half of RAM, zstd) |

Every scheduled job is listed on the Server page with its next run. The page
also has a **Raspberry Pi** card showing power/under-voltage, CPU temperature,
memory, disk, last backup, and whether the Pi is online.

## Updating

Info → Updates (admin) compares the Pi with `origin/main` every 6 hours. **Update
now** runs `pi/update.py` as its own unit (`stocks-update.service`):

1. pull
2. pip install
3. download the web app GitHub Actions built for that exact commit (the
   `web-dist` branch; the Pi never runs npm)
4. restart and wait for the new commit to answer

Any failed step rolls everything back. CI publishes a web app only when the
arm64 test suite and the frontend build pass, so a red commit can't be
installed. Protect the `main` branch on GitHub: whatever lands there is what the
Pi installs.

## Backups and restore

The `db_backup` job (03:47) writes a verified copy to
`/var/lib/stocks-dashboard/backups` and keeps 7 (`STOCKS_BACKUP_KEEP`). A copy on
the same card won't survive the card dying, so mount a USB stick at that path,
via `/etc/fstab`, owned by `stocks`.

To restore:

```bash
sudo systemctl stop stocks-dashboard
```

```bash
sudo cp /var/lib/stocks-dashboard/backups/stocks-<stamp>.db /var/lib/stocks-dashboard/db/stocks.db
```

```bash
sudo rm -f /var/lib/stocks-dashboard/db/stocks.db-wal /var/lib/stocks-dashboard/db/stocks.db-shm
```

```bash
sudo chown stocks:stocks /var/lib/stocks-dashboard/db/stocks.db
```

```bash
sudo systemctl start stocks-dashboard
```

## Everyday commands

```bash
systemctl status stocks-dashboard
```

```bash
journalctl -u stocks-dashboard -f
```

```bash
journalctl -u stocks-update -u stocks-healthcheck -n 50
```

```bash
curl -s http://127.0.0.1:8000/api/health
```

```bash
vcgencmd get_throttled
```

The app's own rotating log is `/var/log/stocks-dashboard/backend.log`. The
updater writes `update.log` next to it.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `install.sh`: "Tailscale is not logged in" | `sudo tailscale up`, then re-run |
| `tailscale serve failed` | Turn on HTTPS certificates for the tailnet (admin console → DNS) |
| `fetch-dist`: "no web app built for …" | CI hasn't published `main`'s commit (still running, or red). Check the **pi** workflow on GitHub, then re-run `install.sh` |
| The service doesn't start after boot | `journalctl -b -u stocks-dashboard`. If it waits on time sync, the Pi has no network for NTP |
| Server page: Power "problem now" | Under-voltage: use the official power supply and a short, thick cable |
| `margin_debt` errors with a timeout | The Pi 3 is slow for FINRA's Cloudflare check. Raise `STOCKS_MARGIN_DEBT_BROWSER_TIMEOUT_SECONDS`; the other margin-debt tiers are unaffected |
| Sources sit in "deferred: network offline" | The Pi can't open TCP connections to `STOCKS_NETCHECK_HOSTS` (1.1.1.1:443, 8.8.8.8:53). If your network blocks those, set hosts it allows |
| A sign-up can't see anything | Approve it on the Server page (Sign-ups waiting for approval) |
| Update refused with 409 | The checkout isn't on `main`, has local changes, or can't fast-forward |

## Uninstall

```bash
sudo /opt/stocks-dashboard/pi/uninstall.sh
```

Add `--purge-data` to also delete the database, backups, logs, settings and
the `stocks` user.
