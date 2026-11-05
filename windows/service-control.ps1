<#
.SYNOPSIS
    Day-to-day control of the Signal dashboard Windows service.

.DESCRIPTION
    -Status needs no elevation. -Start / -Stop / -Restart do, because
    controlling a LocalSystem service requires admin rights by default.
    (Loosening that with `sc sdset` would be a privilege-escalation hole, so
    this script prompts for elevation instead of working around it.)

.EXAMPLE
    .\windows\service-control.ps1 -Status
.EXAMPLE
    .\windows\service-control.ps1 -Restart
#>
[CmdletBinding(DefaultParameterSetName = 'Status')]
param(
    [Parameter(ParameterSetName = 'Start')][switch]$Start,
    [Parameter(ParameterSetName = 'Stop')][switch]$Stop,
    [Parameter(ParameterSetName = 'Restart')][switch]$Restart,
    [Parameter(ParameterSetName = 'Status')][switch]$Status,
    [Parameter(ParameterSetName = 'Logs')][switch]$Logs,
    [int]$Port = 8000,
    [int]$Lines = 40
)

#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

$svc = Get-DashboardService
if (-not $svc) {
    Fail "The $ServiceName service is not installed." @(
        'Install it from an elevated PowerShell:',
        '  .\windows\install-service.ps1'
    )
}

# ------------------------------------------------------------------- logs ----
if ($Logs) {
    $backend = Join-Path $DataLogs 'backend.log'
    if (-not (Test-Path $backend)) { Fail "No log yet at $backend" }
    Write-Host ''
    Write-Host "  Following $backend  (Ctrl+C to stop)" -ForegroundColor DarkGray
    Write-Host ''
    Get-Content $backend -Tail $Lines -Wait
    return
}

# ----------------------------------------------------------------- status ----
if ($Status -or $PSCmdlet.ParameterSetName -eq 'Status') {
    Write-Host ''
    Write-Host '  Signal - service status' -ForegroundColor Cyan
    Write-Host ''
    $stateColor = if ($svc.Status -eq 'Running') { 'Green' } else { 'Yellow' }
    Write-Step 'service' "$($svc.Status)" $stateColor
    Write-Step 'startup' (Get-CimInstance Win32_Service -Filter "Name='$ServiceName'").StartMode

    $health = Get-HealthJson -Port $Port
    if ($health) {
        Write-Step 'health'  $health.status $(if ($health.status -eq 'ok') { 'Green' } else { 'Yellow' })
        Write-Step 'version' $health.version
        Write-Step 'uptime'  ('{0:n0}s' -f $health.uptime_seconds)
        Write-Step 'db'      $health.checks.db      $(if ($health.checks.db) { 'Green' } else { 'Red' })
        Write-Step 'scheduler' $health.checks.scheduler $(if ($health.checks.scheduler) { 'Green' } else { 'Red' })
    } else {
        Write-Step 'health' "no response on 127.0.0.1:$Port" 'Red'
    }

    Write-Step 'database' $(if (Test-Path $DbFile) { $DbFile } else { "$DbFile (not created yet)" })
    Write-Step 'logs'     $DataLogs

    # The service runs code straight from this working tree, so a moved or
    # renamed repo folder breaks it in a way nothing else reports.
    if (-not (Test-Path $Backend)) {
        Write-Host ''
        Write-Host "  WARNING: the service points at $Backend, which no longer exists." -ForegroundColor Red
        Write-Host '  Re-run windows\install-service.ps1 from the new location.' -ForegroundColor Red
    }
    Write-Host ''
    return
}

# ------------------------------------------------- start / stop / restart ----
Assert-Admin

if ($Stop -or $Restart) {
    if ($svc.Status -ne 'Stopped') {
        Write-Step 'stop' 'stopping (graceful, up to 20s)...' 'DarkGray'
        # Stop-Service, not taskkill: NSSM's console-event path is what lets
        # app/main.py's lifespan run its WAL checkpoint before exit.
        Stop-Service -Name $ServiceName -ErrorAction Stop
        (Get-DashboardService).WaitForStatus('Stopped', '00:00:30')
        Write-Step 'stop' 'stopped'
    } else {
        Write-Step 'stop' 'already stopped' 'DarkGray'
    }
}

if ($Start -or $Restart) {
    Start-Service -Name $ServiceName -ErrorAction Stop
    $elapsed = Wait-Health -Port $Port -TimeoutSeconds 60
    if ($elapsed -lt 0) {
        $stderr = Join-Path $DataLogs 'service-stderr.log'
        if (Test-Path $stderr) {
            Write-Host ''
            Write-Host "  --- last 30 lines of service-stderr.log ---" -ForegroundColor DarkGray
            Get-Content $stderr -Tail 30 | ForEach-Object { Write-Host "  $_" }
        }
        Fail 'Started, but /api/health never answered.' @(
            "Logs: $DataLogs"
        )
    }
    Write-Step 'start' "healthy in ${elapsed}s"
}

Write-Host ''
