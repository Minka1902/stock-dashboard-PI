<#
.SYNOPSIS
    One-click updater for the Signal dashboard. Started by the app itself.

.DESCRIPTION
    POST /api/update/apply (admin only) spawns this script detached, after the
    backend has already checked the preconditions (on main, no tracked
    changes, fast-forward possible, something to pull). It then:

      1. pull      git pull --ff-only origin main
      2. deps      backend\.venv\Scripts\python.exe -m pip install -r requirements.txt
      3. frontend  npm install ; npm run build        (in frontend\)
      4. restart   service mode: Restart-Service SignalDashboard, wait for
                   /api/health to report the new commit.
                   dev mode:     nudge uvicorn --reload (touch app\version.py);
                   the UI tells the user to reload the page.

    Any failure after the pull rolls back: git reset --hard <previous>, the
    old requirements and frontend are reinstalled/rebuilt, and the server is
    restarted. The UI gets state "rolled_back" and the reason.

    Progress goes to <StatusDir>\update-status.json (polled by the UI through
    GET /api/update/status) and a transcript to <StatusDir>\update.log.

    It relaunches itself once (-Stage2) so the real worker's parent pid is a
    process that has already exited. NSSM stops a service by killing its
    process tree, walked by parent pid; without the hop, the restart step
    would kill this script halfway through restarting the service.

    Every git call passes -c safe.directory=<repo>: as LocalSystem, git
    otherwise refuses to touch a tree owned by the user who cloned it.

.PARAMETER SkipRestart
    Do everything except the restart / reload nudge. For testing the script
    in a throwaway clone.

.EXAMPLE
    # What the app runs (you normally never call this by hand):
    .\windows\update.ps1 -PreviousCommit 2d5350e -Mode service -StatusDir C:\ProgramData\SignalDashboard\logs
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PreviousCommit,
    [ValidateSet('service', 'dev')][string]$Mode = 'dev',
    [Parameter(Mandatory = $true)][string]$StatusDir,
    [int]$Port = 8000,
    [switch]$SkipRestart,
    [switch]$Stage2
)

#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

