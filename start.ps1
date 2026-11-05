#Requires -Version 5.1
<#
.SYNOPSIS
    Start the Stock Signal Dashboard (backend + frontend).

.DESCRIPTION
    Two run modes, matching the two documented in CLAUDE.md:

      dev  (default)  Two detached, minimized windows: uvicorn --reload on
                      :8000 and the Vite dev server on :5173 (Vite proxies
                      /api -> the backend). Hot reload on both sides.

      prod            Builds frontend/dist once, then serves the SPA *and* the
                      API from a single supervised uvicorn worker on :8000.
                      No Vite, no proxy, one port.

    Never prompts. Anything that would have asked a question is either a flag
    or a hard failure with the command to fix it -- the old prompts blocked
    double-click launches on a window that was already minimized.

    Child output is redirected to logs\, and the backend also writes its own
    rotating log there, so a crash leaves evidence behind instead of dying with
    the console window.

    Exactly one uvicorn worker, always: the APScheduler jobs, TTL caches, rate
    limiter and the shared SQLite connections are all in-process.

.PARAMETER Mode
    'dev' (default) or 'prod'. Positional, so `.\start.ps1 prod` works.

.PARAMETER ApiPort
    Backend port. Default 8000. In dev the Vite proxy target follows this
    automatically via VITE_API_TARGET.

.PARAMETER WebPort
    Vite dev-server port (dev mode only). Default 5173. The backend CORS
    allowlist is pointed at it automatically.

.PARAMETER Install
    Create the venv and install backend/frontend dependencies if missing,
    instead of failing with instructions.

.PARAMETER Kill
    Stop whatever already holds the ports, instead of failing.

.PARAMETER NoBrowser
    Don't open a browser once the API answers /api/health.

.PARAMETER NoSupervise
    prod only: run uvicorn directly rather than under the restart supervisor.

.PARAMETER Stop
    Stop the dashboard and exit.

.PARAMETER Status
    Report what's running -- pids, ports, health, log tails, DB size -- and exit.

.PARAMETER Logs
    Tail the logs and exit (Ctrl+C to stop following).

.EXAMPLE
    .\start.ps1
    Dev: API on :8000 with reload, UI on :5173.

.EXAMPLE
    .\start.ps1 prod -Kill
    Build the frontend, free the port if busy, then serve everything from :8000.

.EXAMPLE
    .\start.ps1 -Status
    Show what's running and where the logs are.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('dev', 'prod')]
    [string]$Mode = 'dev',

    [int]$ApiPort = 8000,
    [int]$WebPort = 5173,
    [switch]$Install,
    [switch]$Kill,
    [switch]$NoBrowser,
    [switch]$NoSupervise,
    [switch]$Stop,
    [switch]$Status,
    [switch]$Logs
)

$ErrorActionPreference = 'Stop'

$Root      = $PSScriptRoot
$Backend   = Join-Path $Root 'backend'
$Frontend  = Join-Path $Root 'frontend'
$Python    = Join-Path $Backend '.venv\Scripts\python.exe'
$DistIndex = Join-Path $Frontend 'dist\index.html'
$RunDir    = Join-Path $Root '.run'
$LogDir    = Join-Path $Backend 'logs'


# ---------------------------------------------------------------- output ----

function Write-Check {
    param([string]$Tag, [string]$Label, [string]$Result, [string]$Color = 'Green')
    Write-Host ("  [{0}] {1,-28} " -f $Tag, $Label) -NoNewline
    Write-Host $Result -ForegroundColor $Color
}

function Write-Note {
    param([string]$Message, [string]$Color = 'DarkGray')
    Write-Host "  $Message" -ForegroundColor $Color
}

function Fail {
    param([string]$Message, [string[]]$Hints = @())
    Write-Host ''
    Write-Host "  error  $Message" -ForegroundColor Red
    foreach ($h in $Hints) { Write-Host "         $h" -ForegroundColor DarkGray }
    Write-Host ''
    exit 1
}


