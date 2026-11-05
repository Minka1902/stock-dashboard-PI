<#
.SYNOPSIS
    Register the Signal dashboard backend as a Windows service (via NSSM).

.DESCRIPTION
    Installs the FastAPI backend + APScheduler ingest loop as a service that
    starts at boot, before any user logs in, and restarts itself on crash.

    Data lives machine-wide under C:\ProgramData\SignalDashboard, NOT in the
    repo, because a LocalSystem service has no user profile to write into.

    Re-running this script is safe: an existing service is stopped and removed
    first, so it doubles as the upgrade path.

.PARAMETER Port
    Port to serve on (default 8000). Bound to 127.0.0.1 only.

.PARAMETER NssmPath
    Use an nssm.exe you already have instead of downloading one.

.PARAMETER NssmSha256
    Expected SHA-256 of the NSSM zip. See -TrustNssmDownload.

.PARAMETER TrustNssmDownload
    Allow downloading NSSM without a pinned checksum. The hash that was
    actually downloaded is printed so you can pin it in Common.ps1.

.PARAMETER SkipBuild
    Don't run `npm run build`; use the existing frontend\dist.

.PARAMETER ImportExistingDb
    Copy an existing backend\stocks.db into the service's data directory
    using SQLite's online backup API. Never deletes the source.

.PARAMETER SkipBrowserInstall
    Don't run `playwright install chromium` into
    C:\ProgramData\SignalDashboard\ms-playwright. Only the margin-debt source's
    headless-browser tier needs it; without it that tier reports
    "headless browser not installed" in the source status.

.EXAMPLE
    .\windows\install-service.ps1
.EXAMPLE
    .\windows\install-service.ps1 -Port 8080 -NssmPath C:\tools\nssm.exe
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$NssmPath,
    [string]$NssmSha256,
    [switch]$TrustNssmDownload,
    [switch]$SkipBuild,
    [switch]$ImportExistingDb,
    [switch]$SkipBrowserInstall,
    [string]$PublicOrigin
)

#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

if ($NssmSha256) { $script:NssmSha256 = $NssmSha256 }
if ($TrustNssmDownload) { $script:AllowUnpinnedNssm = $true }

Write-Host ''
Write-Host '  Signal - install Windows service' -ForegroundColor Cyan
Write-Host ''

Assert-Admin

if ($PublicOrigin) {
    $PublicOrigin = $PublicOrigin.TrimEnd('/')
    if ($PublicOrigin -notmatch '^https://[A-Za-z0-9.-]+$') {
        Fail '-PublicOrigin must be a bare https origin.' @(
            'Example: https://box.tailnet-name.ts.net',
            'No path, no trailing slash, no port. It becomes both the OAuth',
            'redirect base and the first CORS origin, and providers compare',
            'the redirect URI byte-for-byte.')
    }
}

# ---------------------------------------------------------------- preflight --
if (-not (Test-Path $Python)) {
    Fail "No virtualenv at $Python" @(
        'Create it first (from the repo root):',
        '  .\start.bat -Install'
    )
}
# Pre-quoted single string on purpose: Windows PowerShell joins an
# -ArgumentList array without quoting, which hands python a bare `-c import`.
# Same trick as start.ps1's Test-PythonImports.
$probe = Start-Process -FilePath $Python -WindowStyle Hidden -Wait -PassThru `
                       -ArgumentList '-c "import fastapi, uvicorn, apscheduler"'
if ($probe.ExitCode -ne 0) {
    Fail 'The virtualenv is missing dependencies.' @(
        'Reinstall them:',
        "  cd `"$Backend`"",
        '  .venv\Scripts\python.exe -m pip install -r requirements.txt'
    )
}
Write-Step 'preflight' 'venv + deps ok'

# The SPA mount in app/main.py is a silent no-op when index.html is absent --
# you would get a working API serving a blank page, which is a miserable thing
# to debug. Fail here instead.
if (-not $SkipBuild) {
    Write-Step 'build' 'npm run build ...' 'DarkGray'
    Push-Location $Frontend
    try {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { Fail 'npm run build failed -- see the output above.' }
    } finally { Pop-Location }
}
if (-not (Test-Path $DistIndex)) {
    Fail "No frontend build at $DistIndex" @(
        'The backend serves the built SPA from this path; without it the service',
        'comes up as an API with a blank web page.',
        'Build it:  cd frontend; npm run build'
    )
}
Write-Step 'build' 'frontend dist ok'

$nssm = Resolve-Nssm -NssmPath $NssmPath
Write-Step 'nssm' $nssm

