<#
.SYNOPSIS
    Stop and remove the Signal dashboard Windows service.

.DESCRIPTION
    Removes the service registration only. Your database, logs and secrets
    under C:\ProgramData\SignalDashboard are KEPT unless you pass -PurgeData.

.PARAMETER PurgeData
    Also delete C:\ProgramData\SignalDashboard -- the database (accounts, TOTP
    enrolments, all collected history), the logs and service.env. Prompts
    first unless -Force is given.

.EXAMPLE
    .\windows\uninstall-service.ps1
.EXAMPLE
    .\windows\uninstall-service.ps1 -PurgeData
#>
[CmdletBinding()]
param(
    [switch]$PurgeData,
    [switch]$Force,
    [string]$NssmPath
)

#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

Write-Host ''
Write-Host '  Signal - uninstall Windows service' -ForegroundColor Cyan
Write-Host ''

Assert-Admin

$svc = Get-DashboardService
if (-not $svc) {
    Write-Step 'service' 'not installed, nothing to remove' 'DarkGray'
} else {
    if ($svc.Status -ne 'Stopped') {
        Write-Step 'stop' 'stopping (graceful)...' 'DarkGray'
        Stop-Service -Name $ServiceName -ErrorAction SilentlyContinue
        try { (Get-DashboardService).WaitForStatus('Stopped', '00:00:30') } catch {}
    }
    $nssm = if ($NssmPath) { $NssmPath } elseif (Test-Path $NssmExe) { $NssmExe } else { $null }
    if ($nssm) {
        Invoke-Nssm $nssm remove $ServiceName confirm | Out-Null
    } else {
        # nssm.exe is gone (someone cleaned windows\nssm\); sc.exe removes the
        # registration just as well, the service is an ordinary one to Windows.
        & sc.exe delete $ServiceName | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "sc.exe delete $ServiceName failed (exit $LASTEXITCODE)" }
    }
    Write-Step 'service' 'removed'
}

if ($PurgeData) {
    if (-not (Test-Path $DataRoot)) {
        Write-Step 'data' 'nothing to purge' 'DarkGray'
    } else {
        if (-not $Force) {
            Write-Host ''
            Write-Host '  This permanently deletes:' -ForegroundColor Yellow
            Write-Host "    $DbFile" -ForegroundColor Yellow
            Write-Host '      (all accounts, TOTP enrolments, watchlists, portfolios' -ForegroundColor Yellow
            Write-Host '       and every signal ever collected)' -ForegroundColor Yellow
            Write-Host "    $DataLogs" -ForegroundColor Yellow
            Write-Host "    $EnvFile" -ForegroundColor Yellow
            Write-Host ''
            $answer = Read-Host '  Type DELETE to confirm'
            if ($answer -cne 'DELETE') {
                Write-Host ''
                Write-Step 'data' 'kept (not confirmed)' 'DarkGray'
                Write-Host ''
                return
            }
        }
        Remove-Item $DataRoot -Recurse -Force
        Write-Step 'data' 'purged'
    }
} elseif (Test-Path $DataRoot) {
    Write-Step 'data' "kept at $DataRoot"
    Write-Host ''
    Write-Host '  Re-installing later will pick the same database back up.' -ForegroundColor DarkGray
    Write-Host '  Use -PurgeData to delete it.' -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '  Done.' -ForegroundColor Green
Write-Host ''
