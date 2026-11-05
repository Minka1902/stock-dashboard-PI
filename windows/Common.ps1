# Shared helpers for the Windows service scripts. Dot-source, don't run.
#
# Everything the three service scripts agree on lives here: where the repo is,
# where machine-wide data goes, and how to talk to NSSM.

Set-StrictMode -Version Latest

$script:ServiceName    = 'SignalDashboard'
$script:ServiceDisplay = 'Signal Stock Dashboard'
$script:ServiceDesc    = 'Aggregates public stock signals and serves the dashboard on http://127.0.0.1:{0}. Runs the ingest scheduler continuously, including before any user logs in.'

# windows\Common.ps1 -> repo root is one level up.
$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:Backend  = Join-Path $RepoRoot 'backend'
$script:Frontend = Join-Path $RepoRoot 'frontend'
$script:Python   = Join-Path $Backend '.venv\Scripts\python.exe'
$script:DistIndex = Join-Path $Frontend 'dist\index.html'

# Machine-wide, because the service runs as LocalSystem and must not depend on
# any user profile existing.
$script:ProgramData = if ($env:ProgramData) { $env:ProgramData } else { 'C:\ProgramData' }
$script:DataRoot = Join-Path $ProgramData 'SignalDashboard'
$script:DataDb   = Join-Path $DataRoot 'db'
$script:DataLogs = Join-Path $DataRoot 'logs'
$script:DbFile   = Join-Path $DataDb 'stocks.db'
# Playwright's Chromium for the margin-debt headless fetch. Its default cache is
# %LOCALAPPDATA%\ms-playwright of whoever ran `playwright install` -- a profile
# LocalSystem never looks in -- so the service gets a machine-wide copy here and
# PLAYWRIGHT_BROWSERS_PATH pointing at it.
$script:DataBrowsers = Join-Path $DataRoot 'ms-playwright'
$script:EnvFile  = Join-Path $PSScriptRoot 'service.env'
$script:NssmDir  = Join-Path $PSScriptRoot 'nssm'
$script:NssmExe  = Join-Path $NssmDir 'nssm.exe'

# nssm 2.24, the last stable release (2017).
#
# NssmSha256 pins the zip the repo owner downloaded from NssmUrl themselves on
# 2026-09-29. This is a binary we hand SYSTEM-level process control to, so the
# installer and the SignalSetup.exe build (.github/workflows/installer.yml)
# both refuse any download that doesn't match. A mismatch means the file
# changed: verify the new one by hand before re-pinning, never override it.
# -NssmSha256 still overrides it per run.
$script:NssmUrl    = 'https://nssm.cc/release/nssm-2.24.zip'
$script:NssmSha256 = '727D1E42275C605E0F04ABA98095C38A8E1E46DEF453CDFFCE42869428AA6743'
$script:AllowUnpinnedNssm = $false

function Write-Step {
    param([string]$Label, [string]$Value, [string]$Color = 'Green')
    Write-Host ('  {0,-22}' -f $Label) -NoNewline -ForegroundColor DarkGray
    Write-Host $Value -ForegroundColor $Color
}