# ------------------------------------------------------------ data scaffold --
foreach ($d in @($DataRoot, $DataDb, $DataLogs)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}
# Lock the tree down: the DB holds password hashes and TOTP secrets, and
# service.env holds API keys in plaintext. Inherited ProgramData ACLs let
# ordinary users read; these do not.
& icacls.exe $DataRoot /inheritance:r /grant:r `
    'SYSTEM:(OI)(CI)F' 'Administrators:(OI)(CI)F' | Out-Null
Write-Step 'data' $DataRoot

# ---------------------------------------------------------- headless browser --
# FINRA's Cloudflare front refuses plain HTTP, so margin_debt's last tier drives
# a real headless Chromium through Playwright. A per-user `playwright install`
# lands in that user's %LOCALAPPDATA%, which LocalSystem cannot see, so install
# a machine-wide copy under the (already ACL'd) data root and point the service
# at it via PLAYWRIGHT_BROWSERS_PATH in the environment block below.
#
# Warn, don't Fail: only that one tier depends on it, and without it the source
# status says exactly what to run rather than the whole install stopping.
if ($SkipBrowserInstall) {
    Write-Step 'browser' 'skipped (-SkipBrowserInstall)' 'DarkGray'
} else {
    $pwProbe = Start-Process -FilePath $Python -WindowStyle Hidden -Wait -PassThru `
                             -ArgumentList '-c "import playwright"'
    if ($pwProbe.ExitCode -ne 0) {
        Write-Step 'browser' 'playwright not in the venv -- margin-debt browser tier disabled' 'Yellow'
        Write-Host "    Fix: cd `"$Backend`"; .venv\Scripts\python.exe -m pip install -r requirements.txt" -ForegroundColor Yellow
        Write-Host '         then re-run this installer.' -ForegroundColor Yellow
    } else {
        Write-Step 'browser' "playwright install chromium -> $DataBrowsers ..." 'DarkGray'
        $prevBrowsersPath = $env:PLAYWRIGHT_BROWSERS_PATH
        $env:PLAYWRIGHT_BROWSERS_PATH = $DataBrowsers
        $pwExit = 1
        try {
            & $Python -m playwright install chromium
            $pwExit = $LASTEXITCODE
        } catch {
            Write-Host "    $($_.Exception.Message)" -ForegroundColor Yellow
        } finally {
            $env:PLAYWRIGHT_BROWSERS_PATH = $prevBrowsersPath
        }
        if ($pwExit -ne 0) {
            Write-Step 'browser' 'install failed -- margin-debt browser tier will report it' 'Yellow'
            Write-Host '    Retry later (elevated):' -ForegroundColor Yellow
            Write-Host "      `$env:PLAYWRIGHT_BROWSERS_PATH = '$DataBrowsers'" -ForegroundColor Yellow
            Write-Host "      & '$Python' -m playwright install chromium" -ForegroundColor Yellow
        } else {
            Write-Step 'browser' $DataBrowsers
        }
    }
}

if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $PSScriptRoot 'service.env.example') $EnvFile
    Write-Step 'secrets' 'created windows\service.env from the example'
} else {
    Write-Step 'secrets' 'windows\service.env (existing)'
}

# A public origin means anyone can reach the login page, so account creation
# gets its own gate. The code lives in service.env (gitignored, and already
# merged into the env block below) rather than a parameter, so it never lands
# in PowerShell history.
$script:NewInviteCode = ''
if ($PublicOrigin -and (Read-ServiceEnv).Keys -notcontains 'STOCKS_INVITE_CODE') {
    $bytes = New-Object byte[] 18
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $script:NewInviteCode = [Convert]::ToBase64String($bytes).
        TrimEnd('=').Replace('+', '-').Replace('/', '_')
    Add-Content -Path $EnvFile -Encoding utf8 -Value @(
        '',
        '# Added by install-service.ps1 -PublicOrigin.',
        '# The dashboard is reachable by anyone with the URL; creating an',
        '# account additionally needs this code. Without it POST',
        '# /api/auth/register returns 403 and OAuth links to existing accounts',
        '# only. Delete both lines to reopen registration.',
        'STOCKS_REGISTRATION=invite',
        "STOCKS_INVITE_CODE=$script:NewInviteCode")
    Write-Step 'registration' 'invite code generated into windows\service.env'
}

