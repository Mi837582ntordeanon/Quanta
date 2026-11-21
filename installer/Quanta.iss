#define AppName "Q.W.A.N.T.A."
#define AppExe "Quanta.exe"
#define AppVersion "0.1.0"
#define AppPublisher "Quanta Project"
#define DistDir "..\dist\Quanta"

[Setup]
AppId={{6B3A70DD-4EB2-4C1C-A7E6-51620D988F78}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Quanta
DefaultGroupName=Quanta
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=..\assets\quanta.ico
OutputDir=..\dist
OutputBaseFilename=Quanta-Setup
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
DisableProgramGroupPage=yes
CloseApplications=no
Uninstallable=yes
VersionInfoVersion=0.1.0.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=Local AI companion for Windows

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Quanta"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{autodesktop}\Quanta"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Запустить Кванту"; Flags: nowait postinstall skipifsilent
