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
#define MyAppVersion     "1.4.4"
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

[Icons]
Name: "{group}\{#MyAppName}";                    Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}";          Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";              Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}";              Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
; Offer to launch HeyTora after install
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    MsgBox(
      'HeyTora installed successfully!' #13#13
      'On first launch, HeyTora asks for a Google Gemini API key. It is free —' #13
      'get one at https://aistudio.google.com/app/apikey' #13#13
      'You can change it any time via Tray icon > Setup & Diagnostics >' #13
      'API Keys.',
      mbInformation, MB_OK
    );
end;
