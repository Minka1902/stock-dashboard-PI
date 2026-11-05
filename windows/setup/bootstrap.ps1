<#
.SYNOPSIS
    Everything SignalSetup.exe does after the wizard: prerequisites, clone,
    dependencies, the Windows service and the desktop app.

.DESCRIPTION
    Runs elevated (the installer already is). Re-running it is the repair /
    upgrade path: an existing checkout is fast-forwarded, never reset, and
    install-service.ps1 re-registers the service in place.

      1. git, Node and Python, machine-wide, installed through winget if missing
      2. clone (or fast-forward) main into <InstallDir>\repo
      3. backend venv + pip, frontend npm install
      4. windows\install-service.ps1   (build, Playwright, service, health wait)
      5. windows\install-desktop.ps1   (Electron + Start Menu shortcut)

    Machine-wide matters for all three prerequisites. The service and the
    in-app updater run as LocalSystem, which sees neither a user PATH (git,
    npm) nor a per-user Python under %LOCALAPPDATA% -- and the venv's
    python.exe redirects to its base interpreter at every start.

.PARAMETER InstallDir
    Where SignalSetup put the app; the checkout goes in its repo\ subfolder.

.PARAMETER NssmExe
    The checksum-verified nssm.exe bundled into SignalSetup.exe.

.PARAMETER PauseOnError
    Keep the console open on failure so the error can be read.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [string]$NssmExe,
    [switch]$PauseOnError
)

#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoUrl   = 'https://github.com/Minka1902/stock-dashboard'
$Branch    = 'main'
$MinPython = [version]'3.11'
$Repo      = Join-Path $InstallDir 'repo'
$SafeDir   = 'safe.directory=' + ($Repo -replace '\\', '/')
$LogDir    = Join-Path $(if ($env:ProgramData) { $env:ProgramData } else { 'C:\ProgramData' }) 'SignalDashboard\logs'
$LogFile   = Join-Path $LogDir 'setup.log'
$WingetArgs = @('--exact', '--silent', '--accept-package-agreements', '--accept-source-agreements')


# ---------------------------------------------------------------- output ----

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
    Write-Host "  Full log: $LogFile" -ForegroundColor DarkGray
    try { Stop-Transcript | Out-Null } catch { }
    if ($PauseOnError) { Read-Host '  Press Enter to close' | Out-Null }
    exit 1
}


# --------------------------------------------------------- prerequisites ----

function Update-SessionPath {
    # winget installers write the registry PATH; this process started before.
    $env:Path = @(
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User')
    ) -join ';'
}

function Find-MachineCommand {
    <# Resolve against the MACHINE PATH only -- the one LocalSystem sees. #>
    param([string]$Name)
    foreach ($dir in ([Environment]::GetEnvironmentVariable('Path', 'Machine') -split ';')) {
        if (-not $dir) { continue }
        try {
            $candidate = [IO.Path]::Combine([Environment]::ExpandEnvironmentVariables($dir), $Name)
        } catch { continue }
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    return $null
}

function Test-NodeVersion {
    <# Vite 8's engines range: ^20.19.0 || >=22.12.0 #>
    param([string]$Exe)
    $raw = (& $Exe --version) -replace '^v', ''
    $v = $null
    if (-not [version]::TryParse($raw, [ref]$v)) { return $false }
    return ($v.Major -eq 20 -and $v.Minor -ge 19) -or
           ($v.Major -eq 22 -and $v.Minor -ge 12) -or
           ($v.Major -gt 22)
}

function Find-MachinePython {
    <#
        PEP 514: an all-users install registers under HKLM, a per-user one under
        HKCU -- so HKLM is exactly "an interpreter LocalSystem can run".
        Returns the newest python.exe >= $MinPython, or $null.
    #>
    $found = foreach ($root in 'HKLM:\SOFTWARE\Python\PythonCore', 'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore') {
        foreach ($key in (Get-ChildItem -Path $root -ErrorAction SilentlyContinue)) {
            $v = $null
            if (-not [version]::TryParse(($key.PSChildName -replace '-.*$', ''), [ref]$v)) { continue }
            if ($v -lt $MinPython) { continue }
            $install = Get-ItemProperty -Path (Join-Path $key.PSPath 'InstallPath') -ErrorAction SilentlyContinue
            if (-not $install) { continue }
            $exe = if ($install.PSObject.Properties['ExecutablePath']) { $install.ExecutablePath }
                   elseif ($install.PSObject.Properties['(default)']) { Join-Path $install.'(default)' 'python.exe' }
            if ($exe -and (Test-Path -LiteralPath $exe)) { [pscustomobject]@{ Version = $v; Exe = $exe } }
        }
    }
    $best = $found | Sort-Object Version -Descending | Select-Object -First 1
    if ($best) { return $best.Exe }
    return $null
}

function Install-WithWinget {
    param([string]$Id, [string[]]$Extra = @())
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        Fail 'winget is not available, so the prerequisites cannot be installed automatically.' @(
            'Install them machine-wide ("for all users"), then run SignalSetup.exe again:',
            '  Git      https://git-scm.com/download/win',
            '  Node.js  https://nodejs.org   (20.19+ or 22.12+)',
            '  Python   https://www.python.org/downloads/windows/   (3.11+, tick "Install for all users")'
        )
    }
    Write-Step 'winget' "installing $Id ..." 'DarkGray'
    & $winget.Source install --id $Id @WingetArgs @Extra
    Write-Step 'winget' "$Id finished (exit $LASTEXITCODE)" 'DarkGray'
    Update-SessionPath
}