# ------------------------------------------------------------- existing DB ---
$devDb = Join-Path $Backend 'stocks.db'
if ((Test-Path $devDb) -and -not (Test-Path $DbFile)) {
    if ($ImportExistingDb) {
        # sqlite3's online backup folds the -wal in and yields one consistent
        # file. Copying stocks.db alone while a -wal exists silently loses
        # every committed-but-uncheckpointed transaction.
        & $Python -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()" $devDb $DbFile
        if ($LASTEXITCODE -ne 0) { Fail 'Importing the existing database failed.' }
        Write-Step 'database' 'imported from backend\stocks.db'
    } else {
        Write-Host ''
        Write-Host '  NOTE: an existing dev database was found at' -ForegroundColor Yellow
        Write-Host "        $devDb" -ForegroundColor Yellow
        Write-Host '        The service starts with a SEPARATE, EMPTY database, so you will' -ForegroundColor Yellow
        Write-Host '        register a new account and enrol TOTP again. To copy the dev data' -ForegroundColor Yellow
        Write-Host '        across instead, re-run with -ImportExistingDb.' -ForegroundColor Yellow
        Write-Host ''
    }
}

# --------------------------------------------------------------- reinstall ---
if (Get-DashboardService) {
    Write-Step 'service' 'already exists, replacing' 'DarkGray'
    & $nssm stop $ServiceName 2>&1 | Out-Null      # may already be stopped
    Invoke-Nssm $nssm remove $ServiceName confirm | Out-Null
    Start-Sleep -Milliseconds 500
}

# ---------------------------------------------------------------- register ---
$origin = "http://127.0.0.1:$Port"
$entry  = Join-Path $Backend 'run_server.py'

Invoke-Nssm $nssm install $ServiceName $Python | Out-Null
Invoke-Nssm $nssm set $ServiceName AppParameters `
    "`"$entry`" --host 127.0.0.1 --port $Port --no-supervise" | Out-Null
Invoke-Nssm $nssm set $ServiceName AppDirectory $Backend | Out-Null
Invoke-Nssm $nssm set $ServiceName DisplayName $ServiceDisplay | Out-Null
Invoke-Nssm $nssm set $ServiceName Description ($ServiceDesc -f $Port) | Out-Null
Invoke-Nssm $nssm set $ServiceName ObjectName 'LocalSystem' | Out-Null

# Delayed auto-start: still long before any human logs in, but it stops the
# first _refresh_all cycle from racing DHCP/DNS on a cold boot and marking
# every source as errored.
Invoke-Nssm $nssm set $ServiceName Start SERVICE_DELAYED_AUTO_START | Out-Null
Invoke-Nssm $nssm set $ServiceName DependOnService Tcpip Dnscache | Out-Null

# NSSM is the ONLY supervisor. run_server.py's own restart loop is bypassed
# with --no-supervise; two restart policies would fight over the port.
Invoke-Nssm $nssm set $ServiceName AppExit Default Restart | Out-Null
Invoke-Nssm $nssm set $ServiceName AppRestartDelay 5000 | Out-Null
Invoke-Nssm $nssm set $ServiceName AppThrottle 10000 | Out-Null

# Graceful stop. These four are what protect the WAL checkpoint that
# app/main.py's lifespan runs on shutdown -- AppNoConsole 1 would silently
# disable the Ctrl-C path and turn every stop into a TerminateProcess.
Invoke-Nssm $nssm set $ServiceName AppNoConsole 0 | Out-Null
Invoke-Nssm $nssm set $ServiceName AppStopMethodSkip 0 | Out-Null
Invoke-Nssm $nssm set $ServiceName AppStopMethodConsole 20000 | Out-Null
Invoke-Nssm $nssm set $ServiceName AppStopMethodWindow 5000 | Out-Null
Invoke-Nssm $nssm set $ServiceName AppStopMethodThreads 5000 | Out-Null

Invoke-Nssm $nssm set $ServiceName AppStdout (Join-Path $DataLogs 'service-stdout.log') | Out-Null
Invoke-Nssm $nssm set $ServiceName AppStderr (Join-Path $DataLogs 'service-stderr.log') | Out-Null
Invoke-Nssm $nssm set $ServiceName AppRotateFiles 1 | Out-Null
Invoke-Nssm $nssm set $ServiceName AppRotateOnline 1 | Out-Null
Invoke-Nssm $nssm set $ServiceName AppRotateBytes 10485760 | Out-Null