# ------------------------------------------------------------- pid files ----
# Recorded at launch so -Stop knows exactly what it started. The previous
# version walked the process tree of whatever held the port and killed any
# ancestor whose command line matched 'uvicorn|vite|npm', which could take out
# an unrelated terminal that merely happened to be in the chain.

function Save-Pid {
    param([string]$Name, [int]$ProcessId)
    New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
    Set-Content -Path (Join-Path $RunDir "$Name.pid") -Value $ProcessId -Encoding ascii
}

function Get-SavedPids {
    if (-not (Test-Path $RunDir)) { return @() }
    Get-ChildItem -Path $RunDir -Filter '*.pid' -ErrorAction SilentlyContinue | ForEach-Object {
        $raw = (Get-Content $_.FullName -ErrorAction SilentlyContinue | Select-Object -First 1)
        $parsed = 0
        if ([int]::TryParse($raw, [ref]$parsed) -and $parsed -gt 4) {
            [pscustomobject]@{ Name = $_.BaseName; Id = $parsed; File = $_.FullName }
        }
    }
}

function Remove-PidFile {
    param([string]$Path)
    Remove-Item -Path $Path -Force -ErrorAction SilentlyContinue
}


# ----------------------------------------------------------------- ports ----

function Get-PortPids {
    param([int]$Port)
    $found = @()
    if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
        try {
            $found = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
                       Select-Object -ExpandProperty OwningProcess)
        } catch {
            $found = @()
        }
    }
    if ($found.Count -eq 0) {
        # Fallback for hosts without the NetTCPIP module.
        $found = @(netstat -ano |
                   Select-String -Pattern (":{0}\s" -f $Port) |
                   Select-String -Pattern 'LISTENING' |
                   ForEach-Object { ($_.ToString().Trim() -split '\s+')[-1] })
    }
    return @($found | Where-Object { $_ -and [int]$_ -gt 4 } |
             ForEach-Object { [int]$_ } | Select-Object -Unique)
}

function Describe-Pid {
    param([int]$ProcessId)
    $p = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $p) { return "pid $ProcessId (gone)" }
    $cmd = ''
    try {
        $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop).CommandLine
    } catch { }
    if ($cmd) { return "$($p.ProcessName) (pid $ProcessId): $cmd" }
    return "$($p.ProcessName) (pid $ProcessId)"
}

function Stop-Tree {
    <# Kill a process and its children. /T covers uvicorn's reload supervisor,
       which would otherwise respawn the worker we just killed. #>
    param([int]$ProcessId)
    if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) { return $false }
    taskkill /T /F /PID $ProcessId 2>&1 | Out-Null
    return $true
}

function Stop-OrphansOf {
    <#
        Kill live children of a pid that is itself already gone.

        uvicorn --reload runs a supervisor plus a worker. Kill the supervisor
        without /T and the worker is orphaned, keeps the socket, and keeps
        serving -- while the TCP table still credits the dead parent, so the
        port looks held by a pid that no longer exists and can't be killed.
        Matching on ParentProcessId targets exactly those strays, without the
        old version's broad 'uvicorn|vite|npm' command-line sweep that could
        take out an unrelated process.
    #>
    param([int]$DeadParentId)
    $killed = 0
    $orphans = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $DeadParentId" -ErrorAction SilentlyContinue)
    foreach ($orphan in $orphans) {
        $id = [int]$orphan.ProcessId
        if (Stop-Tree -ProcessId $id) {
            $killed++
            Write-Check 'stop' 'orphaned worker' "stopped pid $id (parent $DeadParentId is gone)" 'Yellow'
        }
    }
    return $killed
}

