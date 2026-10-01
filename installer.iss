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
#define MyAppVersion     "1.5.0"
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
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; Tasks: startupicon

[Run]
; Offer to launch HeyTora after install
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

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