# Environment. Paths and origins live here (operational, visible in `nssm
# edit`); secrets come from service.env below.
#
# Two orderings matter:
#   * STOCKS_CORS_ORIGINS[0] is reused by routes_oauth.py::_frontend_origin as
#     the post-login redirect target. If the app origin is not FIRST, a
#     completed OAuth login redirects to the wrong host -- a different cookie
#     jar -- and the session appears to vanish. Do not reorder this.
#   * 127.0.0.1, never "localhost": uvicorn binds IPv4 only, and Windows
#     resolves localhost to ::1 first, so a localhost origin can fail to
#     connect at all. It is also a distinct cookie jar from 127.0.0.1.
$envPairs = [ordered]@{
    'PYTHONUNBUFFERED'           = '1'
    'STOCKS_DB_PATH'             = $DbFile
    'STOCKS_LOG_DIR'             = $DataLogs
    'STOCKS_STATIC_DIR'          = (Join-Path $Frontend 'dist')
    'STOCKS_CORS_ORIGINS'        = $origin
    'STOCKS_OAUTH_REDIRECT_BASE' = $origin
    # Set even with -SkipBrowserInstall, so a later manual install into the
    # same path is picked up on the next restart.
    'PLAYWRIGHT_BROWSERS_PATH'   = $DataBrowsers
}
if ($PublicOrigin) {
    # Public origin FIRST, for the _frontend_origin reason above: it has to be
    # the origin the browser is actually on. 127.0.0.1 stays second so the
    # Electron desktop shell (desktop\src\config.js APP_ORIGIN) keeps working.
    $envPairs['STOCKS_CORS_ORIGINS']        = "$PublicOrigin,$origin"
    $envPairs['STOCKS_OAUTH_REDIRECT_BASE'] = $PublicOrigin
    # TLS is terminated by the tunnel, so the cookie can and must be Secure.
    $envPairs['STOCKS_COOKIE_SECURE']       = '1'
    $envPairs['STOCKS_HSTS_SECONDS']        = '31536000'
    # The tunnel dials loopback; only its X-Forwarded-For is believed.
    $envPairs['STOCKS_TRUSTED_PROXY_IPS']   = '127.0.0.1,::1'
}
foreach ($kv in (Read-ServiceEnv).GetEnumerator()) { $envPairs[$kv.Key] = $kv.Value }
$envArgs = @($envPairs.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })
Invoke-Nssm $nssm set $ServiceName AppEnvironmentExtra @envArgs | Out-Null

Write-Step 'service' "registered as $ServiceName"

# -------------------------------------------------------------------- start --
Invoke-Nssm $nssm start $ServiceName | Out-Null
$elapsed = Wait-Health -Port $Port -TimeoutSeconds 60
if ($elapsed -lt 0) {
    Write-Host ''
    Write-Host '  The service was registered but never answered /api/health.' -ForegroundColor Red
    Write-Host ''
    foreach ($log in @('service-stderr.log', 'backend.log')) {
        $p = Join-Path $DataLogs $log
        if (Test-Path $p) {
            Write-Host "  --- last 30 lines of $log ---" -ForegroundColor DarkGray
            Get-Content $p -Tail 30 | ForEach-Object { Write-Host "  $_" }
            Write-Host ''
        }
    }
    Fail 'Service did not become healthy.' @(
        'Import-time failures (a bad STOCKS_CORS_ORIGINS, a missing dependency)',
        'never reach backend.log -- check service-stderr.log first.',
        "Logs: $DataLogs"
    )
}
Write-Step 'health' "ok in ${elapsed}s"

$health = Get-HealthJson -Port $Port
Write-Host ''
Write-Host '  Installed.' -ForegroundColor Green
Write-Host ''
Write-Step 'url'      $origin
if ($PublicOrigin) {
    Write-Step 'public url' $PublicOrigin 'Cyan'
    Write-Step 'signups'    'invite code required'
}
Write-Step 'service'  "$ServiceName (delayed auto-start, LocalSystem)"
Write-Step 'status'   $health.status $(if ($health.status -eq 'ok') { 'Green' } else { 'Yellow' })
Write-Step 'database' $DbFile
Write-Step 'logs'     $DataLogs
Write-Step 'secrets'  $EnvFile
Write-Host ''
Write-Host '  Day to day:' -ForegroundColor DarkGray
Write-Host '    .\windows\service-control.ps1 -Status'
Write-Host '    .\windows\service-control.ps1 -Restart     (after editing service.env)'
Write-Host '    .\windows\service-control.ps1 -Logs'
Write-Host ''
Write-Host '  It now starts automatically at boot, before you log in.' -ForegroundColor DarkGray
Write-Host ''
if ($script:NewInviteCode) {
    Write-Host '  Invite code (shown once; also in windows\service.env):' -ForegroundColor DarkGray
    Write-Host "    $script:NewInviteCode" -ForegroundColor Yellow
    Write-Host '    Anyone creating an account on the public URL must enter it.'
    Write-Host ''
}
