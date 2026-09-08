; Compile from the repo root:
;   ISCC /DAppVersion=0.3.0 packaging\oi-eegqc.iss
; Unsigned on purpose. Per-user install avoids an admin prompt.

#ifndef AppVersion
  #define AppVersion "0.3.0"
#endif

#define AppName "OI-EEGQC"
#define AppPublisher "Omni-Intelligence"
#define AppExe "OI-EEGQC.exe"

[Setup]
AppId={{8F3C2A91-4B6D-4E17-9C5A-1D8E6F0B2A44}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/Omni-Intel/oi-eegqc
AppSupportURL=https://github.com/Omni-Intel/oi-eegqc/releases
DefaultDirName={localappdata}\Omni-Intelligence\EEGQC
DefaultGroupName={#AppPublisher}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile="..\assets\omni-intelli logo\OMNI_LOGO_100x100.ico"
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist
OutputBaseFilename=OI-EEGQC-Setup-Windows-x64
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "..\dist\OI-EEGQC\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