function Stop-Dashboard {
    <# Stop by pid file first, then sweep the ports for anything left. #>
    param([int[]]$Ports)
    $killed = 0

    foreach ($entry in Get-SavedPids) {
        if (Stop-Tree -ProcessId $entry.Id) {
            $killed++
            Write-Check 'stop' $entry.Name "stopped pid $($entry.Id)" 'Yellow'
        }
        Remove-PidFile -Path $entry.File
    }

    foreach ($port in $Ports) {
        $deadline = (Get-Date).AddSeconds(12)
        while ($true) {
            $owners = Get-PortPids -Port $port
            if ($owners.Count -eq 0) { break }
            foreach ($owner in $owners) {
                if (Stop-Tree -ProcessId $owner) {
                    $killed++
                    Write-Check 'stop' "port $port" "stopped pid $owner" 'Yellow'
                } else {
                    # The listed owner is already dead but the port is still
                    # held: an orphaned child inherited the socket.
                    $killed += Stop-OrphansOf -DeadParentId $owner
                }
            }
            Start-Sleep -Milliseconds 500
            # Repeat: a reload child can stay bound for a moment after its
            # parent dies, so one pass would report a port free while it isn't.
            if ((Get-PortPids -Port $port).Count -eq 0) { break }
            if ((Get-Date) -ge $deadline) { break }
        }
    }
    return $killed
}

function Assert-PortFree {
    param([int]$Port, [string]$Label)
    $owners = Get-PortPids -Port $Port
    if ($owners.Count -eq 0) {
        Write-Check 'preflight' "port $Port" 'free'
        return
    }
    # The Windows service restarts itself, so -Kill cannot free its port: the
    # taskkill succeeds, NSSM starts a new uvicorn ~5s later, and the retry
    # below reports "something is still listening" with no hint as to what.
    # Catch it before offering -Kill at all.
    # NSSM is the service process and runs uvicorn as its child, so the pid
    # holding the port is the child -- match on the parent chain, not on the
    # service pid directly. Only blame the service if it really owns THIS port;
    # `-ApiPort 8001` alongside a running service must still work normally.
    $svc = Get-Service -Name 'SignalDashboard' -ErrorAction SilentlyContinue
    $svcOwnsPort = $false
    if ($svc -and $svc.Status -eq 'Running') {
        $svcPid = (Get-CimInstance Win32_Service -Filter "Name='SignalDashboard'").ProcessId
        foreach ($owner in $owners) {
            $p = Get-CimInstance Win32_Process -Filter "ProcessId=$owner" -ErrorAction SilentlyContinue
            if ($p -and ($owner -eq $svcPid -or $p.ParentProcessId -eq $svcPid)) {
                $svcOwnsPort = $true
                break
            }
        }
    }
    if ($svcOwnsPort) {
        Fail "$Label port $Port is held by the SignalDashboard Windows service." (@(
            'The service restarts on kill, so -Kill will not free it. Either:',
            "  .\start.ps1 $Mode -ApiPort 8001        # run dev alongside the service",
            '  .\windows\service-control.ps1 -Stop    # stop the service first (admin)',
            '',
            'Note the dev instance uses backend\stocks.db, a different database',
            'from the service -- separate accounts, separate TOTP enrolment.',
            'Currently held by:'
        ) + @($owners | ForEach-Object { Describe-Pid $_ }))
    }

    if (-not $Kill) {
        $who = @($owners | ForEach-Object { Describe-Pid $_ })
        Fail "$Label port $Port is already in use." (@(
            'Re-run with -Kill to stop it, or pick another port:',
            "  .\start.ps1 $Mode -ApiPort <n> -WebPort <n>",
            'Currently held by:'
        ) + $who)
    }
    [void](Stop-Dashboard -Ports @($Port))
    if ((Get-PortPids -Port $Port).Count -gt 0) {
        Fail "Could not free port $Port -- something is still listening."
    }
    Write-Check 'preflight' "port $Port" 'freed'
}


# ------------------------------------------------------------- preflight ----

