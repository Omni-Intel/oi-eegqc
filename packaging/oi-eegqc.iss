; Compile from the repo root:
;   ISCC /DAppVersion=0.3.3 packaging\oi-eegqc.iss
; Unsigned on purpose. Per-user install avoids an admin prompt.

#ifndef AppVersion
  #error AppVersion must be supplied by the build script
#endif

#define AppName "脑电质量评估"
#define AppPublisher "Omni-Intelligence"
#define AppExe "OI-EEGQC.exe"
#ifndef SourceDir
  #define SourceDir "..\dist\OI-EEGQC"
#endif
#ifndef OutputName
  #define OutputName "OI-EEGQC-Setup-Windows-x64"
#endif

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
OutputBaseFilename={#OutputName}
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
MinVersion=10.0

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "启动{#AppName}"; Flags: nowait postinstall skipifsilent; Check: not IsAppUpdate
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: IsAppUpdate

[Code]
function IsAppUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;
