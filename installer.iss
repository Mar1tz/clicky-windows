; ────────────────────────────────────────────────────────────────────
;  HeyTora for Windows — Inno Setup script
;
;  Builds a single Setup-HeyTora.exe from the PyInstaller dist folder.
;
;  Prerequisites:
;    1. Run  build.bat  first (produces dist\HeyTora\)
;    2. Install Inno Setup 6 from https://jrsoftware.org/isdl.php
;    3. Run:  iscc installer.iss
;
;  Output:  dist\Setup-HeyTora.exe   (single-file installer, ~200-400 MB)
; ────────────────────────────────────────────────────────────────────

#define MyAppName        "HeyTora"
#define MyAppVersion     "1.7.14"
#define MyAppPublisher   "HeyTora"
#define MyAppExeName     "HeyTora.exe"

[Setup]
AppId={{9A4E3F2C-7B1D-4A8F-9C6E-3D7F1B5E9A0C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; No licence-acceptance page: HeyTora is MIT, free for anyone to run.
; The MIT notice still ships with the app (see [Files]) as MIT requires.
OutputDir=dist
OutputBaseFilename=Setup-HeyTora
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
; Shown in Setup-HeyTora.exe's Properties > Details, like any real installer.
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}
; An upgrade over a running HeyTora closes it first, instead of failing on
; files in use.
CloseApplications=yes
RestartApplications=no
; Held while this installer runs: a HeyTora started meanwhile sees it, says
; it is updating and steps aside, rather than holding files this installer
; has to replace (updates/service.installing_now). Found 4 Oct 2026.
SetupMutex=HeyToraSetup
#if FileExists("assets\icon.ico")
  SetupIconFile=assets\icon.ico
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";  Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "startupicon";  Description: "Launch HeyTora when Windows &starts";  GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; Everything PyInstaller produced
Source: "dist\HeyTora\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; MIT requires the notice travel with the software.
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; Installing over an older "Tora" (the same AppId, so this is an upgrade):
; its exe and shortcuts would otherwise stay beside HeyTora's - and the
; startup one would go on launching a second copy at every sign-in.
Type: files; Name: "{app}\Tora.exe"
Type: files; Name: "{group}\Tora.lnk"
Type: files; Name: "{group}\Uninstall Tora.lnk"
Type: files; Name: "{autodesktop}\Tora.lnk"
Type: files; Name: "{userstartup}\Tora.lnk"
; 1.4.x put "launch at sign-in" in the Startup folder; it is now the same
; registry entry the app's own switch uses (below). Left there, the old
; shortcut would start a second copy beside it.
Type: files; Name: "{userstartup}\{#MyAppName}.lnk"

[Icons]
Name: "{group}\{#MyAppName}";                    Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}";          Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";              Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; "Launch when Windows starts" is the SAME entry Settings > "Start HeyTora
; when Windows starts" reads and writes (ui/autostart.py): the value
; "HeyTora" under HKCU\...\Run holding the quoted exe path. One switch, one
; entry - a Startup shortcut beside it meant two copies at every sign-in.
; Uninstalling removes it (in [Code]) - but only if it starts THIS copy: a
; developer's "start with Windows" for HeyTora run from source is the same
; value name, and an uninstall must not switch that off.
; An update the app installs over itself leaves it alone: the user may have
; switched it off in Settings since, and the task remembered from the first
; install would switch it back on (found 2 Oct 2026).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: startupicon; Check: NotUpdate
; heytora:// - how the browser hands back to the app once sign-in finishes
; (heytora-web /signed-in opens heytora://signed-in). Windows starts
; HeyTora.exe with the link; the copy already running hears it through
; ui/single_instance.py and comes to the front, and the new copy goes.
Root: HKCU; Subkey: "Software\Classes\heytora"; ValueType: string; ValueName: ""; ValueData: "URL:HeyTora"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\heytora"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\heytora\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"",0"
Root: HKCU; Subkey: "Software\Classes\heytora\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[UninstallDelete]
; Uninstalling signs out and forgets the first-launch answers, so a
; reinstall starts at the first launch and its sign-in (ui/sign_in_gate.py).
; Memory, history and the user's own key (.env) in the same folder are kept.
Type: files; Name: "{localappdata}\HeyTora\account.bin"
Type: files; Name: "{localappdata}\HeyTora\setup_complete.flag"

[Run]
; Offer to launch HeyTora after install
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
; An update the app installed over itself (updates/service.py: /VERYSILENT
; /UPDATE=1) opens HeyTora again - unless it was installed as HeyTora was
; being quit (/RELAUNCH=0).
Filename: "{app}\{#MyAppExeName}"; Parameters: "--updated"; Flags: nowait skipifnotsilent; Check: ShouldRelaunch

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

function InitializeSetup(): Boolean;
begin
  // The app starts this installer and then closes itself: give it a moment
  // to let go of its files (CloseApplications catches anything left).
  if ExpandConstant('{param:UPDATE|0}') = '1' then
    Sleep(2500);
  Result := True;
end;

function ShouldRelaunch(): Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|1}') <> '0';
end;

function NotUpdate(): Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') <> '1';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Cmd: String;
begin
  // Stop starting with Windows - turned on here or in the app's Settings -
  // when the entry is this installed copy's, and leave any other alone.
  if CurUninstallStep = usPostUninstall then
    if RegQueryStringValue(HKCU, RunKey, '{#MyAppName}', Cmd) then
      if CompareText(Cmd, '"' + ExpandConstant('{app}\{#MyAppExeName}') + '"') = 0 then
        RegDeleteValue(HKCU, RunKey, '{#MyAppName}');
end;

[Messages]
; The first-run tip on the last page, where a real installer says it -
; rather than a pop-up box to click away before that page.
FinishedLabel=Setup has installed [name] on your computer.%n%nOn first launch HeyTora asks for a Google Gemini API key - free at aistudio.google.com/app/apikey (change it later from the tray icon's menu: API Keys).%n%nThen hold Ctrl + Win and talk.