function Test-PythonImports {
    <#
        Quiet, window-less probe that the venv actually has the deps. The
        argument list is one pre-quoted string on purpose: Windows PowerShell
        joins an -ArgumentList array with spaces without quoting, which would
        hand python a bare `-c import`.
    #>
    $probe = Start-Process -FilePath $Python -WindowStyle Hidden -Wait -PassThru `
                           -ArgumentList '-c "import fastapi, uvicorn, apscheduler"'
    return ($probe.ExitCode -eq 0)
}

function Initialize-Backend {
    if (-not (Test-Path $Python)) {
        if (-not $Install) {
            Fail 'Backend venv is missing.' @(
                'Re-run with -Install, or set it up yourself:',
                '  cd backend',
                '  python -m venv .venv',
                '  .venv\Scripts\python.exe -m pip install -r requirements.txt'
            )
        }
        $sysPython = Get-Command python -ErrorAction SilentlyContinue
        if (-not $sysPython) { $sysPython = Get-Command py -ErrorAction SilentlyContinue }
        if (-not $sysPython) { Fail 'No system Python on PATH (need 3.11+).' }

        Push-Location $Backend
        try {
            & $sysPython.Source -m venv .venv
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Python)) { Fail 'Creating backend\.venv failed.' }
            & $Python -m pip install -r requirements.txt
            if ($LASTEXITCODE -ne 0) { Fail 'pip install -r requirements.txt failed.' }
        } finally {
            Pop-Location
        }
        Write-Check 'preflight' 'backend venv' 'created'
    } else {
        Write-Check 'preflight' 'backend venv' 'ok'
    }

    if (Test-PythonImports) {
        Write-Check 'preflight' 'backend deps' 'ok'
        return
    }
    if (-not $Install) {
        Fail 'Backend dependencies are missing or incomplete.' @(
            'Re-run with -Install, or:',
            '  cd backend && .venv\Scripts\python.exe -m pip install -r requirements.txt'
        )
    }
    Push-Location $Backend
    try {
        & $Python -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { Fail 'pip install -r requirements.txt failed.' }
    } finally {
        Pop-Location
    }
    if (-not (Test-PythonImports)) { Fail 'Backend deps still not importable after install.' }
    Write-Check 'preflight' 'backend deps' 'installed'
}

function Initialize-Frontend {
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        Fail 'npm not found on PATH.' @('Install Node.js (which ships npm) and re-run.')
    }
    if (Test-Path (Join-Path $Frontend 'node_modules')) {
        Write-Check 'preflight' 'frontend node_modules' 'ok'
        return
    }
    if (-not $Install) {
        Fail 'Frontend dependencies are missing.' @(
            'Re-run with -Install, or:  cd frontend && npm install')
    }
    Push-Location $Frontend
    try {
        & npm.cmd install
        if ($LASTEXITCODE -ne 0) { Fail 'npm install failed.' }
    } finally {
        Pop-Location
    }
    Write-Check 'preflight' 'frontend node_modules' 'installed'
}


# ----------------------------------------------------------------- launch ----

function Start-ServerWindow {
    <# Detached, minimized, titled console window with its output on disk. #>
    param([string]$Title, [string]$WorkDir, [string]$Command, [string]$LogName)
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $log = Join-Path $LogDir $LogName
    # Built by concatenation with an explicit quote char: the redirect target
    # has to be quoted for cmd (the path can contain spaces), and nesting
    # escaped quotes inside an interpolated string here is a parse error.
    $quote = [char]34
    $argLine = '/c title ' + $Title + ' & ' + $Command +
               ' > ' + $quote + $log + $quote + ' 2>&1'
    $spec = @{
        FilePath         = 'cmd.exe'
        ArgumentList     = $argLine
        WorkingDirectory = $WorkDir
        WindowStyle      = 'Minimized'
        PassThru         = $true
    }
    return Start-Process @spec
}

function Wait-Health {
    param([int]$Port, [int]$TimeoutSeconds = 60)
    $url = "http://127.0.0.1:$Port/api/health"
    $started = Get-Date
    $deadline = $started.AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                return [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
            }
        } catch {
            # Not up yet -- keep polling until the deadline.
        }
        Start-Sleep -Milliseconds 700
    }
    return -1
}

function Show-LogTail {
    param([string]$Name, [int]$Lines = 6)
    $path = Join-Path $LogDir $Name
    if (-not (Test-Path $path)) { return }
    Write-Host ''
    Write-Host "  --- $Name (last $Lines) ---" -ForegroundColor DarkGray
    Get-Content $path -Tail $Lines -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
}


# ------------------------------------------------------------------- main ----

Write-Host ''

if ($Logs) {
    $paths = @('backend.log', 'api.log', 'web.log', 'supervisor.log') |
        ForEach-Object { Join-Path $LogDir $_ } | Where-Object { Test-Path $_ }
    if ($paths.Count -eq 0) { Fail "No logs yet in $LogDir." }
    Write-Note "Following: $($paths -join ', ')  (Ctrl+C to stop)"
    Write-Host ''
    Get-Content -Path $paths -Tail 40 -Wait
    exit 0
}

if ($Status) {
    Write-Host '  Stock Signal Dashboard - status' -ForegroundColor Cyan
    Write-Host ''
    $saved = @(Get-SavedPids)
    if ($saved.Count -eq 0) {
        Write-Check 'status' 'pid files' 'none recorded' 'Yellow'
    } else {
        foreach ($entry in $saved) {
            $alive = [bool](Get-Process -Id $entry.Id -ErrorAction SilentlyContinue)
            Write-Check 'status' $entry.Name `
                ("pid $($entry.Id) " + $(if ($alive) { 'running' } else { 'NOT running' })) `
                $(if ($alive) { 'Green' } else { 'Red' })
        }
    }
    foreach ($port in @($ApiPort, $WebPort)) {
        $owners = Get-PortPids -Port $port
        Write-Check 'status' "port $port" `
            $(if ($owners.Count) { "listening (pid $($owners -join ', '))" } else { 'free' }) `
            $(if ($owners.Count) { 'Green' } else { 'DarkGray' })
    }
    try {
        $h = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/api/health" -UseBasicParsing -TimeoutSec 3
        Write-Check 'status' 'api /api/health' $h.Content
    } catch {
        Write-Check 'status' 'api /api/health' 'no response' 'Red'
    }
    $db = Join-Path $Backend 'stocks.db'
    if (Test-Path $db) {
        $size = [math]::Round((Get-Item $db).Length / 1MB, 1)
        $wal = Join-Path $Backend 'stocks.db-wal'
        $walSize = if (Test-Path $wal) { [math]::Round((Get-Item $wal).Length / 1MB, 1) } else { 0 }
        Write-Check 'status' 'database' ('{0}MB (+{1}MB WAL)' -f $size, $walSize)
    }
    Show-LogTail -Name 'backend.log'
    Write-Host ''
    exit 0
}

if ($Stop) {
    Write-Host '  Stock Signal Dashboard - stop' -ForegroundColor Cyan
    Write-Host ''
    $total = Stop-Dashboard -Ports @($ApiPort, $WebPort)
    $stuck = @($ApiPort, $WebPort) | Where-Object { (Get-PortPids -Port $_).Count -gt 0 }
    foreach ($port in $stuck) {
        Write-Check 'stop' "port $port" 'still bound - kill it manually' 'Red'
    }
    if ($total -eq 0 -and $stuck.Count -eq 0) { Write-Note 'Dashboard was not running.' }
    Write-Host ''
    if ($stuck.Count -gt 0) { exit 1 }
    exit 0
}

Write-Host "  Stock Signal Dashboard - $Mode" -ForegroundColor Cyan
Write-Host ''

Initialize-Backend
Initialize-Frontend

if ($Mode -eq 'dev') {
    Assert-PortFree -Port $ApiPort -Label 'Backend'
    Assert-PortFree -Port $WebPort -Label 'Frontend'
} else {
    Assert-PortFree -Port $ApiPort -Label 'App'
}

# The session cookie rides on credentialed requests, so the backend needs the
# Vite origin explicitly allowlisted whenever it isn't the built-in default.
if ($Mode -eq 'dev' -and $WebPort -ne 5173) {
    $env:STOCKS_CORS_ORIGINS = "http://localhost:$WebPort"
    Write-Check 'preflight' 'CORS origin' "http://localhost:$WebPort"
}

# Point the Vite proxy at whichever API port we're actually using. Without this
# the target is hard-coded to :8000 and -ApiPort silently breaks the UI.
$env:VITE_API_TARGET = "http://localhost:$ApiPort"

# Python block-buffers stdout/stderr when they aren't a terminal, and here they
# are a file. Without this the redirected log stays empty until the buffer
# fills or the process exits -- i.e. it is empty exactly when you go looking
# for it after a hang.
$env:PYTHONUNBUFFERED = '1'

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
Write-Host ''

if ($Mode -eq 'prod') {
    Write-Check 'build' 'frontend (vite build)' 'running...' 'DarkGray'
    Push-Location $Frontend
    try {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { Fail 'npm run build failed -- see the output above.' }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path $DistIndex)) { Fail "Build finished but $DistIndex is missing." }
    Write-Check 'build' 'frontend dist' 'ok'

    $supervise = if ($NoSupervise) { ' --no-supervise' } else { '' }
    $cmd = ".venv\Scripts\python.exe run_server.py --port $ApiPort$supervise"
    $app = Start-ServerWindow -Title 'stocks-app' -WorkDir $Backend -Command $cmd -LogName 'api.log'
    Save-Pid -Name 'app' -ProcessId $app.Id
    Write-Check 'start' 'app (api + spa)' "pid $($app.Id)"
    $openUrl = "http://localhost:$ApiPort"
} else {
    $backendCmd = ".venv\Scripts\python.exe -m uvicorn app.main:app --reload --reload-dir app --port $ApiPort"
    $api = Start-ServerWindow -Title 'stocks-backend' -WorkDir $Backend -Command $backendCmd -LogName 'api.log'
    Save-Pid -Name 'api' -ProcessId $api.Id
    Write-Check 'start' 'backend (uvicorn reload)' "pid $($api.Id)"

    $frontendCmd = "npm.cmd run dev -- --port $WebPort --strictPort"
    $web = Start-ServerWindow -Title 'stocks-frontend' -WorkDir $Frontend -Command $frontendCmd -LogName 'web.log'
    Save-Pid -Name 'web' -ProcessId $web.Id
    Write-Check 'start' 'frontend (vite)' "pid $($web.Id)"

    $openUrl = "http://localhost:$WebPort"
}

$elapsed = Wait-Health -Port $ApiPort
if ($elapsed -lt 0) {
    Write-Check 'wait' 'api /api/health' 'no response' 'Red'
    Show-LogTail -Name 'api.log' -Lines 20
    Write-Host ''
    Write-Note "Full logs: $LogDir   (.\start.ps1 -Logs to follow)" 'Yellow'
    Write-Note '.\start.ps1 -Stop to shut down.' 'Yellow'
    Write-Host ''
    exit 1
}
Write-Check 'wait' 'api /api/health' "ok (${elapsed}s)"

Write-Host ''
if ($Mode -eq 'prod') {
    Write-Host "  App       http://localhost:$ApiPort" -ForegroundColor White -NoNewline
    Write-Host '   (window "stocks-app", api + built UI)' -ForegroundColor DarkGray
} else {
    Write-Host "  Frontend  http://localhost:$WebPort" -ForegroundColor White -NoNewline
    Write-Host '   (window "stocks-frontend", hot reload)' -ForegroundColor DarkGray
    Write-Host "  Backend   http://localhost:$ApiPort" -ForegroundColor White -NoNewline
    Write-Host '   (window "stocks-backend", api only)' -ForegroundColor DarkGray
    if (Test-Path $DistIndex) {
        Write-Note "heads up: :$ApiPort also serves the last frontend build -- use :$WebPort for live code."
    }
}
Write-Host ''
Write-Note "Logs   $LogDir   (.\start.ps1 -Logs)"
Write-Note 'Status .\start.ps1 -Status'
Write-Note 'Stop   .\start.ps1 -Stop   (or stop.bat)'
Write-Host ''

if (-not $NoBrowser) { Start-Process $openUrl | Out-Null }

exit 0
