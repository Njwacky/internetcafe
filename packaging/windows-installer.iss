; Café Desk installer — Inno Setup 6.
;
; Build the portable app first using build-windows.bat, then compile with Inno Setup 6.
; MyAppVersion defaults to the current release (see version.py). CI overrides it with
; the value from version.py:  iscc /DMyAppVersion=0.8.3 packaging\windows-installer.iss
;
; Design intent: the installer should read like the first screen of the product, not a
; bare file-copy wizard. Before the customer clicks Install it answers the four things a
; counter clerk actually worries about — is this genuine, where do my files go, what
; happens to my data, and what do I do next. Wording stays calm on purpose: a red error
; box or a paragraph of security jargon is what makes people abandon a download.
#define MyAppName "Café Desk"
#ifndef MyAppVersion
#define MyAppVersion "0.8.3"
#endif
#define MyAppPublisher "Njwacko Studio"
#define MyAppExeName "CafeDesk.exe"
#define MyAppSupportWhatsApp "072 227 9505"

[Setup]
AppId={{4B36D389-A2D2-4A1C-9CC7-F2AF905552E7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
VersionInfoDescription={#MyAppName} Setup — 1-year per-computer licensed document studio
VersionInfoProductName={#MyAppName}
VersionInfoCompany={#MyAppPublisher}
DefaultDirName={autopf}\Café Desk
DefaultGroupName={#MyAppName}
; Per-user install: no UAC prompt and no administrator password at the counter.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\installer-output
OutputBaseFilename=CafeDesk-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=115
SetupIconFile=..\assets\icons\cafedesk.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
; Keep the customer's folder, prices and PIN on reinstall or upgrade.
UsePreviousAppDir=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Newer Café Desk releases are distributed as a fresh Setup.exe over the top of the old
; one; allow that without an uninstall/reinstall dance.
AllowNoIcons=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: checkedonce

[Files]
; admin_keygen.py and owner-keys/ must never travel to a customer PC.
Source: "..\dist\CafeDesk\*"; DestDir: "{app}"; Excludes: "CafeDeskData\*,owner-keys\*,*admin_keygen*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Café Desk document studio"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Comment: "Café Desk document studio"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Open Café Desk and activate this computer"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only installer-created notes; customer documents and CafeDeskData are never touched.
Type: files; Name: "{app}\HOW-TO-RUN-FROM-USB.txt"
Type: files; Name: "{app}\Enable-USB-Portable-Data.bat"

[Messages]
; The stock strings are exactly the ones customers screenshot when they are unsure.
WelcomeLabel1=Welcome to [name]
WelcomeLabel2=This will install [name/ver] on this computer.%n%nCafé Desk is the document studio for cafés and print shops — PDFs, CVs, scans, picture sheets, cards, forms and receipts in one window.%n%nNothing is uploaded. Every customer document stays on this PC.
SelectDirLabel3=Setup will install [name] into the following folder.
SelectDirDesc=Keep the default. Café Desk runs from here and needs no other software.
ReadyLabel1=Ready to install [name]
ReadyLabel2a=Click Install to begin. This usually takes under a minute, and no restart is needed.
FinishedHeadingLabel=You're set up — one step left
FinishedLabelNoIcons=[name] is installed.%n%nWhen it opens, it shows this computer's Machine ID (CD-XXXX-XXXX-XXXX) and asks for a 1-year activation key. Send that ID to the owner on WhatsApp {#MyAppSupportWhatsApp} to receive your key.
FinishedLabel=[name] is installed.%n%nWhen it opens, it shows this computer's Machine ID (CD-XXXX-XXXX-XXXX) and asks for a 1-year activation key. Send that ID to the owner on WhatsApp {#MyAppSupportWhatsApp} to receive your key.
ConfirmUninstall=Remove [name] from this computer?%n%nYour saved PDFs, pictures and receipts are not deleted. Saved prices, PIN, sales history and your 1-year licence stay in place unless you remove them yourself.
UninstalledAll=[name] was removed. Your documents and Café Desk data folders were left in place.
UninstalledMost=[name] was removed.%n%nSome files could not be deleted. Close Café Desk and remove the folder manually.
StatusUninstalling=Removing [name]...

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{4B36D389-A2D2-4A1C-9CC7-F2AF905552E7}_is1';

// A returning customer should never wonder whether this update wipes their work.
function ExistingInstallDir(): String;
begin
  Result := '';
  if not RegQueryStringValue(HKCU, UninstallKey, 'InstallLocation', Result) then
    RegQueryStringValue(HKLM, UninstallKey, 'InstallLocation', Result);
end;

procedure InitializeWizard();
var
  PreviousDir: String;
begin
  PreviousDir := ExistingInstallDir();
  if PreviousDir <> '' then
    WizardForm.WelcomeLabel2.Caption :=
      WizardForm.WelcomeLabel2.Caption + #13#10#13#10 +
      'An earlier Café Desk was found in:' + #13#10 + PreviousDir + #13#10 +
      'Its saved prices, PIN, sales history and activation stay exactly as they are — ' +
      'only the app files are refreshed.';
end;
