; Inno Setup script for the Gateway PCC desktop agent.
; Compiled automatically by menu.cmd (Option 3) after PyInstaller builds the exe.
; Produces: installer\GatewayPCC-Setup.exe  — a one-click installer with a proper
; entry in "Add or remove programs" (install / uninstall / reinstall).

#define MyAppName "Gateway PCC"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Epicle Solutions"
#define MyAppURL "https://pcc.arithmed.com"
#define MyAppExeName "GatewayPCC.exe"

[Setup]
; A stable AppId is what lets Windows recognise upgrades / uninstalls. Keep it fixed.
AppId={{8F3A1C92-5D4E-4B7A-9C2F-1E6B0A7D3F45}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
OutputDir=installer
OutputBaseFilename=GatewayPCC-Setup
SetupIconFile=frontend\static\favicon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Per-user install so no admin rights are needed; user may switch to all-users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
