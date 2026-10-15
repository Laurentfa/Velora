; Installateur Windows (Inno Setup, gratuit). Compilation : iscc /DAppVersion=1.2.0 scripts\velora.iss  (après build_windows.bat)
; Installation pour l'utilisateur seul, dans son profil : aucun droit administrateur, donc les mises à jour automatiques sont silencieuses.
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
[Setup]
AppId={{6F0B7E7C-5B0A-4C8E-9C57-3D2A6F1E8A41}
AppName=Velora
AppVersion={#AppVersion}
AppPublisher=Velora
DefaultDirName={localappdata}\Programs\Velora
DefaultGroupName=Velora
PrivilegesRequired=lowest
OutputDir=..\dist\release
OutputBaseFilename=Velora-{#AppVersion}-setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\Velora.exe
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=yes
WizardStyle=modern
[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
[Files]
Source: "..\dist\Velora\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
[Icons]
Name: "{group}\Velora"; Filename: "{app}\Velora.exe"
Name: "{autodesktop}\Velora"; Filename: "{app}\Velora.exe"; Tasks: desktop
[Tasks]
Name: "desktop"; Description: "Créer un raccourci sur le Bureau"; Flags: unchecked
[Run]
Filename: "{app}\Velora.exe"; Description: "Lancer Velora"; Flags: nowait postinstall skipifsilent
