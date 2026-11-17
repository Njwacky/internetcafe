; Build the portable app first using build-windows.bat, then compile with Inno Setup 6.
; MyAppVersion defaults to the current release (see version.py). CI overrides it with
; the value from version.py:  iscc /DMyAppVersion=0.8.2 packaging\windows-installer.iss
#define MyAppName "Cafe Desk"
#ifndef MyAppVersion
#define MyAppVersion "0.8.2"
#endif
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
SetupIconFile=..\assets\icons\cafedesk.ico
UninstallDisplayIcon={app}\CafeDesk.exe
[Files]
Source: "..\dist\CafeDesk\*"; DestDir: "{app}"; Excludes: "CafeDeskData\*,owner-keys\*,*admin_keygen*"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\Cafe Desk"; Filename: "{app}\CafeDesk.exe"
Name: "{autodesktop}\Cafe Desk"; Filename: "{app}\CafeDesk.exe"
[Run]
Filename: "{app}\CafeDesk.exe"; Description: "Launch Cafe Desk"; Flags: nowait postinstall skipifsilent
