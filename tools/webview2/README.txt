WebView2 SDK（启动器内置窗口用）
================================

来源：NuGet 包 `Microsoft.Web.WebView2` 版本 1.0.4314.35
（https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.4314.35）

本目录只放启动器编译/运行需要的 3 个文件，均取自该 NuGet 包：

- Microsoft.Web.WebView2.Core.dll       lib\net462\
- Microsoft.Web.WebView2.WinForms.dll   lib\net462\
- WebView2Loader.dll                    runtimes\win-x64\native\（x64）
- LICENSE-WebView2SDK.txt / NOTICE-WebView2SDK.txt  原包的许可与第三方声明

用途：`tools/make_launcher.ps1` 用 csc 编译 `PaperLibrary.exe` 时引用前两个
托管程序集，并把这三个 DLL 复制到发布包根目录（与 exe 同级）。

运行前提：目标机器装有 Microsoft Edge WebView2 Runtime（Windows 11 自带，
Windows 10 装了新版 Edge 一般也有；缺失时启动器会提示并允许改用浏览器打开）。

注意：这是构建/运行依赖，不是 Python 依赖，不进入 runtime\portable。
升级 SDK 时替换本目录文件并同步更新本说明与 make_launcher.ps1 里的版本行。