function Resolve-Git {
    $git = Find-MachineCommand 'git.exe'
    if (-not $git) {
        Install-WithWinget 'Git.Git' @('--scope', 'machine')
        $git = Find-MachineCommand 'git.exe'
    }
    if (-not $git) { Fail 'git is still not on the machine PATH after installing it.' }
    return $git
}

function Resolve-Node {
    $node = Find-MachineCommand 'node.exe'
    if (-not $node -or -not (Test-NodeVersion $node)) {
        Install-WithWinget 'OpenJS.NodeJS.LTS' @('--scope', 'machine')
        $node = Find-MachineCommand 'node.exe'
    }
    if (-not $node -or -not (Test-NodeVersion $node)) {
        Fail 'Node.js 20.19+ or 22.12+ is not on the machine PATH.' @(
            'Install the current LTS for all users from https://nodejs.org, then run SignalSetup.exe again.')
    }
    $npm = Find-MachineCommand 'npm.cmd'
    if (-not $npm) { Fail "npm.cmd is missing next to $node." }
    return $npm
}

function Resolve-Python {
    $python = Find-MachinePython
    if (-not $python) {
        # --override replaces winget's own installer switches, so the all-users
        # install has to be spelled out; --scope alone is not enough for this
        # package to land in Program Files.
        Install-WithWinget 'Python.Python.3.12' @(
            '--scope', 'machine', '--override', '/quiet InstallAllUsers=1 PrependPath=1')
        $python = Find-MachinePython
    }
    if (-not $python) {
        Fail "No all-users Python $MinPython+ found." @(
            'A per-user Python cannot be used: the service runs as LocalSystem.',
            'Install Python for all users from https://www.python.org, then run SignalSetup.exe again.')
    }
    return $python
}


# -------------------------------------------------------------------- app ----

function Sync-Repo {
    param([string]$Git)
    if (Test-Path (Join-Path $Repo '.git')) {
        & $Git -c $SafeDir -C $Repo pull --ff-only origin $Branch
        if ($LASTEXITCODE -ne 0) {
            Write-Step 'repo' 'could not fast-forward -- keeping the current code' 'Yellow'
            return
        }
        Write-Step 'repo' "up to date with origin/$Branch"
        return
    }
    # A leftover from an interrupted clone; the folder is this installer's own.
    if (Test-Path $Repo) { Remove-Item -LiteralPath $Repo -Recurse -Force }
    & $Git clone --branch $Branch $RepoUrl $Repo
    if ($LASTEXITCODE -ne 0) { Fail "git clone $RepoUrl failed -- see the output above." }
    Write-Step 'repo' "cloned into $Repo"
}

function Install-Dependencies {
    param([string]$Python, [string]$Npm)
    $venv = Join-Path $Repo 'backend\.venv'
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        & $Python -m venv $venv
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) { Fail "Creating $venv failed." }
        Write-Step 'venv' 'created'
    }
    & $venvPython -m pip install --disable-pip-version-check -r (Join-Path $Repo 'backend\requirements.txt')
    if ($LASTEXITCODE -ne 0) { Fail 'pip install -r requirements.txt failed -- see the output above.' }
    Write-Step 'backend deps' 'ok'

    # --no-save, as in update.ps1: a rewritten package-lock.json is a tracked
    # change, and a tracked change blocks every later in-app update.
    Push-Location (Join-Path $Repo 'frontend')
    try {
        & $Npm install --no-save
        if ($LASTEXITCODE -ne 0) { Fail 'npm install (frontend) failed -- see the output above.' }
    } finally { Pop-Location }
    Write-Step 'frontend deps' 'ok'
}

function Install-Nssm {
    <#
        Into the gitignored path Resolve-Nssm checks first, NOT a -NssmPath to
        the installer's temp copy: the registered service binary is nssm.exe
        itself and must outlive setup. An existing copy is kept -- on a repair
        it is the running service host and locked.
    #>
    $target = Join-Path $Repo 'windows\nssm\nssm.exe'
    if (Test-Path $target) { return }
    if (-not $NssmExe) { return }  # install-service.ps1 explains the options
    New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null
    Copy-Item -LiteralPath $NssmExe -Destination $target
    Write-Step 'nssm' 'bundled copy installed'
}

function Invoke-RepoScript {
    <# A child process, so the exit code is the script's own and not a stale $LASTEXITCODE. #>
    param([string]$RelativePath)
    $script = Join-Path $Repo $RelativePath
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script
    if ($LASTEXITCODE -ne 0) { Fail "$RelativePath failed (exit $LASTEXITCODE) -- see the output above." }
}


# ------------------------------------------------------------------- main ----

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'ERROR: run SignalSetup.exe (or this script) as Administrator.' -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Start-Transcript -Path $LogFile -Append | Out-Null
# The installer's environment can predate a git/Node install made since login.
Update-SessionPath

Write-Host ''
Write-Host '  Signal - setup' -ForegroundColor Cyan
Write-Host ''

$git    = Resolve-Git
Write-Step 'git' $git
$npm    = Resolve-Node
Write-Step 'npm' $npm
$python = Resolve-Python
Write-Step 'python' $python

Sync-Repo -Git $git
Install-Dependencies -Python $python -Npm $npm
Install-Nssm
Invoke-RepoScript 'windows\install-service.ps1'
Invoke-RepoScript 'windows\install-desktop.ps1'

Write-Host ''
Write-Host '  Signal is installed.' -ForegroundColor Green
Write-Host '  Start it from the Start Menu ("Signal"). Each launch checks for updates.' -ForegroundColor DarkGray
Write-Host ''
Stop-Transcript | Out-Null
exit 0
