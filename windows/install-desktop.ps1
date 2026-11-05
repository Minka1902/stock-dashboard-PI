<#
.SYNOPSIS
    Set up the Electron desktop app and create a Start Menu shortcut.

.DESCRIPTION
    Runs `npm install` in desktop\ and creates a Start Menu shortcut with the
    AppUserModelID set to match app.setAppUserModelId() in desktop/src/main.js.

    That AUMID match is what makes Windows toast notifications work for an
    unpackaged Electron app: Windows resolves toast identity through the
    shortcut, and without one the notifications may silently never appear.

    No elevation needed -- this is all per-user.

.EXAMPLE
    .\windows\install-desktop.ps1
#>
[CmdletBinding()]
param([switch]$SkipNpmInstall)

#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')

$Desktop = Join-Path $RepoRoot 'desktop'
$AppId   = 'com.signal.dashboard'   # must match desktop/src/main.js

Write-Host ''
Write-Host '  Signal - set up desktop app' -ForegroundColor Cyan
Write-Host ''

if (-not (Test-Path $Desktop)) { Fail "No desktop\ directory at $Desktop" }

if (-not $SkipNpmInstall) {
    Write-Step 'npm' 'installing...' 'DarkGray'
    Push-Location $Desktop
    try {
        & npm.cmd install
        if ($LASTEXITCODE -ne 0) { Fail 'npm install failed -- see the output above.' }
    } finally { Pop-Location }
}
Write-Step 'npm' 'dependencies ok'

# ------------------------------------------------------------------ shortcut --
$electron = Join-Path $Desktop 'node_modules\electron\dist\electron.exe'
if (-not (Test-Path $electron)) {
    Fail "Electron binary not found at $electron" @(
        'Run npm install in desktop\ first, or re-run this script without',
        '-SkipNpmInstall.'
    )
}

$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$lnk = Join-Path $startMenu 'Signal.lnk'

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath       = $electron
$sc.Arguments        = "`"$Desktop`""
$sc.WorkingDirectory = $Desktop
$sc.IconLocation     = Join-Path $Desktop 'assets\icon-128.png'
$sc.Description      = 'Signal stock dashboard'
$sc.Save()

# WScript.Shell cannot set System.AppUserModel.ID, so stamp it through the
# shortcut's property store. Without this the toasts fall back to Electron's
# own identity and may not surface at all.
$stamped = $false
try {
    $code = @'
using System;
using System.Runtime.InteropServices;
public static class Aumid {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHGetPropertyStoreFromParsingName(
        [MarshalAs(UnmanagedType.LPWStr)] string path, IntPtr bc, int flags,
        ref Guid riid, [MarshalAs(UnmanagedType.Interface)] out IPropertyStore ps);

    [StructLayout(LayoutKind.Sequential)]
    public struct PropertyKey { public Guid fmtid; public int pid; }

    [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPropertyStore {
        void GetCount(out uint c);
        void GetAt(uint i, out PropertyKey k);
        void GetValue(ref PropertyKey k, out PropVariant v);
        void SetValue(ref PropertyKey k, ref PropVariant v);
        void Commit();
    }

    [StructLayout(LayoutKind.Explicit)]
    public struct PropVariant {
        [FieldOffset(0)] public ushort vt;
        [FieldOffset(8)] public IntPtr p;
    }

    public static void Set(string lnkPath, string appId) {
        var iid = typeof(IPropertyStore).GUID;
        IPropertyStore store;
        SHGetPropertyStoreFromParsingName(lnkPath, IntPtr.Zero, 2, ref iid, out store);
        var key = new PropertyKey {
            fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), pid = 5 };
        var pv = new PropVariant { vt = 31 /* VT_LPWSTR */,
                                   p = Marshal.StringToCoTaskMemUni(appId) };
        store.SetValue(ref key, ref pv);
        store.Commit();
        Marshal.FreeCoTaskMem(pv.p);
        Marshal.ReleaseComObject(store);
    }
}
'@
    Add-Type -TypeDefinition $code -Language CSharp -ErrorAction Stop | Out-Null
    [Aumid]::Set($lnk, $AppId)
    $stamped = $true
} catch {
    Write-Host "  (could not stamp AppUserModelID: $($_.Exception.Message))" -ForegroundColor DarkGray
}

Write-Step 'shortcut' $lnk
Write-Step 'toast id' $(if ($stamped) { "$AppId (stamped)" } else { 'not stamped -- see below' }) `
                      $(if ($stamped) { 'Green' } else { 'Yellow' })

Write-Host ''
Write-Host '  Done.' -ForegroundColor Green
Write-Host ''
Write-Host '  Launch from the Start Menu ("Signal"), or:' -ForegroundColor DarkGray
Write-Host '    cd desktop; npm start'
if (-not $stamped) {
    Write-Host ''
    Write-Host '  The AppUserModelID could not be stamped on the shortcut. The app' -ForegroundColor Yellow
    Write-Host '  will still run; if desktop notifications never appear, that is the' -ForegroundColor Yellow
    Write-Host '  first thing to suspect. See desktop\README.md.' -ForegroundColor Yellow
}
Write-Host ''
