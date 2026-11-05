; SignalSetup.exe -- a bootstrapper, not a bundle. It ships only bootstrap.ps1
; and a checksum-verified nssm.exe; the app itself is cloned from GitHub so the
; in-app updater (app/updater.py, which needs a git checkout on main) keeps
; working. Built by .github/workflows/installer.yml:
;   iscc /DAppVersion=0.1.0 /DNssmExe=nssm.exe SignalSetup.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef NssmExe
  #define NssmExe "nssm.exe"
#endif

#define AppName "Signal Stock Dashboard"
#define PowerShell "{sys}\WindowsPowerShell\v1.0\powershell.exe"

[Setup]
AppId={{92EC61E4-43C3-4575-ABA8-D81FA9BF9343}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Minka1902
AppPublisherURL=https://github.com/Minka1902/stock-dashboard
; Program Files on purpose: this code runs as LocalSystem, and only
; administrators can write here. A folder under C:\ is modifiable by any
; authenticated user -- that would be a privilege escalation.
DefaultDirName={autopf}\SignalDashboard
DisableProgramGroupPage=yes
PrivilegesRequired=admin
; 64-bit mode so {sys} and the PowerShell it runs are 64-bit: a 32-bit
; PowerShell would read the WOW6432Node registry and Program Files (x86).
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; A fixed name keeps .../releases/latest/download/SignalSetup.exe stable.
OutputDir=Output
OutputBaseFilename=SignalSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
UninstallDisplayName={#AppName}
; install-desktop.ps1 puts the shortcut in the installing user's Start Menu.
UsedUserAreasWarning=no

[Files]
Source: "bootstrap.ps1"; DestDir: "{tmp}"
Source: "{#NssmExe}"; DestDir: "{tmp}"; DestName: "nssm.exe"

[Run]
; runasoriginaluser: the Electron window must not inherit the installer's
; elevation.
Filename: "{app}\repo\desktop\node_modules\electron\dist\electron.exe"; \
  Parameters: """{app}\repo\desktop"""; WorkingDir: "{app}\repo\desktop"; \
  Description: "Launch Signal"; Flags: postinstall nowait runasoriginaluser skipifsilent; \
  Check: BootstrapSucceeded

[UninstallRun]
; Electron and the service's python.exe both hold files under repo\ open.
Filename: "{#PowerShell}"; \
  Parameters: "-NoProfile -Command ""Get-Process electron -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like '{app}\repo\*' } | Stop-Process -Force"""; \
  Flags: runhidden waituntilterminated; RunOnceId: "StopDesktop"
; Removes the service only: the database and logs in ProgramData are kept.
Filename: "{#PowerShell}"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\repo\windows\uninstall-service.ps1"""; \
  Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\repo"
Type: files; Name: "{userprograms}\Signal.lnk"

[Code]
var
  Bootstrapped: Boolean;

function BootstrapSucceeded: Boolean;
begin
  Result := Bootstrapped;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Params: String;
  ResultCode: Integer;
begin
  if CurStep <> ssPostInstall then
    Exit;

  WizardForm.StatusLabel.Caption :=
    'Setting up Signal (prerequisites, dependencies, service). Progress is in the console window...';
  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\bootstrap.ps1') + '"' +
            ' -InstallDir "' + ExpandConstant('{app}') + '"' +
            ' -NssmExe "' + ExpandConstant('{tmp}\nssm.exe') + '"' +
            ' -PauseOnError';

  Bootstrapped := Exec(ExpandConstant('{#PowerShell}'), Params, '', SW_SHOW,
                       ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
  if not Bootstrapped then
    MsgBox('Signal setup did not finish (exit code ' + IntToStr(ResultCode) + ').' + #13#10 + #13#10 +
           'The log is at ' + ExpandConstant('{commonappdata}\SignalDashboard\logs\setup.log') + '.' + #13#10 +
           'Fix the reported problem and run SignalSetup.exe again; it picks up where it stopped.',
           mbError, MB_OK);
end;
