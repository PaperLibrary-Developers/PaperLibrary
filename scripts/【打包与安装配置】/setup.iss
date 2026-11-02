; Inno Setup script for 学术文献管理与图片检索系统
; 使用方法: 安装 Inno Setup (https://jrsoftware.org/isinfo.php)
;           运行 iscc setup.iss
; 输出: dist\PaperLibrary_Setup.exe

#define MyAppName "学术文献管理与图片检索系统"
#define MyAppNameEn "PaperLibrary"
#define MyAppVersion "1.0"
#define MyAppPublisher "PaperLibrary"
#define MyAppURL "http://localhost:8501"
#define MyAppExeName "PaperLibrary.bat"

[Setup]
AppId={{B8F4A3D2-7E6C-4A1B-9D5E-F2C8A3B6D4E1}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\PaperLibrary
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=dist
OutputBaseFilename=PaperLibrary_Setup_v{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; 以下行设置安装包语言为中文
; 需要对应的语言文件，默认英文也可用
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\PaperLibrary.bat
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"

[Files]
; 便携版主目录
Source: "dist\PaperLibrary_Portable\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 注意: 如果某个文件不存在 (如 icon.ico)，忽略即可

[Icons]
; 开始菜单
Name: "{group}\{#MyAppName}"; Filename: "{app}\PaperLibrary.bat"; WorkingDir: "{app}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
; 桌面
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\PaperLibrary.bat"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; 安装完成后询问是否启动
Filename: "{app}\PaperLibrary.bat"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent shellexec

[UninstallDelete]
; 卸载时询问是否删除用户数据
Type: filesandordirs; Name: "{app}\data"

[Code]
// 卸载前确认是否保留数据
function InitializeUninstall(): Boolean;
begin
  if MsgBox('是否同时删除文献数据库和提取的图片？' + #13#10 +
            '（选「否」将保留 data 文件夹）', mbConfirmation, MB_YESNO) = IDNO then
  begin
    // 用户选择保留数据
    RenameFile(ExpandConstant('{app}\data\papers.db'), ExpandConstant('{app}\data\papers.db.backup'));
  end;
  Result := True;
end;
