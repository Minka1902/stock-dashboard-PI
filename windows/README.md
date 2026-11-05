# Windows service

Runs the dashboard backend as a Windows service so it collects data
continuously — from boot, before anyone logs in, and restarting itself on crash.

Without this, the scheduler only runs while someone has `start.bat` open and
stays logged in, so the 3-minute ingest cycle, the pre-market digest and the
nightly jobs simply don't happen.

## One-click installer

[`SignalSetup.exe`](https://github.com/Minka1902/stock-dashboard/releases/latest/download/SignalSetup.exe)
does everything in this document for you. It is a bootstrapper, not a bundle: it ships
`setup/bootstrap.ps1` plus a checksum-verified `nssm.exe`, and fetches the rest.

1. **Prerequisites.** git, Node.js (20.19+ / 22.12+) and Python 3.11+, installed through `winget` when
   missing. All three must be **machine-wide**, because LocalSystem sees neither your user PATH nor a
   per-user Python (the venv redirects to its base interpreter at every start). Python is looked up in
   `HKLM` (PEP 514) for exactly that reason.
2. **Code.** `git clone` of `main` into `C:\Program Files\SignalDashboard\repo`. Program Files is
   deliberate: this code runs as LocalSystem, and only administrators can write there.
3. **Dependencies.** The backend venv plus `pip install`, then `npm install --no-save` in `frontend\`.
4. **Service.** `install-service.ps1`, then `install-desktop.ps1`.

The console window shows the progress, and a transcript goes to
`C:\ProgramData\SignalDashboard\logs\setup.log`. The finish page offers to launch the app.

- **Repair / upgrade.** Run it again. The checkout is fast-forwarded (never reset) and the service is
  re-registered.
- **Update on launch.** Each start of the desktop app checks for updates through the in-app updater
  below. See `desktop/README.md`.
- **Uninstall** (Settings → Apps) removes the service, the checkout and the shortcut. It **keeps**
  `C:\ProgramData\SignalDashboard` (database, logs). `windows\service.env` lives in the checkout, so
  copy it out first if it holds secrets you want to keep.
- **Building it.** `.github/workflows/installer.yml` builds it on `windows-latest` with Inno Setup
  (`setup/SignalSetup.iss`). A PR that touches `windows/setup/` gets the exe as an artifact, and a `v*`
  tag attaches it to the release. The workflow only bundles an `nssm.exe` that matches
  `$script:NssmSha256` in `Common.ps1` (see NSSM below).

## Install

From an **elevated** PowerShell, in the repo root:

```powershell
.\start.bat -Install          # create the venv, if you haven't already
cd frontend; npm run build; cd ..
.\windows\install-service.ps1
```

That registers `SignalDashboard`, starts it, and waits for `/api/health`.
Open <http://127.0.0.1:8000>.

Re-running `install-service.ps1` is the upgrade path — it removes and
re-registers the service, so it's safe to run repeatedly.

### NSSM

The service is hosted by [NSSM](https://nssm.cc). The repo carries no binaries,
so the installer fetches it, and only accepts the download if its SHA-256 matches
`$script:NssmSha256` in `windows\Common.ps1`. NSSM ends up with SYSTEM-level
process control, so a mismatch is fatal. The pin is the zip the owner downloaded
from nssm.cc on 2026-09-29, and the `SignalSetup.exe` build is checked against
the same pin.

```powershell
# Default: download and check against the pinned hash
.\windows\install-service.ps1

# Use a copy you already trust
.\windows\install-service.ps1 -NssmPath C:\tools\nssm.exe

# nssm.cc changed the file: verify the new zip by hand, then re-pin
Get-FileHash .\nssm-2.24.zip -Algorithm SHA256
.\windows\install-service.ps1 -NssmSha256 <hash>   # and update Common.ps1
```

## Day to day

```powershell
.\windows\service-control.ps1 -Status     # no elevation needed
.\windows\service-control.ps1 -Restart    # after editing service.env
.\windows\service-control.ps1 -Stop
.\windows\service-control.ps1 -Logs       # follow backend.log
```

## Updating from the app

**Info / Guide → Updates** shows the running version and commit, and whether
`origin/main` on GitHub has anything newer. The server checks every 6 hours
(`app/updater.py`, first run ~2 minutes after start), caches the result for an
hour, and **Check now** forces a fresh `git fetch`. When an update is available,
a dot appears on the account avatar and next to *Info / Guide*. If git is
missing, or GitHub can't be reached, the section says so. It never reports
"up to date" when it doesn't actually know.

**Update now** is for admins only. The server refuses it (HTTP 409, with the
reason shown under the button) unless all of these hold:

- the checkout is on `main`;
- no *tracked* file has local changes (untracked files are fine);
- there are no local commits missing from `origin/main`, so a fast-forward is
  possible;
- there is something to pull.

It then spawns `windows\update.ps1` detached. The script runs these steps:

1. `git pull --ff-only origin main`
2. `backend\.venv\Scripts\python.exe -m pip install -r requirements.txt`
3. `npm install --no-save` then `npm run build` in `frontend\`. It uses
   `npm install` rather than `npm ci`, because `ci` refuses outright when the
   lockfile has drifted from `package.json`. `--no-save` keeps npm from
   rewriting `package-lock.json`, which would leave a tracked change behind and
   block the next update.
4. The restart depends on how the backend is running:
   - **Service:** `Restart-Service SignalDashboard`, then wait until
     `/api/health` reports the new `commit`.
   - **Dev** (`start.ps1`): touch `app\version.py` so `uvicorn --reload`
     reloads now that the new packages are in, and tell the user to reload the
     page.

The UI follows progress through `GET /api/update/status`. That endpoint stops
answering while the service restarts, so the UI polls `/api/health` until its
`commit` changes, then reloads itself.

Progress and logs go to the log directory (`C:\ProgramData\SignalDashboard\logs\`
for the service, `backend\logs\` in dev):

```
update-status.json   current/last run: state, per-step state + detail
update.log           full transcript of every git / pip / npm call
```

**Rollback.** If any step after the pull fails, the script runs
`git reset --hard <previous commit>`, reinstalls the old requirements,
rebuilds the old frontend, and restarts. The UI then shows `rolled_back` with
the reason. If the rollback itself fails, the state is `failed` and
`update.log` says what is left to do by hand.

**Service or dev?** The server works this out itself; nothing needs to be
configured. It is in service mode exactly when the running `SignalDashboard`
service's PID (`sc queryex`) is one of the Python process's ancestors, because
NSSM runs Python as its direct child. Otherwise it is in dev mode. Set
`STOCKS_UPDATE_MODE=service|dev` to override that.

Things worth knowing:

- **pip and loaded DLLs.** A running server holds its compiled extensions
  open, and Windows won't let pip replace a loaded `.pyd`. In service mode, if
  pip fails, the script stops the service, retries, and starts it again at the
  end. Dev mode has nothing safe to stop, so a failure there rolls back.
- **Surviving the restart.** NSSM stops a service by killing its process
  *tree*, which it walks by parent PID. The updater relaunches itself once, so
  its parent is a process that has already exited, and it keeps running while
  the service stops and starts.
- **Git ownership.** Every git call passes `-c safe.directory=<repo>`. Without
  it, git running as LocalSystem refuses a tree owned by your user ("dubious
  ownership").
- **PATH.** `git` and `npm` must be on the **machine** PATH, because
  LocalSystem doesn't see your user PATH. If either is missing, the update
  fails before anything changes, and the UI says which one.
- **File ownership.** Files that the service pulls or builds are created by
  LocalSystem. Your account can still edit them as an administrator.

## Where things live

```
C:\ProgramData\SignalDashboard\
  db\stocks.db              accounts, TOTP secrets, all collected signals
  logs\backend.log(.1-.5)   the app's own rotating log
  logs\service-stdout.log   uvicorn stdout, rotated by NSSM
  logs\service-stderr.log   import-time tracebacks  <- read this one first
  ms-playwright\            headless Chromium for the margin-debt fetch
```

ACL'd to SYSTEM + Administrators by the installer. Secrets live in
`windows\service.env` (gitignored; created from `service.env.example` on first
install).

The **code** stays in this repo — only data moves to ProgramData. That means
moving or renaming the repo folder breaks the service, and checking out a broken
branch takes down the always-on backend at its next restart.
`service-control.ps1 -Status` warns if the path has gone missing.

## Configuration and secrets

The backend has no `.env` loader — `app/config.py` reads `os.environ` once at
import — and a LocalSystem service inherits nothing from your shell. So
`windows\service.env` is the only way secrets (SMTP, Twilio, API keys, OAuth)
reach it. The installer bakes every `KEY=VALUE` line into the service's
environment block; edit the file and `-Restart` to apply.

These are set by the installer and must **not** be duplicated in `service.env`:
`STOCKS_DB_PATH`, `STOCKS_LOG_DIR`, `STOCKS_STATIC_DIR`, `STOCKS_CORS_ORIGINS`,
`STOCKS_OAUTH_REDIRECT_BASE`, `PLAYWRIGHT_BROWSERS_PATH`.

## Headless browser (margin debt)

FINRA's Cloudflare front refuses plain HTTP clients (the Query API answers 401,
the statistics page 403), so the `margin_debt` source's last tier loads the page
in a real headless Chromium via Playwright and downloads the workbook through
the same browser context. It runs weekly, so it costs one Chromium launch a
week, about 15 seconds.

Playwright's default browser cache is `%LOCALAPPDATA%\ms-playwright` of
whoever ran `playwright install`, and **LocalSystem never looks there**. So
the installer:

1. runs `python -m playwright install chromium` with
   `PLAYWRIGHT_BROWSERS_PATH=C:\ProgramData\SignalDashboard\ms-playwright`, and
2. sets that same `PLAYWRIGHT_BROWSERS_PATH` in the service's environment block.

This step warns rather than failing the install, because only that one tier
depends on it. Skip it with `-SkipBrowserInstall`. If the browser is missing, the
source status on the Server page reads `headless browser not installed — run: …`
and names the path. To install it later, elevated:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = 'C:\ProgramData\SignalDashboard\ms-playwright'
.\backend\.venv\Scripts\python.exe -m playwright install chromium
.\windows\service-control.ps1 -Restart
```

After upgrading the `playwright` package in `requirements.txt`, re-run the
installer (or the commands above). Each Playwright release pins its own browser
build, and until you do, the tier reports it as not installed.

A dev instance (`start.ps1`) uses the per-user cache instead. There, run
`.venv\Scripts\python.exe -m playwright install chromium` once from `backend\`.

## Two databases

The service uses `C:\ProgramData\SignalDashboard\db\stocks.db`. A dev instance
started with `start.bat` uses `backend\stocks.db`. **They are separate** —
separate accounts, separate TOTP enrolments, separate data. That's deliberate
(restarting a dev server must not disturb live collection) but it does surprise
people.

To seed the service from an existing dev database, on first install:

```powershell
.\windows\install-service.ps1 -ImportExistingDb
```

That uses SQLite's online backup API, which folds the `-wal` in and produces one
consistent file — copying `stocks.db` on its own would lose every
committed-but-uncheckpointed transaction. The source is never deleted. There is
no merge path back afterwards.

## Running dev alongside the service

The service owns port 8000, and it restarts itself, so `start.ps1 -Kill` cannot
free that port — `Assert-PortFree` now detects this and says so. Use a different
port instead:

```powershell
.\start.bat dev -ApiPort 8001
```

The Vite proxy follows `-ApiPort` automatically. Or stop the service first with
`.\windows\service-control.ps1 -Stop`.

## Reaching it from the internet

The service stays bound to `127.0.0.1`. A tunnel dials loopback, so nothing about
the bind changes — there is no firewall rule, no port-forward, and the dashboard
is never on your LAN.

[Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel) gives a
stable `https://<machine>.<tailnet>.ts.net` on the free plan, with a cert issued
for you:

```powershell
winget install --id Tailscale.Tailscale -e
tailscale up                       # authenticates this machine
tailscale serve --bg 8000          # tailnet-only first — verify before going public
tailscale funnel --bg 8000         # public 443 -> http://127.0.0.1:8000
tailscale funnel status            # prints the public URL
```

Then re-run the installer with that URL (re-running is the normal upgrade path):

```powershell
.\windows\install-service.ps1 -PublicOrigin https://<machine>.<tailnet>.ts.net
```

`-PublicOrigin` derives everything that has to agree with it: `STOCKS_CORS_ORIGINS`
(public origin **first**, `127.0.0.1` kept second for the desktop shell),
`STOCKS_OAUTH_REDIRECT_BASE`, `STOCKS_COOKIE_SECURE=1`, `STOCKS_HSTS_SECONDS`, and
`STOCKS_TRUSTED_PROXY_IPS`. It also generates an invite code into `service.env` and
prints it once. **Set the origin with this flag, not by hand in `service.env`** —
the ordering rule below is invisible and breaks OAuth silently when violated.

Two things that will quietly kill the tunnel months later:

- **Node key expiry** (default ~180 days). Disable it for this machine in the
  Tailscale admin console, or the URL simply stops resolving one day.
- **The PC sleeping.** Set the power plan to never sleep.

Off switch: `tailscale funnel --https=443 off`.

Once a public origin is set, account creation needs the invite code — the login
page being reachable is not the same as signups being open. This covers the OAuth
callback too, which is a second, equally public account factory: with a code
configured, social login links to accounts that already exist but never creates
one. See `backend/app/registration.py`.

## Why it's configured the way it is

| Setting | Why |
|---|---|
| `Start = SERVICE_DELAYED_AUTO_START` + `DependOnService Tcpip Dnscache` | Still well before any human logs in, but doesn't race DHCP/DNS on a cold boot and mark every source errored on the first cycle. |
| `AppParameters ... --no-supervise` | Runs uvicorn **in-process**. `run_server.py`'s own restart loop is bypassed because NSSM already restarts on crash, and two supervisors would fight over the port. |
| `AppNoConsole 0`, `AppStopMethodSkip 0`, `AppStopMethodConsole 20000` | These three are what make a graceful stop work. `app/main.py`'s lifespan ends with `PRAGMA wal_checkpoint(TRUNCATE)`; without the Ctrl-C path every stop becomes a `TerminateProcess` and the WAL is left uncheckpointed. `AppNoConsole 1` silently disables it. |
| `AppExit Default Restart`, `AppRestartDelay 5000`, `AppThrottle 10000` | Restart on crash, with backoff for a process that dies immediately (almost always misconfiguration). |
| Bound to `127.0.0.1` | A LocalSystem service on `0.0.0.0` would expose the dashboard to the LAN. Loopback needs no firewall rule — and it stays loopback even when the app is public, because the tunnel dials it from this machine. |
| `STOCKS_CORS_ORIGINS` app origin **first** | `routes_oauth.py::_frontend_origin()` reuses `CORS_ORIGINS[0]` as the post-login redirect target. If it isn't first, a completed OAuth login redirects to a different host — a different cookie jar — and the session appears to vanish. **Don't reorder it.** |
| `127.0.0.1`, never `localhost` | uvicorn binds IPv4 only, and Windows resolves `localhost` to `::1` first. They're also separate cookie jars. |
| `ObjectName LocalSystem` | Simplest thing that works. Note this process fetches and parses untrusted remote content (RSS, SEC filings, a FINRA XLS via `xlrd`/`openpyxl`) — see the hardening note below. |

## Troubleshooting

**Service starts, then stops immediately.** Read `logs\service-stderr.log`
first. Import-time failures never reach `backend.log` — the commonest is a bad
`STOCKS_CORS_ORIGINS` (a `*` entry raises `ValueError` at import, by design).
The other is `STOCKS_REGISTRATION=invite` with no `STOCKS_INVITE_CODE`, which
also raises at import: an empty code would compare equal to an empty submission
and open registration to anyone with the URL.

**"I can't create a second account."** That's the invite gate, not a bug — a
public origin sets `STOCKS_REGISTRATION=invite`. The code is in
`windows\service.env`. To reopen signups entirely, delete both
`STOCKS_REGISTRATION` and `STOCKS_INVITE_CODE` from that file and restart.

**Public URL stopped working after months.** Almost certainly Tailscale node key
expiry. `tailscale status` will say so; disable key expiry for this machine in
the admin console.

**Dashboard loads but the page is blank.** The SPA mount is a silent no-op when
`frontend\dist\index.html` is missing. Rebuild the frontend and restart.

**WAL file keeps growing.** The graceful-stop settings above aren't taking
effect. Check `AppNoConsole` is `0` (`nssm edit SignalDashboard`).

**Uninstall.**

```powershell
.\windows\uninstall-service.ps1              # keeps your data
.\windows\uninstall-service.ps1 -PurgeData   # deletes the database too
```

## Hardening note

`ObjectName LocalSystem` is a lot of authority for a process that parses hostile
input from the internet. `NT AUTHORITY\LocalService` still has outbound network
access — all this app needs — with far less local authority. It costs one extra
ACL grant on the data directory:

```powershell
nssm set SignalDashboard ObjectName "NT AUTHORITY\LocalService"
icacls C:\ProgramData\SignalDashboard /grant "NT AUTHORITY\LocalService:(OI)(CI)M"
```

The margin-debt tier adds to this. Once a week it runs a headless Chromium that
executes finra.org's JavaScript under the service account, and Playwright launches
Chromium without its sandbox by default. That's one more reason to prefer
`LocalService`. The browser directory is under the same data root, so the grant
above already covers it.

Also worth knowing: `service.env` is plaintext and readable by any local
administrator. That is the same trust level as the service itself, so it's
proportionate here, but the ACL is not a boundary against local admins.
