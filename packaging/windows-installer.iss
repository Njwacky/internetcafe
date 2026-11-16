; Build the portable app first using build-windows.bat, then compile with Inno Setup 6.
#define MyAppName "Cafe Desk"
#define MyAppVersion "0.8.0"
[Setup]
AppId={{4B36D389-A2D2-4A1C-9CC7-F2AF905552E7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\Cafe Desk
DefaultGroupName=Cafe Desk
PrivilegesRequired=lowest
OutputDir=..\installer-output
OutputBaseFilename=CafeDesk-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
[Files]
Source: "..\dist\CafeDesk\*"; DestDir: "{app}"; Excludes: "CafeDeskData\*,owner-keys\*,*admin_keygen*"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\Cafe Desk"; Filename: "{app}\CafeDesk.exe"
Name: "{autodesktop}\Cafe Desk"; Filename: "{app}\CafeDesk.exe"
[Run]
Filename: "{app}\CafeDesk.exe"; Description: "Launch Cafe Desk"; Flags: nowait postinstall skipifsilent
