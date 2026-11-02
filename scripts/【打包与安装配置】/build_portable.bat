@echo off
chcp 65001 >nul
title 📦 构建文献检索系统 — 便携版

echo.
echo   ╔══════════════════════════════════════════════╗
echo   ║  📦 构建便携版 — 免安装、即开即用           ║
echo   ╚══════════════════════════════════════════════╝
echo.

set "PYTHON_DIR=C:\Program Files\Python311"
set "PATH=%PYTHON_DIR%;%PYTHON_DIR%\Scripts;%APPDATA%\Python\Python311\Scripts;%PATH%"
set "DIST=dist\PaperLibrary_Portable"
cd /d "%~dp0"

:: Step 1: Create output directory
echo   [1/5] 创建输出目录...
if exist "%DIST%" rmdir /s /q "%DIST%"
mkdir "%DIST%\app"
mkdir "%DIST%\data"
mkdir "%DIST%\python"
echo         ✅

:: Step 2: Copy app source files
echo   [2/5] 复制程序文件...
copy app.py "%DIST%\app\" >nul
copy database.py "%DIST%\app\" >nul
copy parser.py "%DIST%\app\" >nul
copy ai_engine.py "%DIST%\app\" >nul
copy launcher_gui.py "%DIST%\app\" >nul
copy requirements.txt "%DIST%\app\" >nul
echo         ✅

:: Step 3: Copy embedded Python
echo   [3/5] 复制 Python 运行环境...
xcopy /E /I /Q "%PYTHON_DIR%" "%DIST%\python\"
echo         ✅

:: Step 4: Install packages into portable Python
echo   [4/5] 安装依赖包到便携目录...
"%DIST%\python\python.exe" -m pip install -r requirements.txt --target "%DIST%\app\site-packages" --no-user --quiet 2>&1
echo         ✅

:: Step 5: Create launcher
echo   [5/5] 创建启动器...
(
echo @echo off
echo cd /d "%%~dp0"
echo start "" "%%~dp0python\pythonw.exe" "%%~dp0app\launcher_gui.py"
) > "%DIST%\PaperLibrary.bat"

:: Create a VBS launcher (no console flash)
(
echo Set WshShell = CreateObject^("WScript.Shell"^)
echo WshShell.Run """%~dp0%DIST%\python\pythonw.exe"" ""%~dp0%DIST%\app\launcher_gui.py""", 0, False
) > "%DIST%\PaperLibrary.vbs"

:: Count total size
for /f %%i in ('powershell -command "(Get-ChildItem -Recurse '%DIST%' | Measure-Object -Property Length -Sum).Sum / 1MB"') do set SIZE_MB=%%i

echo.
echo   ╔══════════════════════════════════════════════╗
echo   ║  ✅ 便携版构建成功！                        ║
echo   ╠══════════════════════════════════════════════╣
echo   ║  输出: %DIST%
echo   ║  体积: %SIZE_MB% MB
echo   ║  启动: 双击 PaperLibrary.bat 或 .vbs
echo   ╠══════════════════════════════════════════════╣
echo   ║  发给别人：直接压缩此文件夹发送即可         ║
echo   ║  对方无需安装 Python，解压即用              ║
echo   ╚══════════════════════════════════════════════╝
echo.
echo   💡 如需进一步制作为 Setup.exe 安装包:
echo      安装 Inno Setup 后运行: iscc setup.iss
echo.
pause