$StatusDir = $StatusDir.TrimEnd('\')
New-Item -ItemType Directory -Force -Path $StatusDir | Out-Null
$StatusFile = Join-Path $StatusDir 'update-status.json'
$LogFile    = Join-Path $StatusDir 'update.log'
$SafeDir    = $RepoRoot -replace '\\', '/'

function Add-Log {
    param([string]$Message)
    $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    $stage = if ($Stage2) { 'worker' } else { 'launch' }
    try { Add-Content -LiteralPath $LogFile -Value "$stamp [$stage] $Message" -Encoding UTF8 } catch { }
}

# ------------------------------------------------------------- stage 1 ----
if (-not $Stage2) {
    # Relaunch and exit at once: see the process-tree note above. Values are
    # quoted by hand because 5.1's Start-Process joins -ArgumentList with bare
    # spaces; TrimEnd('\') above keeps a trailing backslash from escaping the
    # closing quote.
    $argList = @(
        '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', ('"{0}"' -f $PSCommandPath),
        '-PreviousCommit', $PreviousCommit,
        '-Mode', $Mode,
        '-StatusDir', ('"{0}"' -f $StatusDir),
        '-Port', $Port,
        '-Stage2'
    )
    if ($SkipRestart) { $argList += '-SkipRestart' }
    Add-Log "launching worker (mode=$Mode, previous=$PreviousCommit)"
    Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -WindowStyle Hidden | Out-Null
    exit 0
}

# ------------------------------------------------------------- status ----
function Get-Now { (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') }

$Status = [ordered]@{
    state           = 'running'
    mode            = $Mode
    started_at      = (Get-Now)
    updated_at      = (Get-Now)
    finished_at     = $null
    previous_commit = $PreviousCommit
    target_commit   = $null
    new_commit      = $null
    message         = 'updating'
    steps           = @(
        [ordered]@{ id = 'pull';     label = 'Pull from GitHub';        state = 'pending'; detail = '' },
        [ordered]@{ id = 'deps';     label = 'Install Python packages'; state = 'pending'; detail = '' },
        [ordered]@{ id = 'frontend'; label = 'Rebuild the web app';     state = 'pending'; detail = '' },
        [ordered]@{ id = 'restart';  label = 'Restart';                 state = 'pending'; detail = '' }
    )
}

function Save-Status {
    $Status.updated_at = Get-Now
    $json = $Status | ConvertTo-Json -Depth 6
    $tmp = "$StatusFile.tmp"
    # UTF-8 without a BOM; the backend reads it with utf-8-sig either way.
    [IO.File]::WriteAllText($tmp, $json, (New-Object Text.UTF8Encoding($false)))
    # The backend may have the file open for a read at this instant.
    for ($i = 0; $i -lt 10; $i++) {
        try { Move-Item -LiteralPath $tmp -Destination $StatusFile -Force; return }
        catch { Start-Sleep -Milliseconds 150 }
    }
    Add-Log 'WARNING: could not replace the status file'
}

function Set-Step {
    param([string]$Id, [string]$State, [string]$Detail = '')
    foreach ($s in $Status.steps) {
        if ($s.id -eq $Id) { $s.state = $State; if ($Detail) { $s.detail = $Detail } }
    }
    Add-Log "step $Id -> $State $Detail"
    Save-Status
}

function Add-RollbackStep {
    $Status.steps += [ordered]@{ id = 'rollback'; label = 'Roll back'; state = 'running'; detail = '' }
    Save-Status
}

# ------------------------------------------------------------ tooling ----
$script:LastTail = ''

function Invoke-Tool {
    <#
        Runs a native command, appends its output to update.log, keeps the
        last lines for the UI, and returns the exit code. EAP is relaxed for
        the call: git and npm write progress to stderr, and under 'Stop' 5.1
        turns the first stderr line of a redirected native call into a
        terminating error.
    #>
    param([string]$Exe, [string[]]$ToolArgs, [string]$WorkDir = $RepoRoot)
    Add-Log "> $Exe $($ToolArgs -join ' ')"
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    Push-Location -LiteralPath $WorkDir
    try {
        $out = & $Exe @ToolArgs 2>&1
        $code = $LASTEXITCODE
    } catch {
        $out = @("$($_.Exception.Message)")
        $code = 9009
    } finally {
        Pop-Location
        $ErrorActionPreference = $prev
    }
    # Stringify (stderr arrives as ErrorRecords), strip ANSI colour codes, and
    # drop the empty-stderr placeholders 5.1 renders as RemoteException.
    $lines = @($out | ForEach-Object { ("$_" -replace "\x1b\[[0-9;]*[A-Za-z]", '').TrimEnd() } |
               Where-Object { $_.Trim() -and $_ -ne 'System.Management.Automation.RemoteException' })
    foreach ($l in $lines) { Add-Log "    $l" }
    # For the UI: the first few lines that look like the actual error, not a
    # stack trace; fall back to the last lines of output.
    $errLines = @($lines | Where-Object {
        $_ -match '(?i)(error|fatal|failed|unexpected|cannot|not found|denied|conflict|abort)' -and
        $_ -notmatch '^\s*at\s' -and $_ -notmatch 'Getter' })
    $pick = if ($errLines.Count) { $errLines | Select-Object -First 4 } else { $lines | Select-Object -Last 3 }
    $script:LastTail = (@($pick | ForEach-Object { $_.Trim() }) -join ' | ')
    if ($null -eq $code) { $code = 0 }
    return $code
}

function Invoke-Git {
    param([string[]]$GitArgs)
    return (Invoke-Tool 'git' (@('-c', "safe.directory=$SafeDir") + $GitArgs))
}

function Get-GitOutput {
    param([string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $o = & git -c "safe.directory=$SafeDir" -C $RepoRoot @GitArgs 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return ("$o").Trim()
    } finally { $ErrorActionPreference = $prev }
}

function Install-Deps {
    $code = Invoke-Tool $Python @('-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
                                  '-r', 'requirements.txt') $Backend
    return $code
}

function Build-Frontend {
    # npm install, not npm ci: the lockfile is not always in sync with
    # package.json in this repo, and ci refuses outright when it isn't.
    # --no-save stops it rewriting package-lock.json: a modified tracked file
    # would block the *next* update ("local changes").
    $code = Invoke-Tool 'npm.cmd' @('install', '--no-save', '--no-audit', '--no-fund') $Frontend
    Restore-Lockfile
    if ($code -ne 0) { return $code }
    return (Invoke-Tool 'npm.cmd' @('run', 'build') $Frontend)
}

function Restore-Lockfile {
    # Belt and braces for --no-save: the tree was clean before the pull (the
    # backend checks), so any lockfile change is npm's own and safe to drop.
    $dirty = Get-GitOutput @('status', '--porcelain', '--', 'frontend/package-lock.json')
    if ($dirty) {
        Add-Log 'npm modified frontend/package-lock.json; restoring the committed version'
        [void](Invoke-Git @('checkout', '--', 'frontend/package-lock.json'))
    }
}

function Invoke-Restart {
    <# Returns $null on success, or a reason string. #>
    param([string]$ExpectCommit)
    if ($SkipRestart) { return $null }
    if ($Mode -eq 'dev') {
        # uvicorn --reload watches backend\app. The pull already touched
        # files there if the backend changed, but that reload may have fired
        # before pip finished; touching version.py forces one more, now that
        # the new dependencies are installed.
        try {
            (Get-Item -LiteralPath (Join-Path $Backend 'app\version.py')).LastWriteTime = Get-Date
        } catch { return "could not nudge the dev reloader: $($_.Exception.Message)" }
        return $null
    }
    try {
        $svc = Get-DashboardService
        if (-not $svc) { return "service $ServiceName is not installed" }
        if ($svc.Status -eq 'Running') { Restart-Service -Name $ServiceName -Force }
        else { Start-Service -Name $ServiceName }
    } catch {
        return "could not restart ${ServiceName}: $($_.Exception.Message)"
    }
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        $h = Get-HealthJson -Port $Port
        if ($h) {
            $commit = $null
            if ($h.PSObject.Properties.Name -contains 'commit') { $commit = $h.commit }
            if (-not $ExpectCommit -or ($commit -and $ExpectCommit.StartsWith($commit)) -or
                ($commit -and $commit.StartsWith($ExpectCommit))) { return $null }
        }
        Start-Sleep -Seconds 2
    }
    return "the service did not come back on port $Port with commit $ExpectCommit within 120s"
}

function Finish {
    param([string]$State, [string]$Message)
    $Status.state = $State
    $Status.message = $Message
    $Status.finished_at = Get-Now
    Save-Status
    Add-Log "finished: $State - $Message"
    exit 0
}

function Invoke-Rollback {
    param([string]$FailedStep, [string]$Reason, [string]$PreviousFull)
    Set-Step $FailedStep 'failed' $Reason
    Add-RollbackStep
    $problems = @()
    if ((Invoke-Git @('reset', '--hard', $PreviousFull)) -ne 0) {
        $problems += "git reset failed: $script:LastTail"
    }
    if ((Install-Deps) -ne 0) { $problems += "reinstalling old packages failed: $script:LastTail" }
    if ((Build-Frontend) -ne 0) { $problems += "rebuilding the old frontend failed: $script:LastTail" }
    $restartProblem = Invoke-Restart $PreviousCommit
    if ($restartProblem) { $problems += $restartProblem }

    $rb = $Status.steps | Where-Object { $_.id -eq 'rollback' }
    if ($problems.Count -eq 0) {
        $rb.state = 'done'; $rb.detail = "back on $PreviousCommit"
        Finish 'rolled_back' "Update failed at '$FailedStep': $Reason. Rolled back to $PreviousCommit."
    }
    $rb.state = 'failed'; $rb.detail = ($problems -join '; ')
    Finish 'failed' ("Update failed at '$FailedStep': $Reason. ROLLBACK INCOMPLETE - " +
                     ($problems -join '; ') + ". See $LogFile.")
}

# --------------------------------------------------------------- main ----
# Plain, UTF-8 tool output: npm/vite colour and box-drawing characters are
# noise in a log file and in the UI's one-line failure reason.
$env:NO_COLOR = '1'
$env:FORCE_COLOR = '0'
try { [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false) } catch { }
Add-Log "worker started: repo=$RepoRoot mode=$Mode previous=$PreviousCommit skipRestart=$SkipRestart"
Save-Status

try {
    # Pre-flight: nothing has changed yet, so any failure here is a plain
    # 'failed' with no rollback.
    $missing = @()
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { $missing += 'git' }
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { $missing += 'npm (Node.js)' }
    if (-not (Test-Path -LiteralPath $Python)) { $missing += "the backend venv ($Python)" }
    if ($missing.Count) {
        Set-Step 'pull' 'failed' ("not found on this account's PATH: " + ($missing -join ', '))
        Finish 'failed' ("Nothing was changed. Missing: " + ($missing -join ', ') +
                         '. The service runs as LocalSystem, so these must be on the machine PATH.')
    }
    $headFull = Get-GitOutput @('rev-parse', 'HEAD')
    $prevFull = Get-GitOutput @('rev-parse', '--verify', "$PreviousCommit^{commit}")
    if (-not $prevFull -or $headFull -ne $prevFull) {
        Set-Step 'pull' 'failed' "HEAD is $headFull, expected $PreviousCommit"
        Finish 'failed' 'Nothing was changed: the checkout moved since the update was requested.'
    }

    # 1. pull
    Set-Step 'pull' 'running'
    if ((Invoke-Git @('pull', '--ff-only', 'origin', 'main')) -ne 0) {
        # --ff-only either moves HEAD all the way or not at all; confirm.
        $now = Get-GitOutput @('rev-parse', 'HEAD')
        if ($now -eq $prevFull) {
            Set-Step 'pull' 'failed' $script:LastTail
            Finish 'failed' "Nothing was changed: git pull failed ($script:LastTail)."
        }
        Invoke-Rollback 'pull' $script:LastTail $prevFull
    }
    $newShort = Get-GitOutput @('rev-parse', '--short', 'HEAD')
    $Status.new_commit = $newShort
    $Status.target_commit = $newShort
    Set-Step 'pull' 'done' "$PreviousCommit -> $newShort"

    # 2. deps
    Set-Step 'deps' 'running'
    $stoppedForDeps = $false
    if ((Install-Deps) -ne 0) {
        # A running server holds its compiled extensions (.pyd) open, and
        # Windows won't let pip replace a loaded DLL. As the service we can
        # stop it and retry; in dev there is nothing safe to stop.
        if ($Mode -eq 'service' -and -not $SkipRestart) {
            Add-Log 'pip failed while the service was running; stopping it and retrying'
            Set-Step 'deps' 'running' 'retrying with the service stopped'
            try { Stop-Service -Name $ServiceName -Force; $stoppedForDeps = $true } catch { }
            if ((Install-Deps) -ne 0) { Invoke-Rollback 'deps' $script:LastTail $prevFull }
        } else {
            Invoke-Rollback 'deps' $script:LastTail $prevFull
        }
    }
    Set-Step 'deps' 'done'

    # 3. frontend
    Set-Step 'frontend' 'running'
    if ((Build-Frontend) -ne 0) { Invoke-Rollback 'frontend' $script:LastTail $prevFull }
    Set-Step 'frontend' 'done'

    # 4. restart
    if ($SkipRestart) {
        Set-Step 'restart' 'skipped' 'skipped (-SkipRestart)'
        Finish 'done' "Updated $PreviousCommit -> $newShort (restart skipped)."
    }
    Set-Step 'restart' 'running' $(if ($Mode -eq 'service') { "restarting $ServiceName" } else { 'nudging the dev reloader' })
    $problem = Invoke-Restart $newShort
    if ($problem) { Invoke-Rollback 'restart' $problem $prevFull }
    if ($Mode -eq 'service') {
        Set-Step 'restart' 'done' "$ServiceName is back on $newShort"
        Finish 'done' "Updated $PreviousCommit -> $newShort."
    }
    Set-Step 'restart' 'done' 'reload the page to load the new web app'
    Finish 'done' ("Updated $PreviousCommit -> $newShort. Reload the page. If the backend was " +
                   'not started with --reload (start.ps1 dev does that), restart it.')
} catch {
    $reason = "unexpected error: $($_.Exception.Message)"
    Add-Log $reason
    $running = @($Status.steps | Where-Object { $_.state -eq 'running' } | Select-Object -First 1)
    $stepId = if ($running.Count) { $running[0].id } else { 'pull' }
    $prevFull2 = Get-GitOutput @('rev-parse', '--verify', "$PreviousCommit^{commit}")
    $head2 = Get-GitOutput @('rev-parse', 'HEAD')
    if ($prevFull2 -and $head2 -and $head2 -ne $prevFull2) {
        Invoke-Rollback $stepId $reason $prevFull2
    }
    Set-Step $stepId 'failed' $reason
    Finish 'failed' "Nothing was changed: $reason"
}