function Fail {
    param([string]$Message, [string[]]$Hints = @())
    Write-Host ''
    Write-Host "ERROR: $Message" -ForegroundColor Red
    foreach ($h in $Hints) { Write-Host "  $h" -ForegroundColor Yellow }
    Write-Host ''
    exit 1
}

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        # $PSCommandPath is the *calling* script under StrictMode-safe access;
        # fall back to a generic hint if it is somehow empty.
        $leaf = if ($PSCommandPath) { Split-Path -Leaf $PSCommandPath } else { 'install-service.ps1' }
        Fail 'This script must run as Administrator.' @(
            'Controlling a Windows service needs elevation. Right-click',
            'PowerShell -> Run as administrator, then:',
            "  cd `"$RepoRoot`"",
            "  .\windows\$leaf"
        )
    }
}

function Get-DashboardService {
    Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
}

function Resolve-Nssm {
    <#
        Returns a path to nssm.exe, downloading it if needed. The repo carries
        no binaries on purpose, so this is the one place a third-party
        executable enters the tree -- and it only does so after its SHA-256
        matches the pin above.
    #>
    param([string]$NssmPath)

    if ($NssmPath) {
        if (-not (Test-Path $NssmPath)) { Fail "-NssmPath points at nothing: $NssmPath" }
        return (Resolve-Path $NssmPath).Path
    }
    if (Test-Path $NssmExe) { return $NssmExe }

    Write-Step 'nssm' 'not found, downloading...' 'DarkGray'
    $tmpZip = Join-Path $env:TEMP "nssm-2.24-$PID.zip"
    $tmpDir = Join-Path $env:TEMP "nssm-2.24-$PID"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $NssmUrl -OutFile $tmpZip -UseBasicParsing
    } catch {
        Fail "Could not download NSSM from $NssmUrl" @(
            'If this machine is offline or behind a proxy, fetch nssm yourself and',
            'point the installer at it:',
            '  1. Download https://nssm.cc/release/nssm-2.24.zip on another machine',
            "  2. Extract win64\nssm.exe to `"$NssmExe`"",
            '  3. Re-run this script (it will use the local copy), or pass',
            '     -NssmPath C:\path\to\nssm.exe',
            "Underlying error: $($_.Exception.Message)"
        )
    }

    $actual = (Get-FileHash -Path $tmpZip -Algorithm SHA256).Hash
    if ($NssmSha256) {
        if ($actual -ne $NssmSha256) {
            Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue
            Fail 'NSSM download failed its checksum -- refusing to install it.' @(
                "expected $NssmSha256",
                "got      $actual",
                'This binary gets SYSTEM-level process control, so a mismatch is',
                'never worth ignoring. Verify by hand, or pass -NssmPath.'
            )
        }
        Write-Step 'nssm' 'downloaded, checksum matches pin'
    } elseif ($script:AllowUnpinnedNssm) {
        Write-Host ''
        Write-Host '  WARNING: NSSM was downloaded WITHOUT a pinned checksum.' -ForegroundColor Yellow
        Write-Host "  SHA-256: $actual" -ForegroundColor Yellow
        Write-Host '  Verify this against https://nssm.cc, then set NssmSha256 in' -ForegroundColor Yellow
        Write-Host '  windows\Common.ps1 so later installs are checked automatically.' -ForegroundColor Yellow
        Write-Host ''
    } else {
        Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue
        Fail 'No checksum is pinned for the NSSM download.' @(
            'This binary would run with SYSTEM privileges, so it is not installed',
            'from an unverified download by default. Pick one:',
            '',
            '  * Verify it yourself (recommended):',
            '      download https://nssm.cc/release/nssm-2.24.zip',
            '      Get-FileHash .\nssm-2.24.zip -Algorithm SHA256',
            '      then re-run with -NssmSha256 <hash>, or set NssmSha256 in',
            '      windows\Common.ps1 to pin it for every future install.',
            '',
            '  * Use a copy you already trust:',
            '      .\windows\install-service.ps1 -NssmPath C:\tools\nssm.exe',
            '',
            '  * Accept the risk for this run (prints the hash so you can pin it):',
            '      .\windows\install-service.ps1 -TrustNssmDownload'
        )
    }

    Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force
    $arch = if ([Environment]::Is64BitOperatingSystem) { 'win64' } else { 'win32' }
    $src = Join-Path $tmpDir "nssm-2.24\$arch\nssm.exe"
    if (-not (Test-Path $src)) { Fail "The NSSM archive did not contain $arch\nssm.exe" }

    New-Item -ItemType Directory -Force -Path $NssmDir | Out-Null
    Copy-Item $src $NssmExe -Force
    Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue
    Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    return $NssmExe
}

function Invoke-Nssm {
    <#
        nssm reports failure through its exit code and writes the reason to
        stderr, which PowerShell would otherwise scatter into the transcript.
    #>
    # NOT named $Args -- that collides with PowerShell's automatic variable.
    param([string]$Exe, [Parameter(ValueFromRemainingArguments = $true)][string[]]$NssmArgs)
    $output = & $Exe @NssmArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        Fail "nssm $($NssmArgs -join ' ') failed (exit $LASTEXITCODE)" @($output)
    }
    return $output
}

function Read-ServiceEnv {
    <#
        Parses windows\service.env into an ordered KEY=VALUE list.

        The backend has no .env loader at all -- app/config.py reads os.environ
        once at import -- and a LocalSystem service inherits nothing from the
        installing user's shell. So this file is the only way secrets reach the
        running service, and it gets baked into AppEnvironmentExtra.
    #>
    $pairs = [ordered]@{}
    if (-not (Test-Path $EnvFile)) { return $pairs }
    foreach ($line in Get-Content $EnvFile) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $eq = $trimmed.IndexOf('=')
        if ($eq -lt 1) { continue }
        $key = $trimmed.Substring(0, $eq).Trim()
        $value = $trimmed.Substring($eq + 1).Trim()
        # Tolerate quoted values; nssm needs them raw.
        if ($value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
             ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $pairs[$key] = $value
    }
    return $pairs
}

function Get-HealthJson {
    param([int]$Port = 8000, [int]$TimeoutSec = 3)
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" `
                               -UseBasicParsing -TimeoutSec $TimeoutSec
        return ($r.Content | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Wait-Health {
    # Mirrors start.ps1's Wait-Health. /api/health is public and returns 200
    # even when degraded, so any 200 means "the process is up"; the status
    # field is what distinguishes ok from degraded.
    param([int]$Port = 8000, [int]$TimeoutSeconds = 60)
    $started = Get-Date
    $deadline = $started.AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Get-HealthJson -Port $Port) {
            return [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
        }
        Start-Sleep -Milliseconds 700
    }
    return -1
}
